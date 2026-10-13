"""Offline MTF replay to a separate JSON dataset. No DB writes, signals or orders."""

import argparse
import hashlib
import json
import time
from collections import Counter
from pathlib import Path

from backend.app.market import Candle
from backend.app.mexc import SOURCE, SYMBOL, validate_candles
from backend.app.mtf_research import advance, sample_values
from backend.app.observation import HISTORY, observation_steps


def run_replay(candles, now=None):
    validate_candles(candles)
    now = time.time() if now is None else now
    candles = [candle for candle in candles if candle.time + 900 <= now]
    samples, active, events = [], [], []
    last_key = None
    for window, snapshot in observation_steps(candles):
        for sample in active:
            sample["evaluation"] = advance(sample["evaluation"], window[-1])
        active = [s for s in active if s["evaluation"]["status"] in ("pending", "open")]
        if snapshot["status"] == "confirmed" and snapshot["confirm_time"] == window[-1].time:
            sample = sample_values(window, snapshot)
            sample["provenance"] = "historical"
            samples.append(sample)
            active.append(sample)
        key = (snapshot["status"], snapshot["breakout_time"], snapshot["side"])
        if key != last_key:
            events.append(snapshot)
            last_key = key
    return dict(
        version="mtf_replay_v1", provenance="historical", observation_only=True,
        source=SOURCE, symbol=SYMBOL, warmup_bars=HISTORY,
        start=candles[HISTORY - 1].time, end=candles[-1].time + 900,
        processed_bars=len(candles) - HISTORY + 1,
        assumptions=dict(fee_bps=5, slippage_bps=2, funding_included=False, horizon_bars=96),
        samples=samples, events=events,
        summary=dict(Counter(sample["evaluation"]["status"] for sample in samples)),
    )

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path, default=Path("/tmp/walkforward-cache.json"))
    parser.add_argument("--output", type=Path, default=Path("research-output/mtf-historical-samples.json"))
    parser.add_argument("--days", type=int, help="Replay last N days, plus 1,600 warmup bars")
    args = parser.parse_args()
    if args.days is not None and args.days <= 0:
        parser.error("--days must be positive")
    if args.cache.resolve() == args.output.resolve():
        parser.error("Output must not overwrite the input cache")
    raw = args.cache.read_bytes()
    cached = json.loads(raw)
    data = cached.get("data", cached) if isinstance(cached, dict) else cached
    records = data["candles"] if isinstance(data, dict) else data
    candles = [Candle(**record) for record in records]
    if args.days:
        candles = candles[-(args.days * 96 + HISTORY - 1):]
    report = run_replay(candles)
    report["cache_sha256"] = hashlib.sha256(raw).hexdigest()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False))
    temporary.replace(args.output)
    print(json.dumps(dict(output=str(args.output), bars=report["processed_bars"],
                         samples=len(report["samples"]), outcomes=report["summary"])))

if __name__ == "__main__":
    main()
