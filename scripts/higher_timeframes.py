"""Fixed 1h/4h signals with original 15m execution, funding and costs; research only."""

import argparse
import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path

from backend.app.backtest import BacktestConfig, simulate
from backend.app.market import Candle
from backend.app.mexc import validate_candles
from backend.app.strategy import aggregate, breakout, retest
from scripts.walkforward import WARMUP, download, evaluate


def higher_signal(signal, seconds):
    if seconds not in (3600, 14400):
        raise ValueError("Signal timeframe must be 1h or 4h")

    def on_close(candles, allow_short=False):
        if not candles or (candles[-1].time + 900) % seconds:
            return None
        closed = aggregate(candles, seconds)
        return signal(closed, allow_short=allow_short)

    return on_close


def run(cache, output, months):
    if not cache.exists():
        data = download(months)
        cache.write_text(json.dumps({"months": months, "data": data}))
    raw = cache.read_bytes()
    cached = json.loads(raw)
    if cached["months"] != months:
        raise ValueError("Cache months mismatch; choose a separate cache path")
    data = cached["data"]
    candles = [Candle(**record) for record in data["candles"]]
    fair = [Candle(**record) for record in data["fair"]]
    validate_candles(candles)
    validate_candles(fair)
    if [c.time for c in candles] != [c.time for c in fair]:
        raise ValueError("Fair-price history must match trading history")
    funding = data["funding"]
    if (
        not funding
        or any(
            not math.isfinite(row["rate"]) or row["cycle_hours"] <= 0 or row["time"] % 900
            for row in funding
        )
        or any(
            current["time"] - previous["time"] != current["cycle_hours"] * 3600
            for previous, current in zip(funding, funding[1:])
        )
    ):
        raise ValueError("Incomplete or invalid funding history")
    starts = list(range(WARMUP, len(candles) - 2880 + 1, 2880))
    if (
        not starts
        or funding[0]["time"] > candles[starts[0]].time
        or funding[-1]["time"] + funding[-1]["cycle_hours"] * 3600
        < candles[starts[-1] + 2879].time + 900
    ):
        raise ValueError("Insufficient funding/evaluation coverage")
    report = {
        "version": "higher_timeframes_v1",
        "cache_sha256": hashlib.sha256(raw).hexdigest(),
        "source": "MEXC BTC_USDT perpetual",
        "history_months": months,
        "start_utc": datetime.fromtimestamp(candles[starts[0]].time, timezone.utc).isoformat(),
        "end_utc": datetime.fromtimestamp(
            candles[starts[-1] + 2879].time + 900, timezone.utc
        ).isoformat(),
        "execution_timeframe": "15m",
        "config": BacktestConfig().__dict__,
        "warmup_bars_15m": WARMUP,
        "window_days": 30,
        "criteria": {
            "min_trades": 100,
            "pf_gt": 1.3,
            "positive_windows_gte": 0.6,
            "worst_dd_lt": 10,
        },
        "warning": "Fixed exploratory screen, not independent validation; no automatic promotion.",
        "results": {},
    }
    for timeframe, seconds in (("1h", 3600), ("4h", 14400)):
        for name, signal in (("breakout", breakout), ("retest", retest)):
            wrapped = higher_signal(signal, seconds)
            windows = [
                simulate(
                    candles[start - WARMUP : start + 2880],
                    BacktestConfig(),
                    funding,
                    fair[start - WARMUP : start + 2880],
                    data["contract"],
                    warmup=WARMUP,
                    signal=wrapped,
                    window=WARMUP,
                )
                for start in starts
            ]
            result = evaluate(windows)
            if not math.isfinite(result["pf"]):
                result["pf"] = None
            result["periods"] = [
                {
                    key: window[key]
                    for key in (
                        "start_time",
                        "end_time",
                        "trade_count",
                        "return_percent",
                        "net_pnl",
                        "max_drawdown_percent",
                        "total_fees",
                        "funding_cost",
                    )
                }
                for window in windows
            ]
            report["results"][f"{name}_{timeframe}"] = result
            print(
                f"{name}_{timeframe}",
                json.dumps({key: value for key, value in result.items() if key != "periods"}),
                flush=True,
            )
    output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--months", type=int, choices=(12, 36), default=36)
    parser.add_argument("--cache", type=Path, default=Path("/tmp/higher-timeframes-36m-cache.json"))
    parser.add_argument("--output", type=Path, default=Path("/tmp/higher-timeframes-v1.json"))
    args = parser.parse_args()
    run(args.cache, args.output, args.months)
