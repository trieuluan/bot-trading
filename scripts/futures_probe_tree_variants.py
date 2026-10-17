"""Exploratory rule variants on cached MEXC candles; no orders or learner writes."""

import copy
import json
import pickle
from collections import OrderedDict
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from backend.app import futures_history, futures_rule_tree
from backend.app.futures_strategies import genome_name, validate_genome
from backend.app.futures_symbols import SYMBOLS
from backend.app.mexc import MexcFuturesAdapter

CACHE_FILE = Path("/tmp/mexc_10pairs_340d.pkl")
OUTPUT = Path("research-output/futures-tree-final-variants-340d.json")
HISTORICAL_STATS = futures_history.historical_stats
VARIANTS = ((3, "H4", 20), (5, "H4", 20), (8, "H4", 20),
            (3, "H1", 20), (5, "H1", 20), (8, "H1", 20),
            (5, "H4", 15), (5, "H1", 15))


def candidates():
    previous = json.loads(Path("research-output/futures-rule-tree-340d.json").read_text())
    root = next(row["genome"] for row in previous["BTC_USDT"]["strategies"]
                if row["strategy"] == "rule_tree:dcea29b41697")
    result, labels = {}, {}
    for within, frame, lookback in VARIANTS:
        genome = copy.deepcopy(root)
        genome["lookback"] = lookback
        genome["rule"]["within"] = within
        genome["rule"]["children"][0]["children"][1]["frame"] = frame
        validate_genome(genome)
        name = genome_name(genome)
        result[name] = {"genome": genome}
        labels[name] = f"{frame}_w{within}_lb{lookback}"
    return result, labels


@contextmanager
def cached_frames():
    """Single immutable, contiguous symbol dataset per replay; cache is never shared."""
    original = futures_rule_tree.frame_rows
    cache = OrderedDict()

    def frames(candles, frame):
        if frame == "M15" or not candles:
            return original(candles, frame)
        key = (candles[0].time, candles[-1].time, len(candles), frame)
        if key not in cache:
            cache[key] = original(candles, frame)
            if len(cache) > 256:
                cache.popitem(last=False)
        return cache[key]

    with patch.object(futures_rule_tree, "frame_rows", frames):
        yield


def detailed_stats(samples):
    result = HISTORICAL_STATS(samples)
    values = [sample["net_r"] for sample in samples if sample["status"] == "resolved"]
    result["gross_gain_r"] = sum(max(value, 0) for value in values)
    result["gross_loss_r"] = -sum(min(value, 0) for value in values)
    return result


def pooled(rows):
    count = sum(row["resolved"] for row in rows)
    gains = sum(row["gross_gain_r"] for row in rows)
    losses = sum(row["gross_loss_r"] for row in rows)
    return {"resolved": count, "mean_r": (gains - losses) / count if count else None,
            "profit_factor": gains / losses if losses else None}


def save(report):
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    temporary = OUTPUT.with_suffix(".tmp")
    temporary.write_text(json.dumps(report, indent=2, allow_nan=False))
    temporary.replace(OUTPUT)


def main():
    population, labels = candidates()
    if CACHE_FILE.exists():
        with CACHE_FILE.open("rb") as stream:
            cache = pickle.load(stream)
    else:
        cache = {}
        for symbol in SYMBOLS:
            adapter = MexcFuturesAdapter(symbol=symbol)
            try:
                now = adapter.server_time()
                end = now // 900 * 900
                start = end - 340 * 86400
                beginning = start - futures_history.WARMUP * 900
                cache[symbol] = {
                    "start": start, "end": end,
                    "candles": futures_history.load_history(adapter, beginning, end, now),
                    "fair": futures_history.load_history(adapter, beginning, end, now, fair=True),
                    "funding": adapter.funding_history(beginning - 2 * 86400, end),
                }
            finally:
                adapter.close()
        with CACHE_FILE.open("wb") as stream:
            pickle.dump(cache, stream)
    report = {"scope": "exploratory_variants_not_new_independent_data",
              "variant_labels": labels, "symbols": {}, "errors": {}}
    for symbol in SYMBOLS:
        data = cache[symbol]
        print(symbol, "replay", flush=True)
        try:
            with cached_frames(), patch.object(futures_history, "historical_stats", detailed_stats):
                result = futures_history.replay(
                    population, data["candles"], data["fair"], data["funding"],
                    data["start"], data["end"])
            report["symbols"][symbol] = result
            print(symbol, "done", flush=True)
        except Exception as error:
            report["errors"][symbol] = str(error)
            print(symbol, "ERROR", error, flush=True)
        save(report)
    report["summary"] = {}
    for name, label in labels.items():
        rows = [next(row for row in result["strategies"] if row["strategy"] == name)
                for result in report["symbols"].values()]
        totals = {part: pooled([row[part] for row in rows]) for part in ("train", "validation")}
        totals["eligible_pairs"] = sum(row["prior_eligible"] for row in rows)
        report["summary"][label] = totals
        print(label, totals, flush=True)
    save(report)
    if report["errors"]:
        raise RuntimeError(report["errors"])
    print("DONE", flush=True)


if __name__ == "__main__":
    main()
