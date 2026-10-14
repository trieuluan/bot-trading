"""Opt-in DEMO broker execution for regime_router.

Strictly demo only. Configurable position cap, 0.01 lot each. Native broker SL/TP.
Deals synced from Exness are the ground truth for chart markers and trade history.
"""

import json
import fcntl
import hashlib
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import urlopen

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, StrictBool
from sqlalchemy import select, text

from .db import (ExnessDeal, ExnessExecutionState, ExnessExecutionTrade, engine,
                 ForexObservation, Session, WorkspaceFeedState)
from .exness import ExnessError
from .exness_demo_client import ExnessDemoClient, positive_decimal, validate_quote
from .exness_feed import FRAMES, WORKSPACE
from .exness_observation import audit_frames, load_frames
from .exness_sessions import session_closed_ahead
from .forex_learner import live_signal, summary as learning_summary

router = APIRouter(prefix="/api/workspaces/exness_forex/execution")
PENDING = ("submitting", "submitted", "uncertain", "closing")
ACTIVE = ("submitting", "submitted", "uncertain", "open", "closing")


def enabled():
    return (os.getenv("EXNESS_DEMO_EXECUTION_ENABLED") == "true"
            and os.getenv("EXNESS_ENVIRONMENT") == "demo"
            and os.getenv("EXNESS_SYMBOL") == "XAUUSD")


def max_positions():
    # ponytail: cap 1..20 via env; add runtime settings when needed.
    try:
        return min(20, max(1, int(os.getenv("EXNESS_DEMO_MAX_POSITIONS", "1"))))
    except ValueError:
        return 1

def busy(broker):
    pending = [order for order in broker["orders"] if order.get("order_type") != "sltp"]
    return pending or len(broker["positions"]) >= max_positions()

def utc(stamp):
    return datetime.fromtimestamp(stamp, timezone.utc).isoformat()


def live_quote():
    base = os.getenv("EXNESS_QUOTE_BASE_URL", "http://api:8000")
    deadline = time.monotonic() + 5
    with urlopen(base + "/api/workspaces/exness_forex/quotes/stream", timeout=5) as stream:
        while time.monotonic() < deadline:
            line = stream.readline()
            if not line:
                break
            if line.startswith(b"data: "):
                data = json.loads(line[6:])
                if data.get("symbol") == "XAUUSD" and data.get("status") == "live":
                    return validate_quote(data.get("quote") or {})
    raise ExnessError("Fresh XAUUSD quote unavailable")


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


def persist(account, stamp, values):
    with Session.begin() as session:
        row = session.get(ExnessExecutionTrade, (account, stamp))
        row.snapshot = {**row.snapshot, **values}


def reconcile(api, broker, now):
    with Session() as session:
        trades = session.scalars(select(ExnessExecutionTrade).where(
            ExnessExecutionTrade.account == api.account_id)).all()
        deals = session.scalars(select(ExnessDeal).where(ExnessDeal.account == api.account_id)).all()
    open_ids = {position["position_id"] for position in broker["positions"]}
    for row in trades:
        data = row.snapshot
        if data["status"] not in ACTIVE:
            continue
        pid = data.get("position_id")
        if not pid:
            operation = data.get("ack", {}).get("operation_id")
            if operation:
                result = api.get(f"/v1/trading/accounts/{api.account_id}/operations/{operation}")
                if result["status"] in ("rejected", "failed"):
                    persist(api.account_id, row.signal_time,
                            {"status": "rejected", "error": result.get("error_message", "Broker rejected")})
                    continue
            start = data["recorded_at"] - 5
            if now - start > 86400:
                continue
            orders = list(history(api, "orders", start, now))
            matching = [item["order"] for item in orders
                        if item["order"].get("comment") == data["key"][:32]
                        and item["order"].get("instrument") == "XAUUSD"]
            for order in matching:
                if order.get("state") == "rejected":
                    persist(api.account_id, row.signal_time, {"status": "rejected"})
                if order.get("state") == "filled" and order.get("position_id"):
                    pid = order["position_id"]
                    break
                filled = next((deal for deal in deals if deal.snapshot.get("order_id") == order["order_id"]
                               and deal.snapshot.get("type") == "open"), None)
                if filled:
                    pid = filled.position_id
                    break
        if pid:
            fills = [deal for deal in deals if deal.position_id == pid]
            has_close = any(deal.snapshot.get("type") == "close" for deal in fills)
            status = "open" if pid in open_ids else "closed" if has_close else "uncertain"
            if data["status"] == "closing" and pid in open_ids:
                operation = data.get("close_ack", {}).get("operation_id")
                if operation:
                    result = api.get(f"/v1/trading/accounts/{api.account_id}/operations/{operation}")
                    if result["status"] in ("failed", "rejected"):
                        persist(api.account_id, row.signal_time,
                                {"error": "Close rejected; reconcile before a new close request"})
                    status = "closing"
                else:
                    status = "closing"
            persist(api.account_id, row.signal_time, {"position_id": pid, "status": status})


def evaluate(session, now):
    frames = load_frames(session, now)
    feeds = [session.get(WorkspaceFeedState, (WORKSPACE, frame)) for frame in FRAMES]
    if audit_frames(frames)["status"] == "blocked" or any(
        not state or state.error or state.checked_until is None
        or state.checked_until < now // FRAMES[frame][1] * FRAMES[frame][1]
        for frame, state in zip(FRAMES, feeds)
    ):
        return None
    minutes = [row for row in frames["15m"] if row.price_type == "bid"]
    if not minutes or not 0 <= now - (minutes[-1].time + 900) <= 120:
        return None
    candle = minutes[-1]
    breakout = session.get(ForexObservation, (WORKSPACE, candle.time))
    ask = next((row for row in frames["15m"] if row.price_type == "ask" and row.time == candle.time), None)
    if ask is None:
        return None
    return live_signal(candle, [row for row in frames["1h"] if row.price_type == "bid"], minutes,
                       ask, breakout.snapshot if breakout else None)


def execution_tick(api, now=None):
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
        with engine.connect() as connection:
            locked = connection.dialect.name != "postgresql" or connection.scalar(
                text("SELECT pg_try_advisory_lock(hashtext(:name))"), {"name": "demo-" + api.account_id})
            if not locked:
                return "executor_active"
            try:
                return _execution_tick(api, now)
            finally:
                if connection.dialect.name == "postgresql":
                    connection.execute(text("SELECT pg_advisory_unlock(hashtext(:name))"),
                                       {"name": "demo-" + api.account_id})


def _execution_tick(api, now=None):
    now = int(time.time()) if now is None else now
    if not enabled():
        return "disabled"
    client = ExnessDemoClient(api)
    condition = client.prepare(require_flat=False)
    broker = client.snapshot()
    with Session.begin() as session:
        state = session.get(ExnessExecutionState, api.account_id)
        if state is None:
            state = ExnessExecutionState(account=api.account_id, enabled=True, started_at=now,
                                         snapshot={"history_from": now - 86400})
            session.add(state)
        state.snapshot = {**state.snapshot, "heartbeat": now, "broker": broker, "error": None,
                          "demo_verified": True}
    sync_deals(api, now)
    reconcile(api, broker, now)
    near_close = session_closed_ahead(now // 900 * 900, 2)
    with Session() as session:
        own = session.scalars(select(ExnessExecutionTrade).where(
            ExnessExecutionTrade.account == api.account_id)).all()
    for row in own:
        data = row.snapshot
        protections = [order for order in broker["orders"] if order.get("position_id") == data.get("position_id")
                       and order.get("order_type") == "sltp" and float(order.get("stop_loss_price") or 0) > 0
                       and float(order.get("take_profit_price") or 0) > 0]
        if data["status"] != "open" or not (near_close or data.get("close_requested") or not protections):
            continue
        pid = data["position_id"]
        if not any(position["position_id"] == pid for position in broker["positions"]):
            continue
        key = f"rr-close-{api.account_id}-{row.signal_time}"
        persist(api.account_id, row.signal_time, {"status": "closing", "close_key": key})
        ack = client.mutate("DELETE", key, position_id=pid)
        persist(api.account_id, row.signal_time, {"close_ack": ack, "close_reason":
                                                "session_close" if near_close else "missing_protection" if not protections else "manual"})
        return "closing"
    with Session.begin() as session:
        state = session.get(ExnessExecutionState, api.account_id)
        pending = session.scalars(select(ExnessExecutionTrade).where(
            ExnessExecutionTrade.account == api.account_id)).all()
        unresolved = any(row.snapshot["status"] in PENDING for row in pending)
        reason = "paused" if not state.enabled else "reconciling" if unresolved else (
            "position_active" if busy(broker) else
            "session_close_near" if near_close else
            "history_catching_up" if state.snapshot.get("history_until", 0) < now else None)
        signal = evaluate(session, now) if reason is None else None
        state.snapshot = {**state.snapshot, "decision": reason or (signal or {}).get("reason", "Waiting for fresh closed M15 signal")}
        if reason or not signal or signal["status"] != "confirmed":
            return reason or "waiting_signal"
        stamp = signal["candle_time"]
        if signal["available_at"] < state.snapshot.get("armed_at", state.started_at) or session.get(ExnessExecutionTrade, (api.account_id, stamp)):
            return "old_or_duplicate_signal"
        key = f"rr-{api.account_id}-{stamp}"
        record = {"key": key, "recorded_at": now, "status": "submitting", "signal": signal,
                  "signal_time": stamp, "side": signal["side"], "reason": signal["reason"]}
        session.add(ExnessExecutionTrade(account=api.account_id, signal_time=stamp, snapshot=record))
    try:
        with Session() as session:
            if not session.get(ExnessExecutionState, api.account_id).enabled:
                persist(api.account_id, stamp, {"status": "skipped", "error": "Paused before submit"})
                return "paused"
        current = client.snapshot()
        if busy(current):
            persist(api.account_id, stamp, {"status": "skipped", "error": "Position limit reached"})
            return "position_active"
        quote = live_quote()
        body = client.strategy_body(signal, condition, quote, key)
        def on_submit(submission):
            persist(api.account_id, stamp, {"body": submission["body"], "quote": submission["quote"],
                                           "submitted_at": submission["submitted_at"]})
        ack = client.mutate("POST", key, body=body, quote_source=live_quote,
                            build=lambda fresh: client.strategy_body(signal, condition, fresh, key),
                            on_submit=on_submit)
        persist(api.account_id, stamp, {"status": "submitted", "ack": ack})
        return "submitted"
    except Exception as exc:
        persist(api.account_id, stamp, {"status": "uncertain" if client.last_submission else "skipped",
                                       "error": type(exc).__name__ + ": " + str(exc)[:200]})
        raise


def record_error(account, exc):
    with Session.begin() as session:
        state = session.get(ExnessExecutionState, account)
        if state:
            state.snapshot = {**state.snapshot, "error": type(exc).__name__, "error_at": int(time.time())}


@router.get("")
def execution_summary(offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100)):
    account = os.getenv("EXNESS_ACCOUNT_ID", "").strip()
    with Session() as session:
        state = session.get(ExnessExecutionState, account)
        deals = session.scalars(select(ExnessDeal).where(ExnessDeal.account == account,
            ExnessDeal.position_id.is_not(None)).order_by(ExnessDeal.event_time, ExnessDeal.deal_id)).all()
        intents = session.scalars(select(ExnessExecutionTrade).where(ExnessExecutionTrade.account == account)
                                  .order_by(ExnessExecutionTrade.signal_time.desc())).all()
    meta = state.snapshot if state else {}
    broker = meta.get("broker", {})
    positions = {}
    for row in deals:
        deal, pid = row.snapshot, row.position_id
        if deal.get("instrument") != "XAUUSD":
            continue
        position = positions.setdefault(pid, {"position_id": pid, "side": None, "volume": None,
            "open_time": None, "open_price": None, "close_time": None, "close_price": None,
            "status": "history_incomplete", "pnl": None, "deals": [], "source": "external"})
        position["deals"].append(deal)
    for position in broker.get("positions", []):
        if position.get("instrument") == "XAUUSD":
            row = positions.setdefault(position["position_id"], {"position_id": position["position_id"],
                "close_time": None, "close_price": None, "pnl": None, "deals": [], "source": "external"})
            row.update(side=position["direction"], volume=float(position["volume"]),
                       open_price=float(position["open_price"]), status="open",
                       open_time=int(datetime.fromisoformat(position["create_time"].replace("Z", "+00:00")).timestamp()))
    active_ids = {position["position_id"] for position in broker.get("positions", [])}
    for pid, position in positions.items():
        opens = [deal for deal in position["deals"] if deal["type"] == "open"]
        closes = [deal for deal in position["deals"] if deal["type"] == "close"]
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
        for intent in intents:
            data = intent.snapshot
            if data.get("position_id") == pid:
                body = data.get("body", {})
                position.update(source="learning" if data.get("signal", {}).get("policy_mode") else "regime_router",
                    signal_time=intent.signal_time, reason=data.get("reason"),
                    stop=float(body["stop_loss_price"]) if body.get("stop_loss_price") else None,
                    target=float(body["take_profit_price"]) if body.get("take_profit_price") else None,
                    close_requested=data.get("close_requested", False))
        for order in broker.get("orders", []):
            if order.get("position_id") == pid and order.get("order_type") == "sltp":
                position.update(stop=float(order.get("stop_loss_price") or 0) or None,
                                target=float(order.get("take_profit_price") or 0) or None)
    rows = sorted(positions.values(), key=lambda row: row.get("open_time") or 0, reverse=True)
    heartbeat = meta.get("heartbeat")
    return {"enabled": enabled() and bool(state and state.enabled), "configured": enabled(),
        "worker_online": bool(heartbeat and time.time() - heartbeat < 90), "heartbeat": heartbeat,
        "account": account, "strategy": "learning", "lot": "0.01", "max_concurrent": max_positions(),
        "planned_risk_ceiling_usd": 25, "error": meta.get("error"), "decision": meta.get("decision"),
        "account_state": broker.get("account_state"), "synced_at": meta.get("history_synced_at"),
        "history_from": meta.get("history_from", state.started_at - 86400) if state else None,
        "positions": rows[offset:offset + limit], "total": len(rows),
        "has_more": offset + limit < len(rows), "intents": [row.snapshot for row in intents[:20]],
        "open_count": len(broker.get("positions", [])), "orders": broker.get("orders", []),
        "closed_count": sum(row["status"] == "closed" for row in rows),
        "broker_profit": round(sum(row.get("pnl") or 0 for row in rows), 2),
        "learning": learning_summary()}


class ExecutionControl(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: StrictBool


@router.patch("")
def control(body: ExecutionControl):
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
def request_close(signal_time: int):
    if not enabled():
        raise HTTPException(409, "Demo execution not configured")
    with Session.begin() as session:
        row = session.get(ExnessExecutionTrade, (os.getenv("EXNESS_ACCOUNT_ID", ""), signal_time))
        if not row or row.snapshot["status"] != "open":
            raise HTTPException(409, "No open bot-owned position to close")
        row.snapshot = {**row.snapshot, "close_requested": True}
    return {"status": "close_requested"}
