"""Walk-forward comparison of breakout vs retest on long MEXC history. Research only.

    PYTHONPATH=. python scripts/walkforward.py [months=12] [window_days=30]

Rules are fixed (no tuning). Pass criteria are set here, BEFORE looking at results.
"""

import json
import sys
from dataclasses import asdict
from pathlib import Path

from backend.app.backtest import BacktestConfig, simulate
from backend.app.market import Candle
from backend.app.mexc import MexcFuturesAdapter
from backend.app.strategy import breakout, retest

CACHE = Path("/tmp/walkforward-cache.json")
STRATEGIES = {"breakout": (breakout, 21), "retest": (retest, 32)}
WARMUP = 20
# Pre-registered criteria, pooled over all non-overlapping windows, after fees/slippage/funding.
MIN_TRADES, MIN_PF, MIN_WIN_POSITIVE, MAX_DD = 100, 1.3, 0.6, 10.0


def evaluate(windows: list[dict]) -> dict:
    trades = [t for w in windows for t in w["trades"]]
    gain = sum(max(0, t["net_pnl"]) for t in trades)
    loss = -sum(min(0, t["net_pnl"]) for t in trades)
    pf = gain / loss if loss else float("inf") if gain else 0.0
    positive = sum(w["net_pnl"] > 0 for w in windows) / len(windows)
    worst = max(w["max_drawdown_percent"] for w in windows)
    checks = {
        f"trades>={MIN_TRADES}": len(trades) >= MIN_TRADES,
        f"pooled PF>{MIN_PF}": pf > MIN_PF,
        f"positive windows>={MIN_WIN_POSITIVE:.0%}": positive >= MIN_WIN_POSITIVE,
        f"worst DD<{MAX_DD:g}%": worst < MAX_DD,
    }
    return {
        "windows": len(windows), "trades": len(trades), "pf": pf, "positive": positive,
        "worst_dd": worst, "mean_return": sum(w["return_percent"] for w in windows) / len(windows),
        "checks": checks, "pass": all(checks.values()),
    }  # fmt: skip


def download(months: int) -> dict:
    a = MexcFuturesAdapter()
    try:
        now = a.server_time()
        end, start = now // 900 * 900, now // 900 * 900 - months * 30 * 86400
        candles, fair, cursor = [], [], start
        while cursor < end:  # API caps one call at 10,000 candles
            stop = min(end, cursor + 8000 * 900)
            candles += a.history(cursor, stop, now)
            fair += a.history(cursor, stop, now, fair=True)
            cursor = stop
        return {
            "candles": [asdict(c) for c in candles], "fair": [asdict(c) for c in fair],
            "funding": a.funding_history(start, end), "contract": a.contract_info(),
        }  # fmt: skip
    finally:
        a.close()


def main(months: int, window_days: int):
    if CACHE.exists() and json.loads(CACHE.read_text())["months"] == months:
        data = json.loads(CACHE.read_text())["data"]
    else:
        data = download(months)
        CACHE.write_text(json.dumps({"months": months, "data": data}))
    candles = [Candle(**c) for c in data["candles"]]
    fair = [Candle(**c) for c in data["fair"]]
    size, config = window_days * 96, BacktestConfig()
    starts = range(0, len(candles) - size + 1, size)
    print(f"{len(candles)} candles, {len(starts)} windows of {window_days}d, fresh capital each\n")
    for name, (signal, window) in STRATEGIES.items():
        rows = [
            simulate(
                candles[max(0, s - WARMUP) : s + size], config, data["funding"],
                fair[max(0, s - WARMUP) : s + size], data["contract"],
                warmup=WARMUP, signal=signal, window=window,
            )
            for s in starts
        ]  # fmt: skip
        r = evaluate(rows)
        print(f"{name}: windows={r['windows']} trades={r['trades']} pooledPF={r['pf']:.2f} "
              f"meanRet={r['mean_return']:.2f}% positive={r['positive']:.0%} worstDD={r['worst_dd']:.2f}%")  # fmt: skip
        print("  returns%:", " ".join(f"{w['return_percent']:.1f}" for w in rows))
        print("  ", ", ".join(f"{k}: {'ok' if v else 'FAIL'}" for k, v in r["checks"].items()),
              "=> PASS" if r["pass"] else "=> FAIL")  # fmt: skip


if __name__ == "__main__":
    main(*(int(x) for x in sys.argv[1:3]))
