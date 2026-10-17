"""Fixed futures hypotheses on cached MEXC data; no orders, tuning or live observers."""

import argparse
import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path

from backend.app.backtest import BacktestConfig, simulate
from backend.app.futures_rules import funding_filter, liquidity_sweep, mean_reversion
from backend.app.market import Candle
from backend.app.mexc import validate_candles
from backend.app.strategy import mtf_retest
from scripts.walkforward import WARMUP, evaluate


def run(cache, output):
    raw = cache.read_bytes()
    data = json.loads(raw)["data"]
    candles = [Candle(**record) for record in data["candles"]]
    fair = [Candle(**record) for record in data["fair"]]
    validate_candles(candles)
    validate_candles(fair)
    if [candle.time for candle in candles] != [candle.time for candle in fair]:
        raise ValueError("Fair-price candles do not match trading candles")
    funding = data["funding"]
    if (
        not funding
        or any(
            not math.isfinite(record["rate"]) or record["cycle_hours"] <= 0 for record in funding
        )
        or any(
            current["time"] - previous["time"] != current["cycle_hours"] * 3600
            for previous, current in zip(funding, funding[1:])
        )
    ):
        raise ValueError("Missing, unordered or invalid funding history")
    starts = list(range(WARMUP, len(candles) - 2880 + 1, 2880))
    if (
        not starts
        or funding[0]["time"] > candles[starts[0]].time
        or funding[-1]["time"] + funding[-1]["cycle_hours"] * 3600 < candles[-1].time + 900
    ):
        raise ValueError("Insufficient evaluation/funding coverage")
    report = {
        "cache_sha256": hashlib.sha256(raw).hexdigest(),
        "source": "MEXC BTC_USDT perpetual",
        "config": BacktestConfig().__dict__,
        "start_utc": datetime.fromtimestamp(candles[starts[0]].time, timezone.utc).isoformat(),
        "end_utc": datetime.fromtimestamp(
            candles[starts[-1] + 2879].time + 900, timezone.utc
        ).isoformat(),
        "windows": len(starts),
        "window_days": 30,
        "warmup_bars": WARMUP,
        "criteria": {
            "min_trades": 100,
            "pf_gt": 1.3,
            "positive_windows_gte": 0.6,
            "worst_dd_lt": 10,
        },
        "warning": "Exploratory fixed rules on previously examined history; PASS is not evidence of live profitability.",
        "results": {},
    }
    for name, signal in {
        "baseline_mtf": mtf_retest,
        "mean_reversion_v1": mean_reversion,
        "liquidity_sweep_v1": liquidity_sweep,
        "funding_filter_v1": funding_filter(funding),
    }.items():
        windows = [
            simulate(
                candles[start - WARMUP : start + 2880],
                BacktestConfig(),
                funding,
                fair[start - WARMUP : start + 2880],
                data["contract"],
                warmup=WARMUP,
                signal=signal,
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
                    "net_pnl",
                    "return_percent",
                    "max_drawdown_percent",
                    "total_fees",
                    "funding_cost",
                )
            }
            for window in windows
        ]
        report["results"][name] = result
        print(
            name,
            json.dumps({key: value for key, value in result.items() if key != "periods"}),
            flush=True,
        )
    output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path, default=Path("/tmp/walkforward-cache.json"))
    parser.add_argument("--output", type=Path, default=Path("/tmp/futures-hypotheses-v1.json"))
    args = parser.parse_args()
    run(args.cache, args.output)
