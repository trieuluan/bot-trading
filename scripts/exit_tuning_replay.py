"""Local paired exit-tuning replay on archived signals; no DB or VPS writes.

Run: /private/tmp/btvenv3/bin/python -m scripts.exit_tuning_replay
Signals may overlap; this isolates exit-rule deltas on fixed archived entries.
"""

import bisect
import hashlib
import json
import math
import random
from collections import OrderedDict, defaultdict
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from statistics import fmean

from backend.app import futures_exits as exits
from backend.app import futures_learner as learner
from backend.app.futures_strategies import strategy_setup
from backend.app.futures_symbols import symbol_scope
from scripts.entry_timing_paired import measured_finish, simulate
from scripts.futures_replay_340d import setup_memo

DATA = Path("research-output/futures-nov01/mexc_10pairs_340d_compare.pkl")
COHORT = Path("research-output/futures-gene-ablation.json")
MANIFEST = COHORT.with_suffix(".manifest.json")
OUTPUT = Path("research-output/exit-tuning-replay.json")
BASES = {"exit_0": 0, "exit_1": 1, "dryup": 11, "cdir_10": 10, "cdir_23": 23}


def metrics(rows):
    if not rows:
        return {"n": 0}
    values = [r["net_r"] for r in rows]
    losses = -sum(v for v in values if v < 0)
    reasons = defaultdict(int)
    for row in rows:
        reasons[row.get("exit_reason") or row["status"]] += 1
    return dict(n=len(rows), mean_r=fmean(values), total_r=sum(values),
                pf=sum(v for v in values if v > 0) / losses if losses > 0 else None,
                win_rate=sum(1 for v in values if v > 0) / len(values),
                raw_gross_r=fmean(r["raw_gross_r"] for r in rows),
                fee_r=fmean(r["fee_r"] for r in rows), slip_r=fmean(r["slip_r"] for r in rows),
                funding_r=fmean(r["funding_r"] for r in rows), reasons=dict(reasons))


def paired_ci(rows, baseline):
    base = {r["key"]: r for r in baseline}
    weeks = defaultdict(list)
    for row in rows:
        if row["key"] in base:
            weeks[row["time"] // (7 * 86400)].append(row["net_r"] - base[row["key"]]["net_r"])
    if not weeks:
        return None
    blocks, rng, draws = list(weeks.values()), random.Random(42), []
    for _ in range(1000):
        picked = [rng.choice(blocks) for _ in blocks]
        draws.append(sum(map(sum, picked)) / sum(map(len, picked)))
    draws.sort()
    return [draws[25], draws[974]]


def custom_partial_size(position, candles, partial_always=False):
    if partial_always and candles and position.get("exit_policy") == "adaptive":
        direction = 1 if position["side"] == "long" else -1
        progress = direction * (candles[-1].close - position["entry"]) / position["risk"]
        if not (position.get("partial_exits") or []) and progress >= 1.0:
            copied = {**position, "exit_policy": "runner",
                      "exit_params": {"partial_r": 1.0, "partial_fraction": 0.5, "atr_multiple": 3.0}}
            return exits.partial_size(copied, candles)
    return exits.partial_size(position, candles)


def custom_protective_stop(position, candles, fee, slip, be_trigger_r=1.0):
    if be_trigger_r == 1.0 or position.get("exit_policy") not in ("adaptive", "trail"):
        return exits.protective_stop(position, candles, fee, slip)
    stop = exits.protective_stop(position, candles, fee, slip)
    if not candles:
        return stop
    entry, risk = position["entry"], position["risk"]
    direction = 1 if position["side"] == "long" else -1
    close = candles[-1].close
    profit_r = direction * (close - entry) / risk
    if not be_trigger_r <= profit_r < 1.0:
        return stop
    quantity = position.get("quantity", 1.0)
    costs = (position.get("entry_fee", entry * quantity * fee)
             + position.get("funding_cost", 0)) / quantity
    proposed = (direction * entry + costs) / (direction - fee) / (1 - direction * slip)
    if proposed <= 0 or not direction * (close - proposed) > 0:
        return stop
    return max(stop, proposed) if direction == 1 else min(stop, proposed)


def run_grid(configs=None, output=OUTPUT, exit_overrides=None, simulate_trade=simulate, notes=None):
    raw = json.loads(MANIFEST.read_text())
    if hashlib.sha256(DATA.read_bytes()).hexdigest() != raw["data_sha256"]:
        raise ValueError("Dataset differs from archived cohort")
    import pickle
    mexc = pickle.loads(DATA.read_bytes())
    first = max(value[2][0].time for value in mexc.values())
    series = {s: [b for b in v[2] if b.time >= first] for s, v in mexc.items()}
    split = int(datetime.fromisoformat(raw["split_utc"]).timestamp())
    end = min(rows[-1].time for rows in series.values()) + 900

    cohort = [r for r in json.loads(COHORT.read_text())
              if r["arm"] in BASES and r["var"] == ("adaptive" if r["arm"].startswith("exit_") else "orig")]
    cohort_by_sym = defaultdict(list)
    for r in cohort:
        cohort_by_sym[r["sym"]].append(r)

    configs = configs or [
        # label, max_hold_bars, be_trigger_r, partial_always
        ("baseline_48h_be1.0_weak", 192, 1.00, False),
        ("no_max_hold_be1.0_weak", 999999, 1.00, False),  # live VPS behavior!
        ("hold24h_be1.0_weak", 96, 1.00, False),
        ("hold48h_be0.75_weak", 192, 0.75, False),
        ("hold48h_be0.80_weak", 192, 0.80, False),
        ("hold48h_be0.75_always", 192, 0.75, True),
        ("hold48h_be0.80_always", 192, 0.80, True),
        ("hold48h_be1.0_always", 192, 1.00, True),
    ]

    setup_memo()
    original_aggregate = exits.aggregate
    original_finish = learner.finish
    original_max_hold = learner.MAX_HOLD
    original_prot = learner.protective_stop
    original_part = learner.partial_size
    original_discretionary = learner.discretionary_exit

    results = {cfg[0]: {"train": [], "validation": []} for cfg in configs}
    excluded = {cfg[0]: {"train": 0, "validation": 0} for cfg in configs}
    prepared = {}
    for sym, signals in cohort_by_sym.items():
        rows = series[sym]
        positions = {b.time: idx for idx, b in enumerate(rows)}
        fair = [b for b in mexc[sym][3] if b.time >= first]
        funding = mexc[sym][4]
        times = [r["time"] for r in funding]
        if [b.time for b in fair] != [b.time for b in rows]:
            raise ValueError("Trade/mark timestamps differ")

        @lru_cache(maxsize=512)
        def history(index, rows=rows):
            return rows[max(0, index - learner.WARMUP + 1):index + 1]

        items = []
        for order, record in enumerate(signals):
            index = positions[record["t"]]
            genome = raw["candidates"][record["arm"] + ":" + record["var"]]["genome"]
            peers = {name: values[index - learner.WARMUP + 1:index + 1]
                     for name, values in series.items() if name != sym}
            high = bisect.bisect_right(times, rows[index].time)
            with symbol_scope(sym):
                setup = strategy_setup(genome, history(index), funding[:high], benchmarks=peers,
                                       btc_candles=peers.get("BTC_USDT"))
            if setup is None or setup.side != record["side"]:
                raise ValueError(f"Cannot reproduce signal: {record}")
            sample = dict(status="open", entry=None, side=setup.side, stop=setup.stop,
                          target=setup.target, exit_policy=genome.get("exit_policy", "fixed"),
                          exit_params=genome.get("exit_params"), exit_rule=genome["rule"])
            items.append(((sym, record["arm"], record["t"], order), record, index, sample))
        prepared[sym] = (rows, fair, funding, times, history, items)

    def finish(sample, direction, price, why, bar):
        measured_finish(sample, direction, price, why, bar, original_finish)

    learner.finish = finish
    try:
        for sym, (rows, fair, funding, times, history, items) in prepared.items():
            cached = OrderedDict()

            def aggregate(candles, seconds, *, until=None):
                """Replay-local immutable series, one symbol at a time."""
                if not candles:
                    return original_aggregate(candles, seconds, until=until)
                key = candles[0].time, candles[-1].time, len(candles), seconds, until
                if key not in cached:
                    cached[key] = original_aggregate(candles, seconds, until=until)
                    if len(cached) > 1024:
                        cached.popitem(last=False)
                else:
                    cached.move_to_end(key)
                return cached[key]

            exits.aggregate = aggregate
            for key, record, index, sample in items:
                for label, max_hold, be_r, part_always in configs:
                    learner.MAX_HOLD = max_hold * 900
                    learner.protective_stop = lambda pos, c, f, s, b=be_r: custom_protective_stop(pos, c, f, s, b)
                    learner.partial_size = lambda pos, c, pa=part_always: custom_partial_size(pos, c, pa)
                    learner.discretionary_exit = (exit_overrides or {}).get(label, original_discretionary)
                    boundary = split if record["part"] == "train" else end
                    sim = simulate_trade(sample, rows, fair, funding, times, index + 1, boundary, history)
                    if label == "baseline_48h_be1.0_weak" and (
                            sim["status"] != "resolved" or not math.isclose(sim["net_r"], record["r"], abs_tol=1e-9)):
                        raise ValueError(f"Baseline replay mismatch: {record}; replay={sim}")
                    sim.update(key=key, time=record["t"])
                    if sim["status"] == "resolved":
                        results[label][record["part"]].append(sim)
                    else:
                        excluded[label][record["part"]] += 1
            print(f"{sym}: all {len(configs)} configurations done on {len(items)} signals", flush=True)
    finally:
        learner.finish = original_finish
        learner.MAX_HOLD = original_max_hold
        learner.protective_stop = original_prot
        learner.partial_size = original_part
        learner.discretionary_exit = original_discretionary
        exits.aggregate = original_aggregate

    summary = {}
    baseline = results["baseline_48h_be1.0_weak"]
    for label, parts in results.items():
        summary[label] = {
            "train": {**metrics(parts["train"]), "excluded": excluded[label]["train"],
                      "delta_vs_baseline_ci95": paired_ci(parts["train"], baseline["train"])},
            "validation": {**metrics(parts["validation"]), "excluded": excluded[label]["validation"],
                           "delta_vs_baseline_ci95": paired_ci(parts["validation"], baseline["validation"])},
        }
        for part in ("train", "validation"):
            keys = {row["key"] for row in parts[part]}
            matched = [row for row in baseline[part] if row["key"] in keys]
            summary[label][part]["matched_baseline"] = metrics(matched)
            summary[label][part]["paired_mean_delta_r"] = (
                summary[label][part]["mean_r"] - fmean(row["net_r"] for row in matched))
            base_by_key = {row["key"]: row for row in matched}
            summary[label][part]["losses_reduced_to_near_flat"] = sum(
                base_by_key[row["key"]]["net_r"] <= -0.75 and row["net_r"] > -0.25
                for row in parts[part])
            summary[label][part]["winners_turned_nonpositive"] = sum(
                base_by_key[row["key"]]["net_r"] > 0 and row["net_r"] <= 0
                for row in parts[part])
            summary[label][part]["by_symbol"] = {
                symbol: metrics([row for row in parts[part] if row["key"][0] == symbol])
                for symbol in sorted(cohort_by_sym)}
        t, v = summary[label]["train"], summary[label]["validation"]
        print(f"{label:26s} | train: mean_r={t['mean_r']:+.4f} pf={t['pf']:.3f} win={t['win_rate']:.3f} | val: mean_r={v['mean_r']:+.4f} pf={v['pf']:.3f} win={v['win_rate']:.3f} total={v['total_r']:+.1f}")

    output.write_text(json.dumps(dict(
        data_sha256=raw["data_sha256"],
        code_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        split_utc=raw["split_utc"],
        limitations=notes or [
            "Archived 1,532 resolved 5-seed signal cohort; signals overlap and are not a portfolio replay",
            "no_max_hold_be1.0_weak still truncates at train/dataset boundaries, biasing long holds at boundaries",
            "Only adaptive/trail breakeven and adaptive partial rules vary; non-adaptive seeds stay unchanged",
        ],
        configs=summary,
    ), indent=2, allow_nan=False) + "\n")


if __name__ == "__main__":
    run_grid()
