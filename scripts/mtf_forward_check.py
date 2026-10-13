"""Check newly downloaded MEXC candles against the frozen exploratory ATR rule."""

import argparse
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from backend.app.market import Candle
from backend.app.mexc import MexcFuturesAdapter, validate_candles
from scripts.mtf_dataset_report import metrics
from scripts.mtf_historical_replay import run_replay


def evaluate_new_samples(samples, cutoff, threshold):
    new_samples = [sample for sample in samples if sample["features"]["available_at"] > cutoff]
    resolved = [sample for sample in new_samples if sample["evaluation"]["status"] == "resolved"]
    return {
        "new_samples": new_samples,
        "statuses": dict(Counter(sample["evaluation"]["status"] for sample in new_samples)),
        "baseline_resolved": metrics(resolved),
        "atr_filtered_resolved": metrics([
            sample for sample in resolved if sample["features"]["atr_percent"] > threshold
        ]),
    }


def self_check():
    samples = [{"features": {"available_at": timestamp, "atr_percent": atr},
                "evaluation": {"status": status, "outcome": "target", "net_r_ex_funding": 1}}
               for timestamp, atr, status in (
                   (100, 2, "resolved"), (101, 1, "resolved"),
                   (102, 2, "pending"), (103, 2, "resolved"),
               )]
    report = evaluate_new_samples(samples, 100, 1)
    assert len(report["new_samples"]) == 3
    assert report["statuses"] == {"resolved": 2, "pending": 1}
    assert report["baseline_resolved"]["samples"] == 2
    assert report["atr_filtered_resolved"]["samples"] == 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path, default=Path("/tmp/walkforward-cache.json"))
    parser.add_argument("--reference", type=Path, default=Path("research-output/mtf-historical-samples.json"))
    parser.add_argument("--rule", type=Path, default=Path("research-output/mtf-report.json"))
    parser.add_argument("--output", type=Path, default=Path("research-output/mtf-new-data-check.json"))
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args()
    if args.self_check:
        self_check()
        print("Self-check passed")
        return
    if args.output.resolve() in {path.resolve() for path in (args.cache, args.reference, args.rule)}:
        parser.error("Output must not overwrite an input")
    reference_bytes, rule_bytes, cache_bytes = (
        args.reference.read_bytes(), args.rule.read_bytes(), args.cache.read_bytes()
    )
    reference, rule, cached = map(json.loads, (reference_bytes, rule_bytes, cache_bytes))
    if rule["dataset_sha256"] != hashlib.sha256(reference_bytes).hexdigest():
        raise ValueError("Frozen rule does not match reference dataset")
    if reference["cache_sha256"] != hashlib.sha256(cache_bytes).hexdigest():
        raise ValueError("Cache does not match reference replay")
    candles = [Candle(**record) for record in cached["data"]["candles"]]
    validate_candles(candles)
    cutoff = reference["end"]
    if candles[-1].time + 900 != cutoff:
        raise ValueError("Cache boundary does not match reference")
    adapter = MexcFuturesAdapter()
    try:
        now = adapter.server_time()
        end = now // 900 * 900
        cursor = cutoff
        while cursor < end:
            stop = min(end, cursor + 8000 * 900)
            candles.extend(adapter.history(cursor, stop, now))
            cursor = stop
    finally:
        adapter.close()
    validate_candles(candles)
    replay = run_replay(candles, now=now)
    threshold = rule["split"]["atr_percent_threshold"]
    report = {
        "version": "mtf_new_data_check_v1",
        "observation_only": True,
        "reference_sha256": hashlib.sha256(reference_bytes).hexdigest(),
        "rule_sha256": hashlib.sha256(rule_bytes).hexdigest(),
        "atr_percent_threshold": threshold,
        "new_closed_bars": (end - cutoff) // 900,
        "start_utc": datetime.fromtimestamp(cutoff, timezone.utc).isoformat(),
        "end_utc": datetime.fromtimestamp(end, timezone.utc).isoformat(),
        "assumptions": replay["assumptions"],
        "limitations": [
            "Historical extension after original dataset, not prospective paper trading",
            "Only resolved new confirmations enter metrics; pending samples remain in audit",
            "Funding excluded; sample sums are not portfolio returns",
        ],
        **evaluate_new_samples(replay["samples"], cutoff, threshold),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    temporary.write_text(json.dumps(report, indent=2) + "\n")
    temporary.replace(args.output)
    print(json.dumps({key: value for key, value in report.items() if key != "new_samples"}, indent=2))


if __name__ == "__main__":
    main()
