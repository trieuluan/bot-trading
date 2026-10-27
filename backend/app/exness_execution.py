"""Opt-in DEMO broker execution for regime_router.

Strictly demo only. Configurable position and risk caps; split TP1/runner positions. Native broker SL/TP.
Deals synced from Exness are the ground truth for chart markers and trade history.
"""

import csv
import fcntl
import hashlib
import io
import json
import os
import time
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from urllib.request import urlopen

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, StrictBool
from sqlalchemy import select, text

from .db import (
    ExnessDeal,
    ExnessExecutionState,
    ExnessExecutionTrade,
    Session,
    WorkspaceFeedState,
    engine,
)
from .exness import ExnessError
from .exness_demo_client import ExnessDemoClient, positive_decimal, validate_quote
from .exness_feed import FRAMES, WORKSPACE
from .exness_management import (
    closed_minutes,
    exit_reason,
    protective_stop,
    risk_budget,
    split_bodies,
)
from .exness_observation import audit_frames, load_frames
from .exness_sessions import session_closed_ahead
from .forex_exits import DEFAULTS as ADAPTIVE_EXITS
from .forex_htf_v2 import execution_candidates, validate_execution_signal
from .forex_learning import cached_summary as learning_summary
from .forex_notifications import notify_reconciled, stop_exit_reason
from .forex_symbols import configured_symbols, request_symbol

router = APIRouter(prefix="/api/workspaces/exness_forex/execution")
PENDING = ("planned", "submitting", "submitted", "uncertain", "closing")
ACTIVE = ("submitting", "submitted", "uncertain", "open", "closing")


def initial_stop_of(data):
    return data.get("body", {}).get("stop_loss_price")

def enabled():
    return (os.getenv("EXNESS_DEMO_EXECUTION_ENABLED") == "true"
            and os.getenv("EXNESS_ENVIRONMENT") == "demo"
            and bool(configured_symbols()))


def max_positions():
    try:
        return min(66, max(1, int(os.getenv("EXNESS_DEMO_MAX_POSITIONS", "1"))))
    except ValueError:
        return 1

def max_positions_per_symbol():
    try:
        return min(6, max(1, int(os.getenv("EXNESS_DEMO_MAX_POSITIONS_PER_SYMBOL", "6"))))
    except ValueError:
        return 1

def capacity_available(broker, account, symbol, additional=0, *, entry_intent_id=None):
    positions = broker["positions"]
    live_ids = {position["position_id"] for position in positions}
    occupied = len(positions)
    local = sum(position.get("instrument") == symbol for position in positions)
    if any(order.get("order_type") != "sltp" for order in broker["orders"]):
        return False
    with Session() as session:
        intents = session.scalars(select(ExnessExecutionTrade).where(ExnessExecutionTrade.account == account)).all()
        from .db import ExnessEntryIntent
        from .forex_entry_intents import RESERVED
        pending_intents = session.scalars(select(ExnessEntryIntent).where(
            ExnessEntryIntent.account == account, ExnessEntryIntent.status.in_(RESERVED))).all()
    for intent in intents:
        for _, leg in leg_records(intent.snapshot):
            if leg.get("status") in (*ACTIVE, "planned") and leg.get("position_id") not in live_ids:
                occupied += 1
                local += intent.symbol == symbol
    for entry_intent in pending_intents:
        if entry_intent.id == entry_intent_id or any(
                intent.snapshot.get("signal", {}).get("entry_intent_id") == entry_intent.id for intent in intents):
            continue
        legs_count = entry_intent.snapshot.get("reserved_legs", 1)
        occupied += legs_count
        local += (entry_intent.symbol == symbol) * legs_count
    return occupied + additional <= max_positions() and local + additional <= max_positions_per_symbol()

def busy(broker):
    pending = [order for order in broker["orders"] if order.get("order_type") != "sltp"]
    return pending or len(broker["positions"]) >= max_positions()

SIGNAL_WINDOW = 300
LATE_AFTER = 120
MAX_LATE_DRIFT_R = 0.25

def late_drift_ok(signal, entry, now):
    """Late retry only while price stays within 0.25R of the closed signal candle."""
    if signal.get("entry_intent_id"):
        from .db import ExnessEntryIntent
        with Session() as session:
            intent = session.get(ExnessEntryIntent, signal["entry_intent_id"])
        if not intent or not -10 <= now - intent.snapshot.get("last_quote", {}).get("received_at", 0) <= 2:
            raise ExnessError("virtual_entry_expired")
        risk = abs(float(signal["ref_price"]) - float(signal["stop"]))
        if risk <= 0 or abs(float(entry) - float(signal["ref_price"])) > MAX_LATE_DRIFT_R * risk:
            raise ExnessError("price_drift: virtual entry moved beyond 0.25R")
        return
    age = now - signal["available_at"]
    if not 0 <= age <= SIGNAL_WINDOW:
        raise ExnessError("signal_expired: outside 300s entry window")
    if age <= LATE_AFTER:
        return
    ref = signal.get("ref_price")
    if ref is None:
        raise ExnessError("price_drift: no reference price for late entry")
    ref, stop, entry = map(positive_decimal, (ref, signal["stop"], entry))
    if ref == stop:
        raise ExnessError("price_drift: invalid reference risk for late entry")
    drift = abs(entry - ref) / abs(ref - stop)
    if drift > MAX_LATE_DRIFT_R:
        raise ExnessError(f"price_drift: {drift:.2f}R from signal close after {now - signal['available_at']}s; skipped")

def utc(stamp):
    return datetime.fromtimestamp(stamp, timezone.utc).isoformat()


def live_quote(symbol="XAUUSD"):
    from .app_auth import quote_request
    deadline = time.monotonic() + 5
    with urlopen(quote_request(symbol), timeout=5) as stream:
        while time.monotonic() < deadline:
            line = stream.readline()
            if not line:
                break
            if line.startswith(b"data: "):
                data = json.loads(line[6:])
                if data.get("symbol") == symbol and data.get("status") == "live":
                    return validate_quote(data.get("quote") or {})
    raise ExnessError(f"Fresh {symbol} quote unavailable")


def history(api, kind, start, end):
    params = {"from": utc(start), "to": utc(end), "limit": 200}
    for _ in range(20):
        page = api.get(f"/v1/history/accounts/{api.account_id}/{kind}", params)
        yield from page["items"]
        if not page.get("has_more"):
            return
        cursor = page.get("next_cursor")
        if not cursor:
            raise ExnessError("Broker history incomplete: missing cursor")
        params = {"cursor": cursor, "limit": 200}
    raise ExnessError("Broker history pagination incomplete; no new orders")


def sync_deals(api, now):
    with Session.begin() as session:
        state = session.get(ExnessExecutionState, api.account_id)
        start = state.snapshot.get("history_until", state.started_at - 86400)
        if start >= now:
            return
        end = min(now, start + 86400)
        for item in history(api, "deals", max(1, start - 1), end):
            deal = item["deal"]
            identifier = str(deal["deal_id"])
            if session.get(ExnessDeal, (api.account_id, identifier)):
                continue
            stamp = int(datetime.fromisoformat(item["event_time"].replace("Z", "+00:00")).timestamp())
            session.add(ExnessDeal(account=api.account_id, deal_id=identifier,
                position_id=deal.get("position_id"), event_time=stamp,
                snapshot={**deal, "event_time": stamp}))
        state.snapshot = {**state.snapshot, "history_until": end, "history_synced_at": now}


def persist(account, stamp, values, symbol="XAUUSD"):
    with Session.begin() as session:
        row = session.get(ExnessExecutionTrade, (account, symbol, stamp))
        row.snapshot = {**row.snapshot, **values}


def leg_records(data):
    return list(enumerate(data["legs"])) if data.get("legs") else [(None, data)]

def persist_leg(account, stamp, index, values, symbol="XAUUSD"):
    with Session.begin() as session:
        row = session.get(ExnessExecutionTrade, (account, symbol, stamp))
        update_leg(row, index, values)

def persist_symbol_leg(account, symbol, stamp, index, values):
    persist_leg(account, stamp, index, values, symbol)

def update_leg(row, index, values):
    if index is None:
        row.snapshot = {**row.snapshot, **values}
        return
    data = row.snapshot
    legs = [dict(leg) for leg in data["legs"]]
    legs[index] = {**legs[index], **values}
    states = {leg["status"] for leg in legs}
    status = next((value for value in ("uncertain", "submitting", "submitted", "closing", "planned", "open", "closed")
                   if value in states), "skipped")
    row.snapshot = {**data, "legs": legs, "status": status}

def operation_result(api, operation):
    try:
        return api.get(f"/v1/trading/accounts/{api.account_id}/operations/{operation}")
    except ExnessError as exc:
        if str(exc) != "Exness HTTP 404":  # expired operation id: fall back to order history
            raise
        return {}

def beat(account, now):
    with Session.begin() as session:
        state = session.get(ExnessExecutionState, account)
        if state is None:
            state = ExnessExecutionState(account=account, enabled=True, started_at=now,
                                         snapshot={"history_from": now - 86400})
            session.add(state)
        state.snapshot = {**state.snapshot, "heartbeat": now}

def reconcile(api, broker, now):
    with Session() as session:
        trades = session.scalars(select(ExnessExecutionTrade).where(
            ExnessExecutionTrade.account == api.account_id)).all()
        deals = session.scalars(select(ExnessDeal).where(ExnessDeal.account == api.account_id)).all()
    open_ids = {position["position_id"] for position in broker["positions"]}
    for row in trades:
        for index, data in leg_records(row.snapshot):
            if data["status"] not in ACTIVE:
                continue
            def save(values):
                with Session.begin() as session:
                    current = session.get(ExnessExecutionTrade, (api.account_id, row.symbol, row.signal_time))
                    previous = current.snapshot["legs"][index] if index is not None else current.snapshot
                    updated = {**previous, **values}
                    position = next((item for item in broker["positions"]
                                     if item["position_id"] == updated.get("position_id")), None)
                    protection = next((item for item in broker["orders"]
                                       if item.get("position_id") == updated.get("position_id")
                                       and item.get("order_type") == "sltp"), None)
                    notify_reconciled(session, current, previous, updated, position, protection, deals, now)
                    update_leg(current, index, updated)
            pid = data.get("position_id")
            if not pid:
                operation = data.get("ack", {}).get("operation_id")
                if operation:
                    result = operation_result(api, operation)
                    if result.get("status") in ("rejected", "failed"):
                        save({"status": "rejected", "error": result.get("error_message", "Broker rejected")})
                        continue
                start = data["recorded_at"] - 5
                orders = list(history(api, "orders", start, min(now, start + 86400)))
                matching = [item["order"] for item in orders
                            if item["order"].get("comment") == data["key"][:32]
                            and item["order"].get("instrument") == row.symbol]
                if not matching:
                    body = data.get("body", {})
                    submitted = data.get("submitted_at", data["recorded_at"])
                    candidates = []
                    for item in orders:
                        order = item["order"]
                        if (order.get("comment") or order.get("instrument") != body.get("instrument")
                                or order.get("direction") != body.get("side")
                                or order.get("order_type") != "market" or not order.get("create_time")):
                            continue
                        created = datetime.fromisoformat(order["create_time"].replace("Z", "+00:00")).timestamp()
                        if submitted - 5 <= created <= submitted + 120 and all(
                            body.get(field) is not None and order.get(field) is not None
                            and Decimal(str(order[field])) == Decimal(str(body[field]))
                            for field in ("volume", "stop_loss_price", "take_profit_price")
                        ):
                            candidates.append(order)
                    if len(candidates) == 1:
                        matching = candidates
                for order in matching:
                    if order.get("state") == "rejected":
                        save({"status": "rejected"})
                    if order.get("state") == "filled" and order.get("position_id"):
                        pid = order["position_id"]
                        break
                    filled = next((deal for deal in deals if deal.snapshot.get("order_id") == order["order_id"]
                                   and deal.snapshot.get("type") == "open"), None)
                    if filled:
                        pid = filled.position_id
                        break
            if not pid:
                continue
            has_close = any(deal.position_id == pid and deal.snapshot.get("type") == "close" for deal in deals)
            status = "open" if pid in open_ids else "closed" if has_close else "uncertain"
            if data["status"] == "closing" and pid in open_ids:
                operation = data.get("close_ack", {}).get("operation_id")
                if operation:
                    result = operation_result(api, operation)
                    if result.get("status") in ("failed", "rejected"):
                        save({"error": "Close rejected; reconcile before a new close request"})
                status = "closing"
            management = data.get("management", {})
            if management.get("status") in ("submitting", "submitted"):
                stop = next((order.get("stop_loss_price") for order in broker["orders"]
                             if order.get("position_id") == pid and order.get("order_type") == "sltp"), None)
                requested = Decimal(management["stop"])
                direction = next((position.get("direction") for position in broker["positions"]
                                  if position["position_id"] == pid), None)
                confirmed = stop and (Decimal(stop) >= requested if direction == "buy" else Decimal(stop) <= requested)
                if confirmed:
                    management = {**management, "status": "confirmed"}
                elif management.get("ack", {}).get("operation_id"):
                    operation = management["ack"]["operation_id"]
                    result = operation_result(api, operation)
                    if result.get("status") in ("failed", "rejected"):
                        management = {**management, "status": "failed", "error": result.get("error_message", "SL update rejected")}
            exits = [deal.snapshot for deal in deals if deal.position_id == pid and deal.snapshot.get("type") == "close"]
            target = data.get("body", {}).get("take_profit_price")
            tp_filled = bool(status == "closed" and exits and target and not data.get("close_reason") and (
                Decimal(exits[-1]["price"]) >= Decimal(target) if data.get("body", {}).get("side") == "buy" else
                Decimal(exits[-1]["price"]) <= Decimal(target)))
            sl_reason = (stop_exit_reason(data.get("body", {}).get("side"), exits[-1].get("price"),
                                          data.get("broker_stop") or data.get("body", {}).get("stop_loss_price"),
                                          next((d.snapshot.get("price") for d in deals if d.position_id == pid
                                                and d.snapshot.get("type") == "open"), None))
                         if status == "closed" and exits and not tp_filled and not data.get("close_reason") else None)
            save({"position_id": pid, "status": status, "take_profit_filled": tp_filled,
                  **({"close_reason": sl_reason} if sl_reason else {}),
                  **({"management": management} if management else {})})

def evaluate(session, now, symbol="XAUUSD"):
    detail = session.info["execution_evaluation"] = {
        "symbol": symbol, "candle_time": now // 900 * 900 - 900, "evaluated": False}
    frames = load_frames(session, now, symbol)
    feeds = [session.get(WorkspaceFeedState, (WORKSPACE, symbol, frame)) for frame in FRAMES]
    if audit_frames(frames, symbol)["status"] == "blocked" or any(
        not state or state.error or state.checked_until is None
        or state.checked_until < now // FRAMES[frame][1] * FRAMES[frame][1]
        for frame, state in zip(FRAMES, feeds)
    ):
        detail["why"] = "feed_not_ready"
        return None
    minutes = [row for row in frames["15m"] if row.price_type == "bid"]
    if not minutes or not 0 <= now - (minutes[-1].time + 900) <= SIGNAL_WINDOW:
        detail["why"] = "outside_signal_window"
        return None
    candle = minutes[-1]
    ask = next((row for row in frames["15m"] if row.price_type == "ask" and row.time == candle.time), None)
    if ask is None:
        detail["why"] = "ask_candle_missing"
        return None
    account = os.getenv("EXNESS_ACCOUNT_ID", "").strip()
    try:
        candidates = execution_candidates(account, now, symbol)
    except (OSError, ValueError, KeyError, TypeError):
        detail["why"] = "v2_ledger_unavailable"
        return None
    detail.update(candle_time=candle.time, evaluated=True, mode="htf_v2",
                  confirmed=[{"strategy": signal["strategy"], "side": signal["side"], "shadow": False}
                             for signal, _ in candidates],
                  strategies=[{"strategy": signal["strategy"], "status": "confirmed", "reason": None}
                              for signal, _ in candidates])
    selected = candidates[0][0] if candidates else None
    detail["selected"] = selected["strategy"] if selected else None
    if selected:
        selected["ref_price"] = ask.close if selected["side"] == "long" else candle.close
    return selected

def record_journal(state, detail, outcome, now):
    """One row per closed M15: what the learner saw and why the executor did or did not trade."""
    stamp = detail.get("candle_time")
    if stamp is None:
        return
    rows = state.snapshot.get("journal", [])
    symbol = detail.get("symbol", "XAUUSD")
    old = next((row for row in rows if row["time"] == stamp and row.get("symbol", "XAUUSD") == symbol), None)
    if old and old["evaluated"] and not detail.get("evaluated"):
        detail = {**old, **detail, "evaluated": True, "candle_time": stamp,
                  "reason": detail.get("reason")}
    entry = {"symbol": symbol, "time": stamp, "at": now, "evaluated": bool(detail.get("evaluated")), "outcome": outcome,
             "mode": detail.get("mode"), "selected": detail.get("selected"), "confirmed": detail.get("confirmed", []),
             "strategies": detail.get("strategies", []), "reason": detail.get("reason"),
             "events": (old or {}).get("events", [])}
    if not old or old["outcome"] != outcome:
        entry["events"] = (entry["events"] + [{"at": now, "outcome": outcome}])[-16:]
    if old and ((old["outcome"] in ("order_sent", "execution_error")
                 and outcome not in ("order_sent", "execution_error"))
                or (old["outcome"] == "order_planned" and outcome not in ("order_sent", "execution_error", "api_rate_limited"))
                or (old["evaluated"] and outcome.startswith("unavailable:"))
                or (old["outcome"] in ("api_rate_limited", "worker_error") and outcome.startswith("unavailable:"))):
        entry["outcome"] = old["outcome"]
        entry["reason"] = old.get("reason")
    rows = sorted([row for row in rows if (row["time"], row.get("symbol", "XAUUSD")) != (stamp, symbol)] + [entry], key=lambda row: row["time"])[-192:]
    state.snapshot = {**state.snapshot, "journal": rows}

def tick_outcome(reason, signal, detail):
    if reason:
        return "blocked:" + reason
    if signal:
        return "signal"
    if not detail.get("evaluated"):
        return "unavailable:" + detail.get("why", "unknown")
    confirmed = detail.get("confirmed", [])
    return "no_signal" if not confirmed else "shadow_only" if all(row["shadow"] for row in confirmed) else "not_selected"

def funnel(rows):
    evaluated = [row for row in rows if row["evaluated"]]
    return {"candles": len(rows), "evaluated": len(evaluated),
            "with_signal": sum(bool(row["confirmed"]) for row in evaluated),
            "selected": sum(row["selected"] is not None for row in evaluated),
            "orders": sum(row["outcome"] == "order_sent" for row in rows),
            "outcomes": dict(Counter(row["outcome"] for row in rows)),
            "since": min((row["at"] for row in rows), default=None),
            "by_strategy": dict(Counter(item["strategy"] for row in evaluated for item in row["confirmed"]).most_common())}

def execution_tick(api, now=None, entry_request=None):
    if not enabled():
        return "disabled"
    fingerprint = hashlib.sha256(api.account_id.encode()).hexdigest()[:16]
    directory = Path("research-output")
    directory.mkdir(exist_ok=True)
    with (directory / f"exness-probe-{fingerprint}.lock").open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return "calibration_or_executor_active"
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            locked = connection.dialect.name != "postgresql" or connection.scalar(
                text("SELECT pg_try_advisory_lock(hashtext(:name))"), {"name": "demo-" + api.account_id})
            if not locked:
                return "executor_active"
            try:
                if entry_request:
                    observed_at = time.time() if now is None else now
                    client = ExnessDemoClient(api)
                    client.verify_demo()
                    broker = client.snapshot()
                    sync_deals(api, observed_at)
                    reconcile(api, broker, observed_at)
                    return _symbol_execution_tick(api, broker, observed_at,
                                                  entry_request[1], entry_intent_id=entry_request[0])
                result = _execution_tick(api, now)
                observed_at = int(time.time()) if now is None else now
                with Session.begin() as session:
                    state = session.get(ExnessExecutionState, api.account_id)
                    stamp = observed_at // 900 * 900 - 900
                    for symbol in configured_symbols():
                        if state and not any(row["time"] == stamp and row.get("symbol", "XAUUSD") == symbol
                                             for row in state.snapshot.get("journal", [])):
                            signal = evaluate(session, observed_at, symbol)
                            detail = {**session.info.get("execution_evaluation", {"candle_time": stamp}),
                                      "symbol": symbol}
                            reason = result.get(symbol) if isinstance(result, dict) else result
                            if reason == "waiting_signal":
                                reason = None
                            record_journal(state, detail, tick_outcome(reason, signal, detail), observed_at)
                return result
            finally:
                if connection.dialect.name == "postgresql":
                    connection.execute(text("SELECT pg_advisory_unlock(hashtext(:name))"),
                                       {"name": "demo-" + api.account_id})


def _execution_tick(api, now=None):
    now = int(time.time()) if now is None else now
    if not enabled():
        return "disabled"
    beat(api.account_id, now)  # worker alive even if broker API is rate limited
    client = ExnessDemoClient(api)
    client.verify_demo()
    broker = client.snapshot()
    with Session.begin() as session:
        state = session.get(ExnessExecutionState, api.account_id)
        if state is None:
            state = ExnessExecutionState(account=api.account_id, enabled=True, started_at=now,
                                         snapshot={"history_from": now - 86400})
            session.add(state)
        state.snapshot = {**state.snapshot, "heartbeat": now, "broker": broker, "error": None,
                          "demo_verified": True, "stop_management_available": getattr(client, "can_modify", False)}
    sync_deals(api, now)
    reconcile(api, broker, now)
    from .forex_entry_intents import cancel_waiting, pilot_enabled, reconcile_intents
    if not pilot_enabled():
        with Session.begin() as session:
            cancel_waiting(session, api.account_id, "pilot_disabled", now)
    reconcile_intents(api.account_id, now)
    with Session() as session:
        managed = session.scalars(select(ExnessExecutionTrade.symbol).where(
            ExnessExecutionTrade.account == api.account_id,
        ).distinct()).all()
    results = {}
    for symbol in dict.fromkeys((*configured_symbols(), *managed)):
        try:
            results[symbol] = _symbol_execution_tick(api, broker, now, symbol)
        except Exception as exc:
            results[symbol] = type(exc).__name__ + ": " + str(exc)[:160]
            record_error(api.account_id, exc, now, symbol=symbol)
            if getattr(exc, "rate_limited", False) or getattr(exc, "account_blocked", False) or len(configured_symbols()) == 1:
                raise
    return next(iter(results.values())) if len(results) == 1 else results


def _symbol_execution_tick(api, broker, now, symbol, *, entry_intent_id=None):
    from .forex_conversion import cost_context
    from .forex_costs import spec_summary
    client = ExnessDemoClient(api)
    condition = client.prepare(symbol, require_flat=False)
    with Session() as session:
        specs = spec_summary(session, now, symbol)
        profile = specs.get("profile") if specs.get("status") != "stale" else None
    costs_ready = profile is not None and 0 <= now - profile["observed_at"] < 3600
    if costs_ready:
        try:
            condition = cost_context({**condition, **profile}, now, account=api.account_id, symbol=symbol)
            costs_ready = condition.get("commission_per_unit") is not None
        except ValueError:
            costs_ready = False
    near_close = session_closed_ahead(now // 900 * 900, 2, symbol=symbol)
    with Session() as session:
        own = session.scalars(select(ExnessExecutionTrade).where(
            ExnessExecutionTrade.account == api.account_id, ExnessExecutionTrade.symbol == symbol)).all()
    with Session() as session:
        frames = load_frames(session, now, symbol)
    minutes = closed_minutes(frames, now)
    for row in own:
        if row.snapshot.get("signal", {}).get("entry_intent_id"):
            for index, data in leg_records(row.snapshot):
                if data["status"] == "planned":
                    persist_symbol_leg(api.account_id, symbol, row.signal_time, index,
                                       {"status": "skipped", "error": "virtual_entry_no_late_retry"})
        legs = leg_records(row.snapshot)
        partial_done = any(leg.get("role") == "tp1" and leg.get("take_profit_filled") for _, leg in legs)
        for index, data in legs:
            if data["status"] != "open":
                continue
            pid = data.get("position_id")
            position = next((position for position in broker["positions"] if position["position_id"] == pid), None)
            if not position:
                continue
            protections = [order for order in broker["orders"] if order.get("position_id") == pid
                           and order.get("order_type") == "sltp" and float(order.get("stop_loss_price") or 0) > 0
                           and float(order.get("take_profit_price") or 0) > 0]
            entry_time = int(datetime.fromisoformat(position.get("create_time", utc(now)).replace("Z", "+00:00")).timestamp())
            sig = row.snapshot.get("signal", {})
            if sig.get("management_frame") == "H1":
                from .forex_exits import closed_rows
                mgmt_rows = closed_rows(sig, [], frames.get("1h", []), now)
            else:
                mgmt_rows = minutes
            manual = row.snapshot.get("close_requested") or data.get("close_requested")
            required_close = "session_close" if near_close else "missing_protection" if not protections else "manual" if manual else None
            if required_close:
                key = f"rr-close-{api.account_id}-{symbol}-{row.signal_time}-{index}"
                persist_symbol_leg(api.account_id, symbol, row.signal_time, index,
                            {"status": "closing", "close_key": key, "close_reason": required_close})
                ack = client.mutate("DELETE", key, position_id=pid)
                persist_symbol_leg(api.account_id, symbol, row.signal_time, index, {"close_ack": ack})
                return "closing"
            if not costs_ready:
                continue
            if sig.get("exit_policy") in ADAPTIVE_EXITS:
                from .forex_exits import action as exits_action
                from .forex_exits import closed_rows as exits_closed_rows
                ex_rows = exits_closed_rows(sig, frames.get("15m", []), frames.get("1h", []), now)
                stop_str = initial_stop_of(data)
                if not stop_str:
                    raise ExnessError("missing_initial_stop_for_management")
                stop_val = float(stop_str)
                open_val = float(position["open_price"])
                risk_val = abs(open_val - stop_val)
                act = data.get("adaptive_decision")
                duration = 3600 if sig["management_frame"] == "H1" else 900
                if ex_rows and ex_rows[-1].time + duration > (act or {}).get("at", 0):
                    source = frames.get("1h" if duration == 3600 else "15m", [])
                    ask_close = next((bar.close for bar in source if bar.price_type == "ask"
                                      and bar.time == ex_rows[-1].time), None)
                    mark = ex_rows[-1].close if sig["side"] == "long" else ask_close
                    if mark is not None:
                        from .forex_samples import slippage_estimate
                        act = exits_action(sig, ex_rows, open_val, risk_val, entry_time,
                                           float(protections[0]["stop_loss_price"]), condition, float(mark),
                                           slippage_estimate(symbol)["bps"] / 10000,
                                           float(condition.get("commission_per_unit") or 0))
                        if act:
                            persist_symbol_leg(api.account_id, symbol, row.signal_time, index, {"adaptive_decision": act})
                if act and act.get("close_roles") and data.get("role") in act["close_roles"]:
                    key = f"rr-close-{api.account_id}-{symbol}-{row.signal_time}-{index}"
                    persist_symbol_leg(api.account_id, symbol, row.signal_time, index,
                                {"status": "closing", "close_key": key, "close_reason": act["reason"]})
                    ack = client.mutate("DELETE", key, position_id=pid)
                    persist_symbol_leg(api.account_id, symbol, row.signal_time, index, {"close_ack": ack})
                    return "closing"
                pending_management = data.get("management", {}).get("status") in ("submitting", "submitted")
                failed_stop = data.get("management", {}).get("status") == "failed" and data["management"].get("stop") == (act or {}).get("stop")
                if act and act.get("stop") and not pending_management and not failed_stop and client.can_modify and protections[0].get("order_id"):
                    direction = 1 if sig["side"] == "long" else -1
                    if direction * (float(act["stop"]) - float(protections[0]["stop_loss_price"])) <= 0:
                        continue
                    action = {"stop": act["stop"], "stage": "adaptive_stop",
                              "previous_stop": str(protections[0]["stop_loss_price"])}
                    order_id = str(protections[0]["order_id"])
                    key = f"sl-{pid}-{action['stop']}"
                    def on_modify(submission):
                        persist_symbol_leg(api.account_id, symbol, row.signal_time, index,
                                    {"management": {**action, "status": "submitting", "key": key}})
                    ack = client.mutate("PATCH", key, body={"stop_loss_price": action["stop"]}, order_id=order_id,
                                        quote_source=lambda: live_quote(symbol), on_submit=on_modify)
                    persist_symbol_leg(api.account_id, symbol, row.signal_time, index,
                                {"management": {**action, "status": "submitted", "key": key, "ack": ack}})
                    return "managing_stop"
                continue
            early = None if sig.get("exit_policy") == "fixed_structural" else exit_reason(
                position.get("direction"), entry_time, mgmt_rows)
            manual = row.snapshot.get("close_requested") or data.get("close_requested")
            reason = ("session_close" if near_close else "missing_protection" if not protections
                      else "manual" if manual else early)
            if reason:
                key = f"rr-close-{api.account_id}-{symbol}-{row.signal_time}-{index}"
                persist_symbol_leg(api.account_id, symbol, row.signal_time, index,
                            {"status": "closing", "close_key": key, "close_reason": reason})
                ack = client.mutate("DELETE", key, position_id=pid)
                persist_symbol_leg(api.account_id, symbol, row.signal_time, index, {"close_ack": ack})
                return "closing"
            initial = data.get("body", {}).get("stop_loss_price")
            pending_management = data.get("management", {}).get("status") in ("submitting", "submitted")
            if not initial or pending_management or not client.can_modify or not protections[0].get("order_id"):
                continue
            if row.snapshot.get("signal", {}).get("partial_be") is False:
                continue
            quote = live_quote(symbol)
            validate_quote(quote)
            action = protective_stop(position, initial, quote, protections[0], condition, mgmt_rows, partial_done)
            if action:
                action["previous_stop"] = str(protections[0]["stop_loss_price"])
            if not action or (data.get("management", {}).get("status") == "failed"
                              and data["management"].get("stop") == action["stop"]):
                continue
            order_id = str(protections[0]["order_id"])
            key = f"sl-{pid}-{action['stop']}"
            def on_modify(submission):
                persist_symbol_leg(api.account_id, symbol, row.signal_time, index,
                            {"management": {**action, "status": "submitting", "key": key}})
            ack = client.mutate("PATCH", key, body={"stop_loss_price": action["stop"]}, order_id=order_id,
                                quote_source=lambda: live_quote(symbol), on_submit=on_modify)
            persist_symbol_leg(api.account_id, symbol, row.signal_time, index,
                        {"management": {**action, "status": "submitted", "key": key, "ack": ack}})
            return "managing_stop"
    if not costs_ready:
        return "costs_unverified"
    for row in own:
        retry = next(((index, data) for index, data in leg_records(row.snapshot)
                      if data["status"] == "planned" and data.get("retryable_pre_submit")), None)
        if retry:
            index, data = retry
            if now - row.snapshot["signal"]["available_at"] > SIGNAL_WINDOW or near_close:
                for leg_index, leg in leg_records(row.snapshot):
                    if leg["status"] == "planned":
                        persist_symbol_leg(api.account_id, symbol, row.signal_time, leg_index,
                                    {"status": "skipped", "error": "signal_expired: retry window closed"})
                continue
            result = submit_leg(client, row, index, data, condition, now)
            if result == "submitted":
                with Session.begin() as session:
                    state = session.get(ExnessExecutionState, api.account_id)
                    record_journal(state, {"candle_time": row.signal_time}, "order_sent", now)
            return result
        if not row.snapshot.get("legs"):
            continue
        for index, data in leg_records(row.snapshot):
            if data["status"] != "planned":
                continue
            first = row.snapshot["legs"][0]
            if (first["status"] in ("closed", "rejected", "skipped", "closing")
                    or row.snapshot.get("close_requested") or near_close):
                persist_symbol_leg(api.account_id, symbol, row.signal_time, index, {"status": "skipped", "error": "Setup no longer active"})
                continue
            with Session() as session:
                state = session.get(ExnessExecutionState, api.account_id)
            if not state.enabled:
                persist_symbol_leg(api.account_id, symbol, row.signal_time, index, {"status": "skipped", "error": "Paused before runner"})
                continue
            if first["status"] != "open":
                continue
            if busy(broker):
                persist_symbol_leg(api.account_id, symbol, row.signal_time, index, {"status": "skipped", "error": "Runner capacity unavailable"})
                continue
            return submit_leg(client, row, index, data, condition, now)
    with Session.begin() as session:
        state = session.get(ExnessExecutionState, api.account_id)
        pending = session.scalars(select(ExnessExecutionTrade).where(
            ExnessExecutionTrade.account == api.account_id)).all()
        unresolved = any(row.snapshot["status"] in PENDING or any(
            leg.get("management", {}).get("status") in ("submitting", "submitted")
            for _, leg in leg_records(row.snapshot)) for row in pending)
        from .db import ExnessEntryIntent
        unresolved = unresolved or session.scalar(select(ExnessEntryIntent.id).where(
            ExnessEntryIntent.account == api.account_id, ExnessEntryIntent.status == "uncertain").limit(1)) is not None
        reason = "symbol_disabled" if symbol not in configured_symbols() else "costs_unverified" if not costs_ready else "paused" if not state.enabled else "reconciling" if unresolved else (
            "position_active" if busy(broker) else
            "session_close_near" if near_close else
            "history_catching_up" if state.snapshot.get("history_until", 0) < now else None)
        from .forex_entry_intents import pilot_enabled
        from .forex_htf_v2 import execution_enabled
        if not reason and not entry_intent_id and pilot_enabled() and execution_enabled():
            from .forex_entry_intents import create_intent
            from .forex_entry_pilot import PROVENANCE as PILOT_PROVENANCE
            from .forex_entry_pilot import candidates as pilot_candidates
            from .forex_htf_v2 import virtual_candidates
            waiting = pilot_candidates(api.account_id, symbol, now) + virtual_candidates(api.account_id, symbol, now)
            for sample in sorted(waiting, key=lambda item: (item["provenance"] != PILOT_PROVENANCE, item["strategy"])):
                proposal = sample["signal"]
                from .db import ExnessEntryIntent
                from .forex_entry_intents import identity
                if session.get(ExnessEntryIntent, identity(api.account_id, symbol, proposal["setup_id"], proposal["side"])):
                    continue
                if any(row.snapshot.get("signal", {}).get("setup_id") == proposal["setup_id"] for row in pending if row.symbol == symbol):
                    continue
                max_legs = (3 if proposal["exit_policy"] == "ladder_h1" else
                            1 if proposal["exit_policy"] in ("fixed_structural", "runner_h1", "trail_swing_h1") else 2)
                if capacity_available(broker, api.account_id, symbol, max_legs):
                    create_intent(session, api.account_id, proposal, sample["tree"], now, max_legs,
                                  version=sample["strategy_version"], provenance=sample["provenance"])
                    break
        if entry_intent_id:
            from .db import ExnessEntryIntent
            from .forex_entry_intents import execution_signal
            requested = session.get(ExnessEntryIntent, entry_intent_id)
            signal = (execution_signal(requested) if requested and requested.account == api.account_id
                      and requested.symbol == symbol and requested.status == "triggered" else None)
        else:
            signal = evaluate(session, now, symbol)
        if signal and str(signal.get("strategy", "")).startswith(("hast3_", "hast4_")) and signal.get("shadow_only") is False:
            try:
                signal = validate_execution_signal(signal, api.account_id, now)
            except ExnessError as exc:
                reason, signal = str(exc), None
        elif signal and (signal.get("shadow_only") or str(signal.get("strategy", "")).startswith(("ast_", "hast_", "hast2_", "hast3_", "hast4_"))):
            reason, signal = "shadow_only_signal", None
        detail = session.info.get("execution_evaluation", {
            "symbol": symbol, "candle_time": now // 900 * 900 - 900, "evaluated": False})
        detail = {**detail, "symbol": symbol}
        if reason:
            signal = None
        decision = reason or (signal or {}).get("reason", "Waiting for fresh closed M15 signal")
        state.snapshot = {**state.snapshot, "decision": decision,
                          "symbols": {**state.snapshot.get("symbols", {}), symbol: {"decision": decision, "at": now}}}
        record_journal(state, detail, tick_outcome(reason, signal, detail), now)
        if reason or not signal or signal["status"] != "confirmed":
            return reason or "waiting_signal"
        stamp = signal["candle_time"]
        if signal["available_at"] < state.snapshot.get("armed_at", state.started_at) or session.get(ExnessExecutionTrade, (api.account_id, symbol, stamp)):
            record_journal(state, detail, "old_or_duplicate_signal", now)
            return "old_or_duplicate_signal"
        digest = hashlib.sha256(f"{api.account_id}:{symbol}:{stamp}".encode()).hexdigest()[:16]
        key = f"fx-{symbol}-{digest}"
        if signal.get("setup_id"):
            duplicate = any(row.snapshot.get("signal", {}).get("setup_id") == signal["setup_id"]
                            for row in session.scalars(select(ExnessExecutionTrade).where(
                                ExnessExecutionTrade.account == api.account_id, ExnessExecutionTrade.symbol == symbol)).all())
            if duplicate:
                record_journal(state, detail, "old_or_duplicate_signal", now)
                return "old_or_duplicate_signal"
        intent_row = None
        if signal.get("entry_intent_id"):
            from .db import ExnessEntryIntent
            intent_row = session.get(ExnessEntryIntent, signal["entry_intent_id"])
            if intent_row:
                intent_row.status = "submitting"
        try:
            quote = live_quote(symbol)
            equity = broker.get("account_state", {}).get("equity")
            setup_budget = risk_budget(equity)
            body = client.strategy_body(signal, condition, quote, key, equity=equity)
            late_drift_ok(signal, quote["ask" if body["side"] == "buy" else "bid"], now)
            if signal.get("htf_ast_v2") and signal.get("exit_policy") in ADAPTIVE_EXITS:
                from .forex_exits import plan_legs
                bodies = plan_legs(signal, body, condition, Decimal(str(quote["ask" if body["side"] == "buy" else "bid"])))
            else:
                bodies = split_bodies(body, condition, Decimal(str(quote["ask" if body["side"] == "buy" else "bid"])),
                                      partial_be=signal.get("split", signal.get("partial_be", True)))
        except ExnessError as exc:
            detail["reason"] = str(exc)[:200]
            record_journal(state, detail, "blocked:preflight_failed", now)
            state.snapshot = {**state.snapshot, "decision": "preflight_failed: " + str(exc)[:160]}
            if intent_row:
                intent_row.status = "cancelled"
                reason_detail = str(exc)[:200]
                intent_row.snapshot = {**intent_row.snapshot, "status": "cancelled", "reason": "preflight_failed",
                                       "reason_code": reason_detail.split(":", 1)[0].strip(), "reason_detail": reason_detail, "ended_at": now}
            return "preflight_failed"
        if not capacity_available(broker, api.account_id, symbol, len(bodies), entry_intent_id=signal.get("entry_intent_id")):
            record_journal(state, detail, "position_capacity_for_split", now)
            if intent_row:
                intent_row.status = "cancelled"
                intent_row.snapshot = {**intent_row.snapshot, "status": "cancelled", "reason": "capacity_unavailable", "ended_at": now}
            return "position_capacity_for_split"
        legs = [{"key": key + f"-{index}", "body": {name: value for name, value in part.items() if name != "role"},
                 "role": part["role"], "status": "planned", "recorded_at": now,
                 "contract_size": condition["contract_size"]} for index, part in enumerate(bodies)]
        record = {"symbol": symbol, "key": key, "recorded_at": now, "status": "planned", "signal": signal,
                  "risk_equity": str(equity), "risk_budget_usd": str(setup_budget), "risk_percent": 1,
                  "signal_time": stamp, "side": signal["side"], "reason": signal["reason"],
                  "legs": legs, "exit_policy": "structural_tp_v1" if signal.get("target_context") else "split_1r_2r_be_trail_early_v1"}
        if len(legs) == 1:
            record = {**record, **legs[0]}
            record.pop("legs")
        record_journal(state, detail, "order_planned", now)
        session.add(ExnessExecutionTrade(account=api.account_id, symbol=symbol, signal_time=stamp, snapshot=record))
    row = type("Intent", (), {"snapshot": record, "signal_time": stamp, "symbol": symbol})()
    try:
        result = submit_leg(client, row, 0 if len(legs) > 1 else None, legs[0], condition, now)
        if result == "submitted":
            with Session.begin() as s:
                st = s.get(ExnessExecutionState, api.account_id)
                if st:
                    record_journal(st, {"candle_time": stamp}, "order_sent", now)
        if signal.get("entry_intent_id"):
            for index, data in enumerate(legs[1:], start=1):
                if result != "submitted":
                    persist_symbol_leg(api.account_id, symbol, stamp, index, {"status": "skipped", "error": "previous_leg_not_confirmed"})
                    continue
                result = submit_leg(client, row, index, data, condition, int(time.time()))
        return result
    except Exception as exc:
        with Session.begin() as s:
            st = s.get(ExnessExecutionState, api.account_id)
            if st:
                outcome = "api_rate_limited" if getattr(exc, "rate_limited", False) and not client.last_submission else "execution_error"
                record_journal(st, {"candle_time": stamp, "reason": str(exc)[:200]}, outcome, now)
        raise

def submit_leg(client, row, index, data, condition, now):
    from .forex_conversion import cost_context
    symbol = getattr(row, "symbol", "XAUUSD")
    api = client.api
    with Session() as session:
        state = session.get(ExnessExecutionState, api.account_id)
        if not state.enabled:
            persist_symbol_leg(api.account_id, symbol, row.signal_time, index, {"status": "skipped", "error": "Paused before submit"})
            return "paused"
    current = client.snapshot()
    if not capacity_available(current, api.account_id, symbol):
        persist_symbol_leg(api.account_id, symbol, row.signal_time, index, {"status": "skipped", "error": "Position limit reached"})
        return "position_active"
    signal = row.snapshot["signal"]
    latest_snapshot = row.snapshot
    if signal.get("entry_intent_id"):
        with Session() as session:
            latest = session.get(ExnessExecutionTrade, (api.account_id, symbol, row.signal_time))
            latest_snapshot = latest.snapshot
    key = data["key"]
    def build(quote):
        submitted_at = max(now, int(time.time()))
        fresh_condition = cost_context(condition, submitted_at, account=api.account_id, symbol=symbol)
        if fresh_condition.get("commission_per_unit") is None:
            raise ExnessError("costs_unverified")
        late_drift_ok(signal, quote["ask" if signal["side"] == "long" else "bid"], submitted_at)
        equity = current.get("account_state", {}).get("equity")
        budget = risk_budget(equity)
        if latest_snapshot.get("risk_budget_usd") is not None:
            budget = min(budget, positive_decimal(latest_snapshot["risk_budget_usd"]))
        full = client.strategy_body(signal, fresh_condition, quote, key, equity=equity)
        volume = positive_decimal(data["body"]["volume"])
        if volume > positive_decimal(full["volume"]):
            raise ExnessError("Fresh quote no longer supports planned lot")
        full["volume"] = format(volume, "f")
        entry = Decimal(str(quote["ask" if full["side"] == "buy" else "bid"]))
        contract = positive_decimal(condition["contract_size"])
        committed = Decimal(0)
        for other_index, other in leg_records(latest_snapshot):
            if other_index == index or other["status"] not in (*ACTIVE, "closed"):
                continue
            opened = next((position for position in current["positions"]
                           if position["position_id"] == other.get("position_id")), None)
            other_entry = (positive_decimal(opened["open_price"]) if opened else
                           Decimal(str(other.get("quote", {}).get("ask" if full["side"] == "buy" else "bid", entry))))
            committed += abs(other_entry - positive_decimal(other["body"]["stop_loss_price"])) * positive_decimal(other["body"]["volume"]) * contract
        conversion = positive_decimal(fresh_condition["quote_to_account_loss"])
        if (committed + abs(entry - Decimal(full["stop_loss_price"])) * volume * contract) * conversion > budget:
            raise ExnessError("Combined split risk exceeds setup budget")
        if data["role"] == "tp1":
            target = entry + entry - Decimal(full["stop_loss_price"])
            full["take_profit_price"] = format(target.quantize(Decimal(1).scaleb(-condition["point_digits"])), "f")
        elif signal.get("entry_intent_id") and data["role"] in ("partial1", "partial2"):
            target = positive_decimal(data["body"]["take_profit_price"])
            if (target - entry) * (1 if signal["side"] == "long" else -1) <= 0:
                raise ExnessError("virtual_partial_target_passed")
            full["take_profit_price"] = str(target)
        return full
    def on_submit(submission):
        persist_symbol_leg(api.account_id, symbol, row.signal_time, index,
                    {"status": "submitting", "body": submission["body"], "quote": submission["quote"],
                     "tp_reason": signal.get("tp_reason"),
                     "cost_profile": cost_context(condition, submission["submitted_at"], account=api.account_id, symbol=symbol),
                     "submitted_at": submission["submitted_at"]})
    try:
        ack = client.mutate("POST", key, body=data["body"], quote_source=lambda: live_quote(symbol),
                            build=build, on_submit=on_submit)
        persist_symbol_leg(api.account_id, symbol, row.signal_time, index, {"status": "submitted", "ack": ack})
        return "submitted"
    except Exception as exc:
        if (getattr(exc, "rate_limited", False) and not client.last_submission
                and index in (None, 0) and data["status"] == "planned"):
            persist_symbol_leg(api.account_id, symbol, row.signal_time, index,
                        {"status": "planned", "retryable_pre_submit": True, "error": str(exc)[:200]})
            raise
        persist_symbol_leg(api.account_id, symbol, row.signal_time, index,
                    {"status": "uncertain" if client.last_submission else "skipped",
                     "error": type(exc).__name__ + ": " + str(exc)[:200]})
        raise

def record_error(account, exc, now=None, symbol=None):
    now = int(time.time()) if now is None else now
    with Session.begin() as session:
        state = session.get(ExnessExecutionState, account)
        if state:
            msg = type(exc).__name__ + ": " + str(exc)[:200]
            label = "api_rate_limited" if getattr(exc, "rate_limited", False) else "worker_error"
            state.snapshot = {**state.snapshot, "error": type(exc).__name__, "error_msg": msg,
                              "error_at": now, "error_symbol": symbol}
            for affected in (symbol,) if symbol else configured_symbols():
                record_journal(state, {"symbol": affected, "candle_time": now // 900 * 900 - 900,
                                       "reason": msg}, label, now)


@router.get("")
def execution_summary(offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100), symbol: str = "XAUUSD",
                      view: str = "tickets", status: str = "all"):
    from .forex_position_groups import group_page, position_groups, setup_key, ticket_owners
    if view not in ("tickets", "setups") or status not in ("all", "open", "closed", "bot"):
        raise HTTPException(422, "Unsupported execution view or status")
    request_symbol(symbol)
    account = os.getenv("EXNESS_ACCOUNT_ID", "").strip()
    with Session() as session:
        state = session.get(ExnessExecutionState, account)
        deals = session.scalars(select(ExnessDeal).where(ExnessDeal.account == account,
            ExnessDeal.position_id.is_not(None)).order_by(ExnessDeal.event_time, ExnessDeal.deal_id)).all()
        intents = session.scalars(select(ExnessExecutionTrade).where(ExnessExecutionTrade.account == account, ExnessExecutionTrade.symbol == symbol)
                                  .order_by(ExnessExecutionTrade.signal_time.desc())).all()
    meta = state.snapshot if state else {}
    broker = meta.get("broker", {})
    positions = {}
    for row in deals:
        deal, pid = row.snapshot, row.position_id
        if deal.get("instrument") != symbol:
            continue
        position = positions.setdefault(pid, {"symbol": symbol, "position_id": pid, "side": None, "volume": None,
            "open_time": None, "open_price": None, "close_time": None, "close_price": None,
            "status": "history_incomplete", "pnl": None, "deals": [], "source": "external"})
        position["deals"].append(deal)
    for position in broker.get("positions", []):
        if position.get("instrument") == symbol:
            row = positions.setdefault(position["position_id"], {"symbol": symbol, "position_id": position["position_id"],
                "close_time": None, "close_price": None, "pnl": None, "deals": [], "source": "external"})
            row.update(side=position["direction"], volume=float(position["volume"]),
                       open_price=float(position["open_price"]), status="open",
                       open_time=int(datetime.fromisoformat(position["create_time"].replace("Z", "+00:00")).timestamp()))
    active = {str(position["position_id"]): position for position in broker.get("positions", [])}
    active_ids = set(active)
    owners = ticket_owners(intents)
    for pid, position in positions.items():
        opens = [deal for deal in position["deals"] if deal["type"] == "open"]
        closes = [deal for deal in position["deals"] if deal["type"] == "close"]
        initial_volume = sum(float(deal["volume"]) for deal in opens) if opens else None
        position.update(initial_volume=initial_volume, has_closes=bool(closes),
                        remaining_volume=float(active[pid]["volume"]) if pid in active else None,
                        ownership_verified=False, setup_id=None)
        if opens:
            volume = sum(float(deal["volume"]) for deal in opens)
            position.update(side=opens[0]["direction"], volume=volume,
                open_time=opens[0]["event_time"], open_price=sum(float(deal["price"]) * float(deal["volume"]) for deal in opens) / volume)
        if closes:
            volume = sum(float(deal["volume"]) for deal in closes)
            position.update(close_time=closes[-1]["event_time"], close_price=sum(float(deal["price"]) * float(deal["volume"]) for deal in closes) / volume,
                pnl=sum(float(deal.get("profit", {}).get("amount", 0)) for deal in closes))
        if pid in active_ids:
            position["status"] = "open"
        elif opens and closes and sum(positive_decimal(deal["volume"]) for deal in closes) >= sum(positive_decimal(deal["volume"]) for deal in opens):
            position["status"] = "closed"
            position["remaining_volume"] = 0
        if closes and any(deal.get("profit", {}).get("amount") is None for deal in closes):
            position["pnl"] = None
        owner = owners.get((account, symbol, str(pid)))
        if owner and position.get("side") in ("buy", "sell") and owner[1].get("body", {}).get("side") == position.get("side"):
            intent, owned_leg = owner
            data = intent.snapshot
            matched_leg = owned_leg
            if matched_leg or data.get("position_id") == pid:
                leg_info = matched_leg or data
                body = leg_info.get("body", {})
                mgmt = leg_info.get("management", {})
                position.update(source="learning" if data.get("signal", {}).get("policy_mode") else "regime_router",
                    signal_time=intent.signal_time, reason=data.get("reason"),
                    setup_id=setup_key(account, symbol, intent.signal_time), ownership_verified=True,
                    take_profit_filled=bool(leg_info.get("take_profit_filled")),
                    strategy=data.get("signal", {}).get("strategy"),
                    role=leg_info.get("role", "runner"),
                    exit_variant=data.get("signal", {}).get("exit_variant"),
                    exit_variant_mode=data.get("signal", {}).get("exit_variant_mode"),
                    sl_buffer_k=data.get("signal", {}).get("sl_buffer_k"),
                    tp_reason="Chốt một phần tại 1R" if leg_info.get("role") == "tp1" else leg_info.get("tp_reason", data.get("signal", {}).get("tp_reason")),
                    management_stage=mgmt.get("stage"),
                    management_status=mgmt.get("status"),
                    management_error=mgmt.get("error"),
                    close_reason=leg_info.get("close_reason"),
                    partial_taken=any(other.get("role") == "tp1" and other.get("take_profit_filled")
                                      for _, other in leg_records(data)),
                    stop=float(body["stop_loss_price"]) if body.get("stop_loss_price") else None,
                    target=float(body["take_profit_price"]) if body.get("take_profit_price") else None,
                    close_requested=data.get("close_requested", False) or leg_info.get("close_requested", False))
        for order in broker.get("orders", []):
            if order.get("position_id") == pid and order.get("order_type") == "sltp":
                position.update(stop=float(order.get("stop_loss_price") or 0) or None,
                                target=float(order.get("take_profit_price") or 0) or None)
    rows = sorted(positions.values(), key=lambda row: row.get("open_time") or 0, reverse=True)
    heartbeat = meta.get("heartbeat")
    result = {"enabled": enabled() and bool(state and state.enabled), "configured": enabled(),
        "worker_online": bool(heartbeat and time.time() - heartbeat < 90), "heartbeat": heartbeat,
        "account": account, "strategy": "learning", "lot": "Theo rủi ro 1% equity", "risk_percent": 1,
        "symbol": symbol, "max_concurrent": max_positions(), "max_concurrent_per_symbol": max_positions_per_symbol(),
        "planned_risk_ceiling_usd": planned_risk_usd(broker.get("account_state", {}).get("equity")),
        "stop_management_available": meta.get("stop_management_available", False),
        "error": meta.get("error"),
        "error_msg": meta.get("error_msg") if meta.get("error") else None,
        "error_at": meta.get("error_at") if meta.get("error") else None,
        "error_symbol": meta.get("error_symbol") if meta.get("error") else None,
        "decision": meta.get("symbols", {}).get(symbol, {}).get("decision", meta.get("decision") if symbol == "XAUUSD" else None),
        "account_state": broker.get("account_state"), "synced_at": meta.get("history_synced_at"),
        "history_from": meta.get("history_from", state.started_at - 86400) if state else None,
        "positions": rows[offset:offset + limit], "total": len(rows),
        "has_more": offset + limit < len(rows), "intents": [row.snapshot for row in intents[:20]],
        "open_count": len(broker.get("positions", [])), "orders": broker.get("orders", []),
        "symbol_open_count": sum(position.get("instrument") == symbol for position in broker.get("positions", [])),
        "closed_count": sum(row["status"] == "closed" for row in rows),
        "broker_profit": round(sum(row.get("pnl") or 0 for row in rows), 2),
        "learning": learning_summary(symbol=symbol),
        "journal": list(reversed([row for row in meta.get("journal", []) if row.get("symbol", "XAUUSD") == symbol])), "funnel": funnel([row for row in meta.get("journal", []) if row.get("symbol", "XAUUSD") == symbol])}
    if view == "setups":
        result.update(group_page(position_groups(rows), offset, limit, status), view=view)
        result["positions"] = [leg for group in result["groups"] for leg in group["legs"]]
    return result


@router.get("/trades.csv")
def execution_csv(symbol: str = "XAUUSD"):
    request_symbol(symbol)
    fields = ("symbol", "position_id", "source", "side", "status", "volume", "open_time", "open_price",
              "close_time", "close_price", "stop", "target", "pnl", "signal_time", "reason")
    def rows():
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(fields)
        yield buffer.getvalue()
        offset = 0
        while True:
            page = execution_summary(offset=offset, limit=100, symbol=symbol)
            for position in page["positions"]:
                values = [position.get(field) for field in fields]
                values = ["'" + value if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")) else value
                          for value in values]
                buffer.seek(0)
                buffer.truncate(0)
                writer.writerow(values)
                yield buffer.getvalue()
            if not page["has_more"]:
                break
            offset += 100
    return StreamingResponse(rows(), media_type="text/csv", headers={
        "Content-Disposition": f'attachment; filename="exness-{symbol}-trades.csv"', "Cache-Control": "no-store"})

class ExecutionControl(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: StrictBool


@router.patch("")
def control(body: ExecutionControl, symbol: str = "XAUUSD"):
    request_symbol(symbol)
    if not enabled():
        raise HTTPException(409, "Demo execution not configured")
    with Session.begin() as session:
        state = session.get(ExnessExecutionState, os.getenv("EXNESS_ACCOUNT_ID", ""))
        if not state:
            raise HTTPException(409, "Executor has not verified demo account")
        state.enabled = body.enabled
        if body.enabled:
            state.snapshot = {**state.snapshot, "armed_at": int(time.time())}
    return {"enabled": body.enabled}


@router.post("/trades/{signal_time}/close")
def request_close(signal_time: int, position_id: str | None = Query(None, pattern=r"^[0-9]+$"), symbol: str = "XAUUSD"):
    request_symbol(symbol)
    if not enabled():
        raise HTTPException(409, "Demo execution not configured")
    with Session.begin() as session:
        row = session.get(ExnessExecutionTrade, (os.getenv("EXNESS_ACCOUNT_ID", ""), symbol, signal_time))
        if not row or not any(leg["status"] == "open" for _, leg in leg_records(row.snapshot)):
            raise HTTPException(409, "No open bot-owned position to close")
        if position_id is None:
            row.snapshot = {**row.snapshot, "close_requested": True}
        else:
            matched = next(((index, leg) for index, leg in leg_records(row.snapshot)
                            if leg.get("position_id") == position_id and leg["status"] == "open"), None)
            if not matched:
                raise HTTPException(409, "Position does not belong to this open setup")
            if matched[0] is None:
                row.snapshot = {**row.snapshot, "close_requested": True}
            else:
                legs = [dict(leg) for leg in row.snapshot["legs"]]
                legs[matched[0]]["close_requested"] = True
                row.snapshot = {**row.snapshot, "legs": legs}
    return {"status": "close_requested"}
def planned_risk_usd(equity):
    try:
        return float(risk_budget(equity))
    except ExnessError:
        return None
