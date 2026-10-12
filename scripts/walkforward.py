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
from backend.app.strategy import breakout, mtf_retest, retest

CACHE = Path("/tmp/walkforward-cache.json")
WARMUP = 1600  # every strategy trades the same bars; lets EMA200 / 4h EMA50 converge
EMA_PERIOD = 200  # single pre-declared variant, not tuned


def with_trend(signal, period=EMA_PERIOD):
    """Keep only setups aligned with EMA(period) of closed candles: long above, short below."""

    def filtered(candles, allow_short=False):
        setup = signal(candles, allow_short=allow_short)
        if setup is None:
            return None
        ema, k = candles[0].close, 2 / (period + 1)
        for c in candles[1:]:
            ema += k * (c.close - ema)
        return setup if (setup.side == "long") == (candles[-1].close > ema) else None

    return filtered


def first_of(*signals):
    """Fixed rule, declared before running: first non-empty signal wins (order = argument order)."""

    def combined(candles, allow_short=False):
        return next((x for f in signals if (x := f(candles, allow_short=allow_short))), None)

    return combined


STRATEGIES = {
    "breakout": (breakout, 21),
    "retest": (retest, 32),
    "breakout+ema200": (with_trend(breakout), WARMUP),
    "union(breakout,retest)": (first_of(breakout, retest), WARMUP),
    "mtf(4h,1h,15m)": (mtf_retest, WARMUP),
    "union+ema200": (with_trend(first_of(breakout, retest)), WARMUP),
}
ROTATING = "rotate-by-prev-window"


def rotate_previous(results):
    names = ("breakout", "retest", "breakout+ema200")
    count = len(results[names[0]])
    if count < 2 or any(len(results[name]) != count for name in names):
        raise ValueError("Rotation needs at least two aligned windows per strategy")

    def previous_pf(name, index):
        trades = results[name][index - 1]["trades"]
        gain = sum(max(0, trade["net_pnl"]) for trade in trades)
        loss = -sum(min(0, trade["net_pnl"]) for trade in trades)
        return gain / loss if loss else (float("inf") if gain else 0)

    picks = [max(names, key=lambda name: previous_pf(name, index)) for index in range(1, count)]
    return picks, [results[name][index] for index, name in enumerate(picks, 1)]


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
    starts = range(WARMUP, len(candles) - size + 1, size)
    print(f"{len(candles)} candles, {len(starts)} windows of {window_days}d, fresh capital each\n")
    results = {}
    for name, (signal, window) in STRATEGIES.items():
        rows = results[name] = [
            simulate(
                candles[s - WARMUP : s + size], config, data["funding"],
                fair[s - WARMUP : s + size], data["contract"],
                warmup=WARMUP, signal=signal, window=window,
            )
            for s in starts
        ]  # fmt: skip
        report(name, rows)
    # Honest "pick the best": choose by PF of the PREVIOUS window only, trade the next one.
    picks, rotated = rotate_previous(results)
    report(ROTATING, rotated)
    print("  picks:", ", ".join(picks))


def report(name, rows):
    r = evaluate(rows)
    print(f"{name}: windows={r['windows']} trades={r['trades']} pooledPF={r['pf']:.2f} "
          f"meanRet={r['mean_return']:.2f}% positive={r['positive']:.0%} worstDD={r['worst_dd']:.2f}%")  # fmt: skip
    print("  returns%:", " ".join(f"{w['return_percent']:.1f}" for w in rows))
    print("  ", ", ".join(f"{k}: {'ok' if v else 'FAIL'}" for k, v in r["checks"].items()),
          "=> PASS" if r["pass"] else "=> FAIL")  # fmt: skip


if __name__ == "__main__":
    main(*(int(x) for x in sys.argv[1:3]))
