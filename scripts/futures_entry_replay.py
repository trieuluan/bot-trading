"""Matched G0 signal-cohort entry replay. Research only; no DB or live changes."""

import argparse
import copy
import hashlib
import json
import math
import time
from collections import Counter, defaultdict
from multiprocessing import Pool
from pathlib import Path

from backend.app.futures_entries import ENTRY_POLICIES, WAIT_BARS, entry_level, wait
from backend.app.futures_learner import SLIP, WARMUP, advance
from backend.app.futures_strategies import RULE_SEEDS, strategy_setup
from backend.app.futures_symbols import SYMBOLS, symbol_scope
from backend.app.mexc import validate_candles
from scripts.futures_h4_variants import load, summarize
from scripts.futures_replay_340d import setup_memo

ARMS = ENTRY_POLICIES


def new_sample(name, setup, genome, stamp, reference_risk):
    return {"strategy": name, "candle_time": stamp, "entry_time": stamp + 900,
            "side": setup.side, "stop": setup.stop, "target": setup.target,
            "status": "open", "entry": None, "net_r": None, "label_available_at": None,
            "exit_policy": genome.get("exit_policy", "fixed"),
            "exit_params": genome.get("exit_params"), "exit_rule": genome["rule"],
            "reference_risk": reference_risk, "first_entry_time": stamp + 900}


def advance_entry(sample, arm, bar, mark, funding, history):
    if sample["status"] != "open":
        return
    if sample["entry"] is None and arm != "market" and not sample.get("confirmed"):
        sample["entry_policy"] = arm
        filled = wait(sample, bar, funding)
        if filled is None:
            return
        bar, funding = filled
    advance(sample, bar, mark, funding, history)

def run_pair(task):
    symbol, data, destination = task
    setup_memo()
    raw, first, last, clip = load(data)
    series = {name: clip(raw[name][2]) for name in SYMBOLS}
    candles, fair, funding = series[symbol], clip(raw[symbol][3]), raw[symbol][4]
    for rows in (*series.values(), fair):
        validate_candles(rows)
        if [bar.time for bar in rows] != list(range(first, last + 900, 900)):
            raise ValueError("Incomplete source coverage")
    baseline = json.loads((Path(data) / "g0-consensus" / f"{symbol}.json").read_text())
    report = baseline["report"]
    strategies = {row["strategy"]: row for row in report["strategies"]}
    for index, seed in enumerate(RULE_SEEDS):
        source = strategies[f"consensus:{index:02d}"]
        if source["genome"] != seed:
            raise ValueError("Source G0 changed")
    if any(key.split(":")[2] != "dataset_boundary" for key in baseline["excluded"]
           if key.startswith("consensus:")):
        raise ValueError("Source G0 contains censored signals away from dataset boundary")
    cohort = defaultdict(list)
    for row in baseline["rows"]:
        if row[1] == "consensus":
            cohort[row[4]].append(row)
    started = time.monotonic()
    active, samples = [], []
    stamps = {bar.time: index for index, bar in enumerate(candles)}
    expected = sum(strategies[f"consensus:{index:02d}"][part]["resolved"]
                   for index in range(len(RULE_SEEDS)) for part in ("train", "validation"))
    if sum(map(len, cohort.values())) != expected:
        raise ValueError("Incomplete source cohort")
    with symbol_scope(symbol):
        for index in range(WARMUP, len(candles)):
            bar, mark = candles[index], fair[index]
            if bar.time == report["train_before"]:
                for sample in active:
                    sample.update(status="excluded", exit_reason="split_boundary")
                active.clear()
            if not active and bar.time not in cohort:
                continue
            history = candles[index - WARMUP + 1:index + 1]
            known = [row for row in funding if row["time"] <= bar.time]
            for sample in active:
                advance_entry(sample, sample["arm"], bar, mark, known, history)
            active = [sample for sample in active if sample["status"] == "open"]
            for row in cohort.get(bar.time, []):
                _, _, part, template, stamp, side, expected_r, _ = row
                genome = RULE_SEEDS[int(template)]
                peers = {name: rows[index - WARMUP + 1:index + 1]
                         for name, rows in series.items() if name != symbol}
                setup = strategy_setup(genome, history, known, benchmarks=peers,
                    btc_candles=peers.get("BTC_USDT"), fair_candles=[mark])
                if setup is None or setup.side != side:
                    raise ValueError(f"Source setup mismatch: {symbol}/{template}/{stamp}")
                direction = 1 if side == "long" else -1
                ref = direction * (candles[stamps[stamp] + 1].open * (1 + direction * SLIP) - setup.stop)
                if ref <= 0:
                    raise ValueError("Invalid source market risk")
                for arm in ARMS:
                    sample = new_sample(f"{arm}:{template}", setup, genome, stamp, ref)
                    sample.update(symbol=symbol, arm=arm, part=part, template=template,
                                  expected_market_r=expected_r)
                    if arm != "market":
                        sample["level"] = entry_level(arm, history, side, setup.stop)
                        if sample["level"] is None:
                            sample.update(status="cancelled", exit_reason="no_valid_level")
                    samples.append(sample)
                    if sample["status"] == "open":
                        active.append(sample)
    for sample in active:
        sample.update(status="excluded", exit_reason="dataset_boundary")
    for sample in samples:
        if sample["arm"] == "market" and (sample["status"] != "resolved"
                or not math.isclose(sample["net_r"], sample["expected_market_r"], abs_tol=1e-10)):
            raise ValueError("Market baseline failed to reproduce")
    payload = {"symbol": symbol, "seconds": time.monotonic() - started,
               "start": report["start_time"], "end": report["end_time"],
               "split": report["train_before"], "samples": samples,
               "source_censored": {key: count for key, count in baseline["excluded"].items()
                                   if key.startswith("consensus:")}}
    path = Path(destination) / f"{symbol}.json"
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, allow_nan=False))
    temporary.replace(path)
    print(f"{symbol}: DONE {payload['seconds']:.1f}s, {expected} signals, market exact", flush=True)
    return symbol


def row(sample, reference=False):
    value = sample["net_r"]
    if reference:
        value *= sample["risk"] / sample["reference_risk"]
    return (sample["symbol"], sample["arm"], sample["part"], sample["template"],
            sample["candle_time"], sample["side"], value, sample["exit_reason"])


def stats(samples):
    resolved = [sample for sample in samples if sample["status"] == "resolved"]
    cancelled = [sample for sample in samples if sample["status"] == "cancelled"]
    censored = [sample for sample in samples if sample["status"] == "excluded"]
    scored = len(resolved) + len(cancelled)
    result = summarize([row(sample) for sample in resolved])
    result.update(signals=len(samples), filled=sum(sample["entry"] is not None for sample in samples),
                  cancelled=len(cancelled), censored=len(censored), scorable_signals=scored,
                  fill_rate=sum(sample["entry"] is not None for sample in samples) / len(samples) if samples else None,
                  reasons=dict(Counter(sample["exit_reason"] for sample in samples)))
    reference = summarize([row(sample, reference=True) for sample in resolved])
    result["reference_r"] = reference
    result["reference_r_per_signal"] = reference.get("total_r", 0) / scored if scored else None
    result["native_r_per_signal"] = result.get("total_r", 0) / scored if scored else None
    result["missed_market_r"] = summarize([
        (sample["symbol"], sample["arm"], sample["part"], sample["template"], sample["candle_time"],
         sample["side"], sample["expected_market_r"], sample["exit_reason"])
        for sample in cancelled])
    return result


def summary(payloads):
    samples = [sample for payload in payloads for sample in payload["samples"]]
    result = {}
    for arm in ARMS:
        result[arm] = {}
        for part in ("train", "validation"):
            selected = [sample for sample in samples if sample["arm"] == arm and sample["part"] == part]
            entry = stats(selected)
            entry["by_symbol"] = {symbol: stats([sample for sample in selected if sample["symbol"] == symbol])
                                  for symbol in SYMBOLS}
            result[arm][part] = entry
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=5, choices=range(1, 11))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    source = json.loads((args.data / "g0-consensus" / "manifest.json").read_text())
    files = [Path(__file__), *Path("backend/app").glob("*.py"),
             Path("scripts/futures_h4_variants.py"), Path("scripts/futures_replay_340d.py")]
    hashes = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in files}
    for path, digest in source["code_sha256"].items():
        if path.startswith("backend/app/") and hashes.get(path) != digest:
            raise ValueError(f"Source engine changed: {path}")
    cache_hash = hashlib.sha256((args.data / "mexc_10pairs_340d_compare.pkl").read_bytes()).hexdigest()
    if cache_hash != source["source_sha256"]:
        raise ValueError("Source candle cache changed")
    identity = {"arms": ARMS, "wait_bars": WAIT_BARS, "source_sha256": cache_hash,
                "candidates": copy.deepcopy(RULE_SEEDS), "code_sha256": hashes,
                "source_cohort_sha256": {symbol: hashlib.sha256(
                    (args.data / "g0-consensus" / f"{symbol}.json").read_bytes()).hexdigest()
                    for symbol in SYMBOLS},
                "fill_model": "strict trade-through, limit-price fill, taker fees, no entry slippage for limits; "
                              "market/retest next-open with adverse slippage, exit slippage for all; "
                              "stop-first, ambiguous fill-bar target not credited unless close beyond target",
                "cohort": "fixed G0 market signals; independent shadow labels, not rescheduled portfolio"}
    manifest = args.output / "manifest.json"
    if manifest.exists() and json.loads(manifest.read_text()) != json.loads(json.dumps(identity)):
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
