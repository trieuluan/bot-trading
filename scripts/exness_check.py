"""Read-only Exness connectivity check. Prints no secrets and sends no orders.

PYTHONPATH=. python scripts/exness_check.py
PYTHONPATH=. python scripts/exness_check.py --real
"""

import argparse
import os
import time

from backend.app.exness import ExnessError, ExnessReadOnly


def check_real_account(api, symbol):
    api.use_access_point()
    account = api.account()
    if str(account.get("account_id")) != api.account_id or account.get("is_demo") is not False:
        raise ExnessError("Broker must confirm the configured real account")
    settings = account.get("settings")
    if not isinstance(settings, dict) or settings.get("currency") != "USD":
        raise ExnessError("Expected a USD account for the current execution model")

    snapshot = api.get(f"/v1/trading/accounts/{api.account_id}/snapshot")
    positions, orders = snapshot.get("positions"), snapshot.get("orders")
    if (not isinstance(snapshot.get("account_state"), dict)
            or not isinstance(positions, list) or not isinstance(orders, list)):
        raise ExnessError("Invalid broker snapshot")

    condition = api.condition(symbol)
    if (condition.get("instrument") != symbol or condition.get("trade_mode") != "enabled"
            or condition.get("exec_mode") != "market"):
        raise ExnessError(f"Market execution unavailable for {symbol}")

    return {"account_type": "real", "currency": "USD", "symbol": symbol,
            "positions": len(positions), "orders": len(orders)}


def real_preflight():
    names = ("EXNESS_LIVE_API_KEY", "EXNESS_LIVE_PRIVATE_KEY", "EXNESS_LIVE_ACCOUNT_ID",
             "EXNESS_LIVE_API_BASE_URL")
    values = [os.getenv(name, "").strip() for name in names]
    missing = [name for name, value in zip(names, values) if not value]
    if missing:
        raise ExnessError("Missing " + ", ".join(missing))
    symbol = os.getenv("EXNESS_LIVE_SYMBOL", "XAUUSD").strip().upper()
    api = ExnessReadOnly(*values)
    try:
        result = check_real_account(api, symbol)
    finally:
        api.client.close()
    print("Read-only check passed", " | ".join(f"{key}={value}" for key, value in result.items()))


def verify_demo_cutover():
    from sqlalchemy import select

    from backend.app.db import ExnessEntryIntent, ExnessExecutionState, ExnessExecutionTrade, Session
    from backend.app.exness_demo_client import ExnessDemoClient
    from backend.app.exness_execution import ACTIVE, leg_records
    from backend.app.forex_entry_intents import RESERVED, cancel_waiting

    api = ExnessReadOnly.from_env()
    try:
        api.use_access_point()
        client = ExnessDemoClient(api)
        client.verify_demo()
        broker = client.snapshot()
        with Session.begin() as session:
            state = session.get(ExnessExecutionState, api.account_id, with_for_update=True)
            if not state or state.enabled:
                raise ExnessError("Pause demo opening before verifying the cutover")
            if broker["positions"] or broker["orders"]:
                raise ExnessError("Demo broker still has positions or orders")
            trades = session.scalars(select(ExnessExecutionTrade).where(ExnessExecutionTrade.account == api.account_id))
            if any(leg["status"] in (*ACTIVE, "planned") for trade in trades for _, leg in leg_records(trade.snapshot)):
                raise ExnessError("Demo trades still require reconciliation")
            now = int(time.time())
            cancel_waiting(session, api.account_id, "account_cutover", now)
            unresolved = session.scalar(select(ExnessEntryIntent.id).where(
                ExnessEntryIntent.account == api.account_id, ExnessEntryIntent.status.in_(RESERVED)).limit(1))
            if unresolved:
                raise ExnessError("Demo intents still require reconciliation")
            state.snapshot = {**state.snapshot, "broker": broker, "demo_cutover_flat_at": now}
    finally:
        api.client.close()
    print("Demo cutover verified; account paused and flat")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--real", action="store_true", help="Check a real account using EXNESS_LIVE_* credentials")
    modes.add_argument("--verify-demo-cutover", action="store_true", help="Record a broker-verified paused and flat demo account")
    args = parser.parse_args()
    if args.verify_demo_cutover:
        verify_demo_cutover()
        return
    if args.real:
        real_preflight()
        return

    symbol = os.getenv("EXNESS_SYMBOL", "XAUUSD")
    api = ExnessReadOnly.from_env()
    api.use_access_point()
    account = api.account()
    settings = account.get("settings", {})
    print("account:", account.get("account_name"), "| currency:", settings.get("currency"),
          "| leverage:", settings.get("leverage"), "| trade_mode:", settings.get("trade_mode"),
          "| status:", settings.get("account_status"))  # fmt: skip
    names = api.instruments().get("instruments", [])
    print("instruments:", len(names), "| has", symbol + ":", symbol in names)
    print("condition keys:", sorted(api.condition(symbol)))
    limits = api.limits()
    caps = [m["limits"] for m in limits.get("rest", {}).get("methods", []) if m.get("limits")]
    print("candle cap:", caps[0].get("max_candles_per_request") if caps else "n/a")
    end = int(time.time()) // 3600 * 3600
    rows = api.candles(symbol, "H1", end - 5 * 3600, end)
    print("H1 candles:", len(rows))
    for row in rows[-2:]:
        print(" ", row["time"], "bid", row["bid"]["close"], "ask", row["ask"]["close"])


if __name__ == "__main__":
    try:
        main()
    except ExnessError as exc:
        raise SystemExit(f"FAILED: {exc}")
