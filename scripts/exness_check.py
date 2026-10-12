"""Read-only Exness connectivity check. Prints no secrets and sends no orders.

PYTHONPATH=. python scripts/exness_check.py
"""

import os
import time

from backend.app.exness import ExnessError, ExnessReadOnly


def main():
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
