"""Local matched G0 replay. No DB writes, evolution, or portfolio simulation."""

import argparse
import copy
import hashlib
import json
import time
from collections import Counter
from multiprocessing import Pool
from pathlib import Path

from backend.app import futures_history, futures_rule_tree
from backend.app.futures_learner import WARMUP
from backend.app.futures_strategies import RULE_SEEDS, validate_genome
from backend.app.futures_symbols import SYMBOLS, symbol_scope
from scripts.futures_h4_variants import load, summarize
from scripts.futures_replay_340d import setup_memo

ARMS = ("ema_only", "consensus")


def candidates():
    result = {}
    for arm in ARMS:
        for index, seed in enumerate(RULE_SEEDS):
            genome = copy.deepcopy(seed)
            if arm == "ema_only":
                genome["rule"]["children"][0] = {"feature": "trend", "frame": "H4"}
            validate_genome(genome)
            result[f"{arm}:{index:02d}"] = {"genome": genome}
    return result


def run_pair(task):
    symbol, data, destination = task
    setup_memo()
    raw, first, last, clip = load(data)
    series = {name: clip(raw[name][2]) for name in SYMBOLS}
    captured = []
    original_stats = futures_history.historical_stats
    original_h4 = futures_rule_tree.h4_matches
    cache = {}
    cache_stamp = None

    def cached_h4(feature, rows, candles, direction, context):
        nonlocal cache_stamp
        stamp = candles[-1].time
        if stamp != cache_stamp:
            cache.clear()
            cache_stamp = stamp
        key = (feature, direction, tuple(bar.time for bar in rows))
        if key not in cache:
            cache[key] = original_h4(feature, rows, candles, direction, context)
        return cache[key]

    def capture(samples, cutoff=None):
        captured.append(samples)
        return original_stats(samples, cutoff)

    started = time.monotonic()
    population = candidates()
    futures_history.historical_stats = capture
    futures_rule_tree.h4_matches = cached_h4
    try:
        with symbol_scope(symbol):
            result = futures_history.replay(
                population, series[symbol], clip(raw[symbol][3]), raw[symbol][4],
                max(raw[symbol][0], first + WARMUP * 900), last + 900,
                progress=lambda done, total: print(
                    f"{symbol}: {done}/{total}", flush=True
                ) if done % (96 * 30) == 0 else None,
                btc_candles=series["BTC_USDT"] if symbol != "BTC_USDT" else None,
                benchmarks={name: rows for name, rows in series.items() if name != symbol},
            )
    finally:
        futures_history.historical_stats = original_stats
        futures_rule_tree.h4_matches = original_h4
    rows = []
    excluded = Counter()
    for index, name in enumerate(population):
        arm, template = name.split(":")
        for offset, part in enumerate(("train", "validation")):
            for sample in captured[index * 2 + offset]:
                if sample["status"] == "resolved":
                    rows.append((symbol, arm, part, template, sample["candle_time"],
                                 sample["side"], sample["net_r"], sample["exit_reason"]))
                else:
                    reason = sample.get("exit_reason", sample.get("reason", "unknown"))
                    excluded[f"{arm}:{part}:{reason}"] += 1
    payload = {"symbol": symbol, "seconds": time.monotonic() - started,
               "report": result, "rows": rows, "excluded": dict(excluded)}
    path = Path(destination) / f"{symbol}.json"
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, allow_nan=False))
    temporary.replace(path)
    print(f"{symbol}: DONE {payload['seconds']:.1f}s, {len(rows)} resolved", flush=True)
    return symbol


def summary(payloads):
    rows = [row for payload in payloads for row in payload["rows"]]
    result = {}
    for arm in ARMS:
        result[arm] = {}
        for part in ("train", "validation"):
            selected = [row for row in rows if row[1] == arm and row[2] == part]
            stats = summarize(selected)
            stats["by_symbol"] = {
                symbol: summarize([row for row in selected if row[0] == symbol])
                for symbol in SYMBOLS
            }
            stats["by_template"] = {
                f"{index:02d}": summarize([row for row in selected if row[3] == f"{index:02d}"])
                for index in range(len(RULE_SEEDS))
            }
            stats["exit_reasons"] = dict(Counter(row[7] for row in selected))
            stats["sides"] = {
                side: summarize([row for row in selected if row[5] == side])
                for side in ("long", "short")
            }
            stats["eligible_priors"] = sum(
                strategy["prior_eligible"]
                for payload in payloads for strategy in payload["report"]["strategies"]
                if strategy["strategy"].startswith(f"{arm}:")
            )
            result[arm][part] = stats
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=5, choices=range(1, 11))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    identity = {"candidates": candidates(), "source_sha256": hashlib.sha256(
        (args.data / "mexc_10pairs_340d_compare.pkl").read_bytes()).hexdigest(),
        "code_sha256": {str(path): hashlib.sha256(path.read_bytes()).hexdigest()
                        for path in [Path(__file__), *Path("backend/app").glob("*.py"),
                                     Path("scripts/futures_h4_variants.py"),
                                     Path("scripts/futures_replay_340d.py")]}}
    manifest = args.output / "manifest.json"
    if manifest.exists() and json.loads(manifest.read_text()) != identity:
        raise ValueError("Checkpoint inputs/code changed; choose a new output directory")
    manifest.write_text(json.dumps(identity, sort_keys=True, indent=2))
    pending = [(symbol, str(args.data), str(args.output)) for symbol in SYMBOLS
               if not (args.output / f"{symbol}.json").exists()]
    with Pool(args.workers) as pool:
        for _ in pool.imap_unordered(run_pair, pending):
            pass
    payloads = [json.loads((args.output / f"{symbol}.json").read_text()) for symbol in SYMBOLS]
    (args.output / "summary.json").write_text(json.dumps(summary(payloads), indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
