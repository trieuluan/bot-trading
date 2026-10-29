"""Transactional, account/symbol/setup scoped virtual entry state machine."""

import hashlib
import os
import time

from sqlalchemy import select, text

from .db import ExnessEntryIntent, ExnessExecutionState
from .forex_entry_policy import live_entry_allowed, observe

RESERVED = ("waiting", "triggered", "submitting", "submitted", "uncertain")


def execution_signal(row):
    payload = row.snapshot
    sig = payload["signal"]
    if not live_entry_allowed(sig["entry_policy"], sig["side"], payload.get("winner")):
        from .exness import ExnessError
        raise ExnessError("limit_short_shadow_only")
    body = {**sig, "tree": payload["tree"], "shadow_only": False,
            "execution_source": payload.get("provenance", "live_forward_htf_ast4_entry_pilot"),
            "strategy_version": payload["strategy_version"],
            "entry_intent_id": row.id, "entry_branch": payload["winner"]}
    if payload.get("trigger_quote"):
        body["ref_price"] = payload["trigger_quote"]["ask" if sig["side"] == "long" else "bid"]
    return body


SUBMIT_WINDOW = 30  # seconds after trigger; price safety = fresh live_quote (<=2s) + 0.25R drift in late_drift_ok

def submit_window_ok(snapshot, now):
    # ponytail: broker prep takes seconds, so quote age is not gated here; add per-step timing if SUBMIT_WINDOW proves too loose.
    triggered = snapshot.get("triggered_at")
    return type(triggered) in (int, float) and 0 <= now - triggered <= SUBMIT_WINDOW

def validate_execution_signal(signal, account, now):
    from .db import Session
    from .exness import ExnessError
    from .forex_entry_pilot import PROVENANCE as PILOT_PROVENANCE
    from .forex_entry_pilot import logic_version as pilot_version
    from .forex_entry_pilot import read_ledger as read_pilot
    from .forex_entry_policy import tree_id
    from .forex_htf_v4 import PROVENANCE, logic_version, read_ledger
    if not pilot_enabled() or signal.get("shadow_only") is not False:
        raise ExnessError("entry_pilot_disabled")
    if not live_entry_allowed(signal.get("entry_policy", {}), signal.get("side"), signal.get("entry_branch")):
        raise ExnessError("limit_short_shadow_only")
    with Session() as session:
        row = session.get(ExnessEntryIntent, signal.get("entry_intent_id", ""))
        state = session.get(ExnessExecutionState, account)
        if (not row or row.account != account or row.status not in ("triggered", "submitting", "submitted")
                or not state or not state.enabled or row.snapshot.get("continuity_error")
                or not submit_window_ok(row.snapshot, now)):
            raise ExnessError("unverified_virtual_entry")
        expected = execution_signal(row)
        supplied = dict(signal)
        if supplied.get("tp_reason") == expected["htf_structure"]["target_reason"]:
            supplied.pop("tp_reason")
        supplied.pop("partial_skip_reason", None)
        expected_version = logic_version()
        if row.snapshot.get("provenance", PROVENANCE) == PILOT_PROVENANCE:
            ledger, expected_version = read_pilot(account, row.symbol), pilot_version()
        elif row.snapshot.get("provenance", PROVENANCE) != PROVENANCE:
            from .forex_htf_v2 import PROVENANCE as MAIN_PROVENANCE
            from .forex_htf_v2 import logic_version as main_version
            from .forex_htf_v2 import read_ledger as read_main
            if row.snapshot["provenance"] != MAIN_PROVENANCE:
                raise ExnessError("unverified_virtual_entry")
            ledger = read_main(account, row.symbol)
            expected_version = main_version()
        else:
            ledger = read_ledger(account, row.symbol)
        source = next((sample for sample in ledger["samples"] if sample["strategy"] == row.strategy
                       and sample["setup_id"] == row.setup_id
                       and sample["strategy_version"] == row.snapshot["strategy_version"]
                       and sample["costs"].get("verified") is True), None)
        if (row.snapshot["strategy_version"] != expected_version or row.strategy != tree_id(row.snapshot["tree"])
                or row.snapshot["tree"] not in ledger["population"] or supplied != expected
                or source is None or source["signal"] != row.snapshot["signal"]):
            raise ExnessError("unverified_virtual_entry")
        return expected


def pilot_enabled():
    return os.getenv("FOREX_ENTRY_PILOT_ENABLED", "false").lower() == "true"


def identity(account, symbol, setup_id, side):
    if not account or not symbol or not setup_id or side not in ("long", "short"):
        raise ValueError("Invalid entry identity")
    return "fx-entry-" + hashlib.sha256(f"{account}:{symbol}:{setup_id}:{side}".encode()).hexdigest()[:40]


def lock_account(session, account):
    if session.get_bind().dialect.name == "postgresql":
        session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:name))"), {"name": "entry-" + account})
    elif session.get_bind().dialect.name == "sqlite":
        connection = session.connection()
        if not connection.connection.driver_connection.in_transaction:
            connection.exec_driver_sql("BEGIN IMMEDIATE")


def create_intent(session, account, signal, tree, now, max_legs, *, version=None, provenance=None):
    from .forex_entry_policy import SCHEMA, tree_id
    lock_account(session, account)
    entry = signal["entry_policy"]
    if not live_entry_allowed(entry, signal["side"]):
        raise ValueError("limit_short_shadow_only")
    if (tree["schema"] != SCHEMA or tree_id(tree) != signal["strategy"]
            or not entry["available_at"] <= now < entry["expires_at"] or type(max_legs) is not int or not 1 <= max_legs <= 3):
        raise ValueError("Invalid virtual entry request")
    key = identity(account, signal["symbol"], signal["setup_id"], signal["side"])
    existing = session.get(ExnessEntryIntent, key)
    if existing:
        return existing
    from .forex_entry_pilot import PROVENANCE, logic_version
    payload = {**entry, "status": "waiting", "side": signal["side"], "signal": signal,
               "strategy_version": version or logic_version(), "provenance": provenance or PROVENANCE,
               "tree": tree, "reserved_legs": max_legs, "created_at": now}
    row = ExnessEntryIntent(id=key, account=account, symbol=signal["symbol"], setup_id=signal["setup_id"],
                            strategy=signal["strategy"], side=signal["side"], status="waiting",
                            limit_price=entry["limit_price"], stop_price=entry["stop_price"],
                            available_at=entry["available_at"], expires_at=entry["expires_at"], snapshot=payload)
    session.add(row)
    session.flush()
    return row


def consume_quote(session, account, symbol, quote, now, *, connected=True, invalidated=False):
    started = time.monotonic()
    lock_account(session, account)
    session.info["entry_lock_wait"] = time.monotonic() - started
    state = session.get(ExnessExecutionState, account)
    from .exness_execution import enabled
    from .forex_htf_v2 import execution_enabled
    allowed = pilot_enabled() and enabled() and execution_enabled() and bool(state and state.enabled)
    rows = session.scalars(select(ExnessEntryIntent).where(ExnessEntryIntent.account == account,
                           ExnessEntryIntent.symbol == symbol,
                           ExnessEntryIntent.status.in_(("waiting", "triggered", "submitting"))).with_for_update()).all()
    triggered = []
    for row in rows:
        from .db import WorkspaceCandle
        from .exness_feed import WORKSPACE
        structure = row.snapshot["signal"]["htf_structure"]
        hours = session.scalars(select(WorkspaceCandle).where(
            WorkspaceCandle.workspace_id == WORKSPACE, WorkspaceCandle.symbol == symbol,
            WorkspaceCandle.timeframe == "1h", WorkspaceCandle.price_type == "bid",
            WorkspaceCandle.time >= structure["confirmed_h1"], WorkspaceCandle.time + 3600 <= now,
        ).order_by(WorkspaceCandle.time)).all()
        direction = 1 if row.side == "long" else -1
        current = quote.get("bid" if row.side == "long" else "ask") if isinstance(quote, dict) else None
        broken = (invalidated or any(direction * (bar.close - structure["invalidation"]) <= 0 for bar in hours)
                  or type(current) in (int, float) and direction * (current - structure["invalidation"]) <= 0)
        fresh = bool(hours and hours[0].time == structure["confirmed_h1"]
                     and hours[-1].time == int(now) // 3600 * 3600 - 3600
                     and all(right.time - left.time == 3600 for left, right in zip(hours, hours[1:])))
        previous_status = row.status
        candles = session.scalars(select(WorkspaceCandle).where(
            WorkspaceCandle.workspace_id == WORKSPACE, WorkspaceCandle.symbol == symbol,
            WorkspaceCandle.timeframe == "15m", WorkspaceCandle.price_type == "bid",
            WorkspaceCandle.time >= row.available_at, WorkspaceCandle.time < row.expires_at,
            WorkspaceCandle.time + 900 <= now,
        ).order_by(WorkspaceCandle.time)).all() if row.snapshot.get("policy") in ("stop_trigger_break", "limit_setup_mid", "limit_or_stop") else ()
        observation = observe({**row.snapshot, "status": "waiting"}, quote, now,
                              connected=connected and fresh, allowed=allowed, invalidated=broken, candles=candles)
        if previous_status != "waiting":
            row.snapshot = {**row.snapshot, "last_quote": observation.get("last_quote", row.snapshot.get("last_quote"))}
            # Expiry after trigger must not block a submit already in flight (window bounded by SUBMIT_WINDOW).
            if observation["status"] in ("cancelled", "expired") and observation.get("reason") != "entry_expired":
                row.snapshot = {**row.snapshot, "continuity_error": observation["reason"]}
            continue
        row.snapshot = observation
        row.status = observation["status"]
        if row.status == "triggered":
            triggered.append(row.id)
    return triggered


def cancel_waiting(session, account, reason, now, symbol=None, detail=None):
    lock_account(session, account)
    query = select(ExnessEntryIntent).where(ExnessEntryIntent.account == account,
                                          ExnessEntryIntent.status.in_(("waiting", "triggered", "submitting")))
    if symbol is not None:
        query = query.where(ExnessEntryIntent.symbol == symbol)
    rows = session.scalars(query.with_for_update()).all()
    for row in rows:
        if row.status != "waiting":
            row.snapshot = {**row.snapshot, "continuity_error": reason, **({"continuity_detail": detail} if detail else {})}
            continue
        row.status = "cancelled"
        row.snapshot = {**row.snapshot, "status": "cancelled", "reason": reason, "ended_at": now,
                        **({"cancel_detail": detail} if detail else {})}
    return len(rows)


def execute_trigger(api, identifier, symbol):
    from .exness_execution import execution_tick
    outcome = None
    try:
        outcome = execution_tick(api, entry_request=(identifier, symbol))
    except Exception as exc:
        outcome = f"execution_error: {type(exc).__name__}"
        raise
    finally:
        reconcile_intents(api.account_id, time.time(), identifier=identifier, outcome=outcome)

def reconcile_intents(account, now, *, identifier=None, restart=False, outcome=None):
    from .db import ExnessDeal, ExnessExecutionTrade, Session
    with Session.begin() as session:
        lock_account(session, account)
        query = select(ExnessEntryIntent).where(ExnessEntryIntent.account == account,
                                              ExnessEntryIntent.status.in_(RESERVED))
        if identifier:
            query = query.where(ExnessEntryIntent.id == identifier)
        for row in session.scalars(query.with_for_update()).all():
            if row.status == "waiting" and not restart:
                continue
            if row.status == "triggered" and not (identifier or restart or row.snapshot.get("continuity_error")):
                continue
            trade = session.get(ExnessExecutionTrade, (account, row.symbol, row.snapshot["signal"]["candle_time"]))
            if trade and trade.snapshot.get("signal", {}).get("entry_intent_id") == row.id:
                statuses = [leg["status"] for leg in trade.snapshot.get("legs", [trade.snapshot])]
                status = ("uncertain" if any(value in ("uncertain", "submitting", "planned") for value in statuses)
                          else "submitted" if any(value in ("submitted", "open", "closing") for value in statuses)
                          else "completed")
                row.snapshot = {**row.snapshot, "execution_trade": trade.signal_time}
                state = session.get(ExnessExecutionState, account)
                positions = (state.snapshot.get("broker", {}).get("positions", []) if state else [])
                fills = []
                level = row.snapshot.get("limit_price" if row.snapshot.get("winner") == "limit" else "stop_price")
                for leg in trade.snapshot.get("legs", [trade.snapshot]):
                    pid = leg.get("position_id")
                    if not pid:
                        continue
                    deals = session.scalars(select(ExnessDeal).where(ExnessDeal.account == account,
                                            ExnessDeal.position_id == pid, ExnessDeal.event_time <= now)).all()
                    opens = [deal.snapshot for deal in deals if deal.snapshot.get("type") == "open"]
                    position = next((value for value in positions if value["position_id"] == pid), None)
                    volume = sum(float(deal["volume"]) for deal in opens)
                    price = (sum(float(deal["price"]) * float(deal["volume"]) for deal in opens) / volume if volume else
                             float(position["open_price"]) if position else None)
                    if price is not None:
                        # Chỉ ghi giờ khi broker/ledger có dữ liệu; thiếu thì bỏ khoá, UI hiện "—".
                        filled_at = (min(deal["event_time"] for deal in opens if deal.get("event_time") is not None)
                                     if any(deal.get("event_time") is not None for deal in opens)
                                     else position.get("open_time") if position else None)
                        fills.append({"position_id": pid, "price": price,
                                      "deviation": (price - level) * (1 if row.side == "long" else -1) if level else None,
                                      **({"submitted_at": leg["submitted_at"]} if leg.get("submitted_at") else {}),
                                      **({"filled_at": filled_at} if filled_at else {})})
                row.snapshot = {**row.snapshot, "broker_fills": fills}
            elif row.status in ("submitting", "submitted", "uncertain"):
                status = "uncertain"
            else:
                status = "cancelled"
                row.snapshot = {**row.snapshot, "reason": "worker_restart" if restart else "trigger_preflight_failed",
                                "ended_at": now}
                if outcome and not restart:
                    row.snapshot = {**row.snapshot, "reason_code": str(outcome).split(":", 1)[0].strip()[:80],
                                    "reason_detail": str(outcome)[:200]}
            row.status = status
            row.snapshot = {**row.snapshot, "status": status}


def summary(session, account, symbol):
    rows = session.scalars(select(ExnessEntryIntent).where(ExnessEntryIntent.account == account,
                           ExnessEntryIntent.symbol == symbol).order_by(ExnessEntryIntent.available_at.desc()).limit(100)).all()
    return [{**row.snapshot, "id": row.id, "strategy": row.strategy, "setup_id": row.setup_id,
             "side": row.side, "status": row.status, "available_at": row.available_at,
             "expires_at": row.expires_at, "limit_price": row.limit_price, "stop_price": row.stop_price} for row in rows]
