"""Fixed futures hypotheses on cached MEXC data; no orders, tuning or live observers."""

import argparse
import hashlib
import json
import math
from bisect import bisect_left
from datetime import datetime, timezone
from pathlib import Path
from statistics import fmean, pstdev

from backend.app.backtest import BacktestConfig, simulate
from backend.app.market import Candle
from backend.app.mexc import validate_candles
from backend.app.strategy import Setup, aggregate, mtf_retest
from scripts.walkforward import WARMUP, evaluate


def atr14(candles):
    return fmean(
        max(
            current.high - current.low,
            abs(current.high - previous.close),
            abs(current.low - previous.close),
        )
        for previous, current in zip(candles[-15:-1], candles[-14:])
    )


def ema50(candles):
    value = candles[0].close
    for candle in candles[1:]:
        value += 2 / 51 * (candle.close - value)
    return value


def setup_at_close(last, direction, stop, target, reason):
    if (
        direction * (last.close - stop) <= 0
        or direction * (target - last.close) <= 0
        or target <= 0
    ):
        return None
    return Setup("long" if direction == 1 else "short", last.close, stop, target, reason)


def mean_reversion(candles, allow_short=False):
    """4h efficiency <= .30; 1h outside prior BB20/2; first 15m reclaims band, target mean."""
    hours, higher = aggregate(candles, 3600), aggregate(candles, 14400)
    if len(hours) < 21 or len(higher) < 21:
        return None
    last = candles[-1]
    touch = hours[-1]
    if last.time != touch.time + 3600:
        return None
    closes = [candle.close for candle in higher[-21:]]
    path = sum(abs(current - previous) for previous, current in zip(closes, closes[1:]))
    if path == 0 or abs(closes[-1] - closes[0]) / path > 0.30:
        return None
    reference = [candle.close for candle in hours[-21:-1]]
    mean, deviation = fmean(reference), pstdev(reference)
    if deviation <= 0:
        return None
    lower, upper = mean - 2 * deviation, mean + 2 * deviation
    buffer = atr14(candles) * 0.1
    if touch.close < lower < last.close and last.close > last.open:
        return setup_at_close(last, 1, min(touch.low, last.low) - buffer, mean, "MR v1")
    if allow_short and touch.close > upper > last.close and last.close < last.open:
        return setup_at_close(last, -1, max(touch.high, last.high) + buffer, mean, "MR v1")
    return None


def liquidity_sweep(candles, allow_short=False):
    """4h EMA50 direction; latest 15m wick sweeps prior 20 closed 1h bars and reclaims."""
    hours = aggregate(candles[:-1], 3600)
    higher = aggregate(candles, 14400)
    if len(hours) < 20 or len(higher) < 50:
        return None
    last = candles[-1]
    lower, upper = (
        min(candle.low for candle in hours[-20:]),
        max(candle.high for candle in hours[-20:]),
    )
    buffer = atr14(candles) * 0.1
    if (
        higher[-1].close > ema50(higher)
        and last.low < lower < last.close < upper
        and last.close > last.open
    ):
        stop = last.low - buffer
        return setup_at_close(last, 1, stop, last.close + 2 * (last.close - stop), "Sweep v1")
    if (
        allow_short
        and higher[-1].close < ema50(higher)
        and lower < last.close < upper < last.high
        and last.close < last.open
    ):
        stop = last.high + buffer
        return setup_at_close(last, -1, stop, last.close - 2 * (stop - last.close), "Sweep v1")
    return None


def funding_filter(records):
    times = [record["time"] for record in records]

    def signal(candles, allow_short=False):
        last = candles[-1]
        index = bisect_left(times, last.time + 900) - 1
        if index < 0 or len(candles) < 15:
            return None
        record = records[index]
        if last.time + 900 - record["time"] > record["cycle_hours"] * 3600:
            return None
        higher = aggregate(candles, 14400)
        if len(higher) < 50:
            return None
        trend = higher[-1].close - ema50(higher)
        buffer = atr14(candles) * 0.1
        if (
            record["rate"] <= -0.0003
            and trend > 0
            and last.close > max(candle.high for candle in candles[-11:-1])
        ):
            stop = min(candle.low for candle in candles[-6:]) - buffer
            return setup_at_close(last, 1, stop, last.close + 2 * (last.close - stop), "Funding v1")
        if (
            allow_short
            and record["rate"] >= 0.0003
            and trend < 0
            and last.close < min(candle.low for candle in candles[-11:-1])
        ):
            stop = max(candle.high for candle in candles[-6:]) + buffer
            return setup_at_close(
                last, -1, stop, last.close - 2 * (stop - last.close), "Funding v1"
            )
        return None

    return signal


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
