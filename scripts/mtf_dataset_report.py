"""Descriptive chronological ATR check; never changes trading configuration."""

import argparse
import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from statistics import median


def metrics(samples):
    values = [sample["evaluation"]["net_r_ex_funding"] for sample in samples]
    return {
        "samples": len(samples),
        "target_hits": sum(sample["evaluation"]["outcome"] == "target" for sample in samples),
        "sum_net_r_ex_funding": sum(values),
        "mean_net_r_ex_funding": sum(values) / len(values) if values else None,
    }


def build_report(data):
    if data.get("provenance") != "historical":
        raise ValueError("Expected a historical replay dataset")
    samples = sorted(data["samples"], key=lambda sample: sample["confirm_time"])
    if len(samples) < 4 or len({sample["confirm_time"] for sample in samples}) != len(samples):
        raise ValueError("Need at least four unique samples")
    for sample in samples:
        features, evaluation = sample["features"], sample["evaluation"]
        if evaluation["status"] != "resolved":
            raise ValueError("Unresolved sample: report requires complete outcomes")
        if features["available_at"] > evaluation["entry_time"]:
            raise ValueError("Entry precedes feature availability")
        if evaluation["label_available_at"] <= evaluation["entry_time"]:
            raise ValueError("Outcome precedes entry")
        if any(not math.isfinite(value) for value in (
            features["atr_percent"], evaluation["net_r_ex_funding"],
        )) or features["atr_percent"] <= 0:
            raise ValueError("Invalid ATR or return")
    split = int(len(samples) * 0.6)
    cutoff = samples[split]["features"]["available_at"]
    train = [sample for sample in samples[:split]
             if sample["evaluation"]["label_available_at"] < cutoff]
    if not train:
        raise ValueError("No training labels available before cutoff")
    test = samples[split:]
    threshold = median(sample["features"]["atr_percent"] for sample in train)
    groups = {}
    for sample in samples:
        month = datetime.fromtimestamp(sample["evaluation"]["entry_time"], timezone.utc).strftime("%Y-%m")
        groups.setdefault(month, []).append(sample)
    return {
        "version": "mtf_report_v1",
        "exploratory_only": True,
        "limitations": [
            "Dataset already inspected: later segment is not an untouched holdout",
            "Sample outcomes may overlap; summed R is not portfolio performance",
            "Funding excluded; fees and slippage follow replay assumptions",
            "Small sample; no trading configuration changes authorized by this report",
        ],
        "assumptions": data["assumptions"],
        "overall": metrics(samples),
        "monthly": {month: metrics(rows) for month, rows in sorted(groups.items())},
        "sides": {side: metrics([sample for sample in samples if sample["features"]["side"] == side])
                  for side in ("long", "short")},
        "split": {"cutoff_utc": datetime.fromtimestamp(cutoff, timezone.utc).isoformat(),
                  "purged_train_samples": split - len(train), "atr_percent_threshold": threshold},
        "segments": {name: {
            "baseline": metrics(rows),
            "atr_filtered": metrics([sample for sample in rows if sample["features"]["atr_percent"] > threshold]),
        } for name, rows in (("train", train), ("later", test))},
    }


def self_check():
    samples = [{"confirm_time": index * 900,
                "features": {"available_at": index * 900 + 900, "atr_percent": index + 1, "side": "long"},
                "evaluation": {"status": "resolved", "entry_time": index * 900 + 900,
                               "label_available_at": index * 900 + 1800,
                               "net_r_ex_funding": -1, "outcome": "stop"}}
               for index in range(10)]
    report = build_report({"provenance": "historical", "samples": samples, "assumptions": {}})
    assert report["split"]["purged_train_samples"] == 1
    assert report["split"]["atr_percent_threshold"] == 3
    assert report["segments"]["later"]["atr_filtered"]["samples"] == 4
    assert report["overall"]["sum_net_r_ex_funding"] == -10


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=Path("research-output/mtf-historical-samples.json"))
    parser.add_argument("--output", type=Path, default=Path("research-output/mtf-report.json"))
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args()
    if args.self_check:
        self_check()
        print("Self-check passed")
        return
    if args.input.resolve() == args.output.resolve():
        parser.error("Output must not overwrite input")
    raw = args.input.read_bytes()
    report = build_report(json.loads(raw))
    report["dataset_sha256"] = hashlib.sha256(raw).hexdigest()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    temporary.write_text(json.dumps(report, indent=2) + "\n")
    temporary.replace(args.output)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
