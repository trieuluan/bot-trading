"""Local counter-trend/range-reversal replay. No DB writes, evolution, or portfolio simulation."""

import argparse
import hashlib
import json
import time
from collections import Counter
from multiprocessing import Pool
from pathlib import Path
from statistics import fmean

from backend.app import futures_history, futures_strategies
from backend.app.futures_exits import h1_view
from backend.app.futures_learner import WARMUP
from backend.app.futures_rule_tree import adx_state, frame_rows
from backend.app.futures_rules import ema50
from backend.app.futures_strategies import validate_genome
from backend.app.futures_symbols import SYMBOLS, symbol_scope
from scripts.futures_h4_variants import load, summarize
from scripts.futures_replay_340d import setup_memo

ARMS = ("counter", "aligned", "counter_mr", "aligned_mr")
MR_EXIT = {"max_bars": 8, "min_progress_r": 0.5}
MIN_REWARD_R = 1.0
TEMPLATES = {
    "A": (("sweep", "H1"), ("pin_bar", "M15")),
    "B": (("bb_pierce", "H1"), ("engulfing", "M15")),
    "C": (("rsi_divergence", "H1"), ("pin_bar", "M15")),
}

def h4_gate(rows, direction, opposed):
    if len(rows) < 50:
        return False
    state = adx_state(rows)
    if state is None or not 0 <= state[0] < 20:
        return False
    delta = direction * (rows[-1].close - ema50(rows))
    return delta < 0 if opposed else delta > 0

def mr_levels(side, candles, target_r=2.0, stop_buffer=0.0):
    hours, atr = h1_view(candles)
    if atr is None or len(hours) < 20:
        return None
    direction = 1 if side == "long" else -1
    entry = candles[-1].close
    swing = (min(bar.low for bar in hours[-5:]) if direction == 1
             else max(bar.high for bar in hours[-5:]))
    risk = max(direction * (entry - swing) + atr * 0.25, atr) + stop_buffer * atr
    target = fmean(bar.close for bar in hours[-20:])
    if risk <= 0 or target <= 0 or direction * (target - entry) < MIN_REWARD_R * risk:
        return None
    return entry - direction * risk, target

def mean_target(real):
    def wrapped(genome, history, funding, **kwargs):
        if genome.get("exit_policy") != "time_stop":
            return real(genome, history, funding, **kwargs)
        original = futures_strategies.adaptive_levels
        futures_strategies.adaptive_levels = mr_levels
        try:
            return real(genome, history, funding, **kwargs)
        finally:
            futures_strategies.adaptive_levels = original
    return wrapped

def leaf(pair):
    return {"feature": pair[0], "frame": pair[1]}

def candidates():
    result = {}
    for arm in ARMS:
        for name, (setup, trigger) in TEMPLATES.items():
            children = [{"feature": "trend", "frame": "H4"}, leaf(setup), leaf(trigger)]
            genome = {"family": "rule_tree", "lookback": 20, "target_r": 2.0,
                      "stop_buffer": 0.0, "max_er": 0.30, "funding_threshold": 0.0003,
                      "exit_policy": "adaptive", "rule": {"op": "all", "children": children}}
            if arm.endswith("_mr"):
                genome.update(exit_policy="time_stop", exit_params=dict(MR_EXIT))
            validate_genome(genome)
            result[f"{arm}:{name}"] = {"genome": genome}
    return result

def run_pair(task):
    symbol, data, destination = task
    setup_memo()
    raw, first, last, clip = load(data)
    series = {name: clip(raw[name][2]) for name in SYMBOLS}
    captured = []
    original_stats = futures_history.historical_stats
    original_matches = futures_strategies.matches
    original_setup = futures_history.strategy_setup

    def capture(samples):
        captured.append(samples)
        return original_stats(samples)

    started = time.monotonic()
    population = candidates()
    roots = {id(candidate["genome"]["rule"]): name.split(":")[0]
             for name, candidate in population.items()}
    cache = {}
    cache_stamp = None

    def research_matches(rule, candles, direction, lookback, anchor=None, context=None):
        nonlocal cache_stamp
        arm = roots.get(id(rule))
        if arm is None:
            return original_matches(rule, candles, direction, lookback, anchor, context)
        stamp = candles[-1].time
        if stamp != cache_stamp:
            cache.clear()
            cache_stamp = stamp
        key = (arm, direction)
        if key not in cache:
            cache[key] = h4_gate(frame_rows(candles, "H4"), direction, arm.startswith("counter"))
        return cache[key] and all(original_matches(
            child, candles, direction, lookback, anchor, context)
            for child in rule["children"][1:])

    futures_history.historical_stats = capture
    futures_strategies.matches = research_matches
    futures_history.strategy_setup = mean_target(original_setup)
    try:
        with symbol_scope(symbol):
            result = futures_history.replay(
                population, series[symbol], clip(raw[symbol][3]), raw[symbol][4],
                max(raw[symbol][0], first + WARMUP * 900), last + 900,
                progress=lambda done, total: print(f"{symbol}: {done}/{total}", flush=True)
                if done % (96 * 30) == 0 else None,
                btc_candles=series["BTC_USDT"] if symbol != "BTC_USDT" else None,
                benchmarks={name: rows for name, rows in series.items() if name != symbol},
            )
    finally:
        futures_history.historical_stats = original_stats
        futures_strategies.matches = original_matches
        futures_history.strategy_setup = original_setup
    rows, excluded = [], Counter()
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
            stats["by_symbol"] = {s: summarize([r for r in selected if r[0] == s]) for s in SYMBOLS}
            stats["by_template"] = {t: summarize([r for r in selected if r[3] == t])
                                    for t in TEMPLATES}
            stats["sides"] = {side: summarize([r for r in selected if r[5] == side])
                              for side in ("long", "short")}
            stats["exit_reasons"] = dict(Counter(r[7] for r in selected))
            stats["eligible_priors"] = sum(strategy["prior_eligible"]
                for payload in payloads for strategy in payload["report"]["strategies"]
                if strategy["strategy"].startswith(f"{arm}:"))
            result[arm][part] = stats
    return result

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=5, choices=range(1, 11))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    identity = {"signal_contract": {
        "counter": "H4 ADX14 < 20 AND opposite H4 EMA50 direction AND H1 setup AND M15 trigger",
        "aligned": "H4 ADX14 < 20 AND same H4 EMA50 direction AND H1 setup AND M15 trigger",
        "override": "Research-only root matcher; serialized H4 trend leaf is a schema placeholder",
        "exits": "base arms adaptive; *_mr arms: target=mean of last 20 closed H1 closes "
                 "(skip if reward<1R, no legacy 1.5R barrier filter), "
                 "time_stop 8h if progress<0.5R, same initial stop and native time_stop breakeven",
    }, "candidates": candidates(), "source_sha256": hashlib.sha256(
        (args.data / "mexc_10pairs_340d_compare.pkl").read_bytes()).hexdigest(),
        "code_sha256": {str(path): hashlib.sha256(path.read_bytes()).hexdigest()
                        for path in [Path(__file__), *Path("backend/app").glob("*.py"),
                                     Path("scripts/futures_h4_variants.py"),
                                     Path("scripts/futures_replay_340d.py")]}}
    manifest = args.output / "manifest.json"
    if manifest.exists() and json.loads(manifest.read_text()) != identity:
        raise ValueError("Checkpoint inputs/code changed; choose a new output directory")
    manifest.write_text(json.dumps(identity, sort_keys=True, indent=2))
    pending = [(s, str(args.data), str(args.output)) for s in SYMBOLS
               if not (args.output / f"{s}.json").exists()]
    with Pool(args.workers) as pool:
        for _ in pool.imap_unordered(run_pair, pending):
            pass
    payloads = [json.loads((args.output / f"{s}.json").read_text()) for s in SYMBOLS]
    (args.output / "summary.json").write_text(json.dumps(summary(payloads), indent=2, allow_nan=False))

if __name__ == "__main__":
    main()
