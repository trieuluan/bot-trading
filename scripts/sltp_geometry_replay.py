"""Local paired SL/TP geometry replay on archived signals; no DB or VPS writes.

Run: /private/tmp/btvenv3/bin/python -m scripts.sltp_geometry_replay
Tests structural SL (recent H1 swings + ATR buffer) and structural / fixed TP
on fixed archived signals.
"""

import bisect
import copy
import hashlib
import json
import math
from collections import OrderedDict, defaultdict
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from statistics import fmean

from backend.app import futures_exits as exits
from backend.app import futures_learner as learner
from backend.app.futures_rules import atr14
from backend.app.futures_rule_tree import frame_rows
from backend.app.futures_strategies import strategy_setup
from backend.app.futures_symbols import symbol_scope
from scripts.entry_timing_paired import measured_finish
from scripts.exit_tuning_replay import paired_ci, metrics
from scripts.futures_replay_340d import setup_memo
from scripts.structure_exit_replay import settle_boundary

DATA = Path("research-output/futures-nov01/mexc_10pairs_340d_compare.pkl")
COHORT = Path("research-output/futures-gene-ablation.json")
MANIFEST = COHORT.with_suffix(".manifest.json")
OUTPUT = Path("research-output/sltp-geometry-replay.json")
BASES = {"exit_0": 0, "exit_1": 1, "dryup": 11, "cdir_10": 10, "cdir_23": 23}


def recent_h1_swing(history, side, lookback=5):
    """Lowest low (long) or highest high (short) of last `lookback` closed H1 bars."""
    h1 = frame_rows(history, "H1")
    if len(h1) < lookback:
        return None
    prior = h1[-lookback:]
    return min(b.low for b in prior) if side == "long" else max(b.high for b in prior)


def calculate_arm_geometry(arm_name, sample, history):
    """Return new (stop, target, exit_policy, exit_params) or None if invalid."""
    side = sample["side"]
    direction = 1 if side == "long" else -1
    signal_close = history[-1].close
    atr_val = atr14(history)
    if atr_val is None or atr_val <= 0:
        return None

    stop = sample["stop"]
    target = sample["target"]
    policy = sample.get("exit_policy", "fixed")
    params = copy.deepcopy(sample.get("exit_params"))

    if arm_name == "baseline":
        return stop, target, policy, params

    if arm_name in ("swing_h1_atr025_tp2r", "swing_h1_atr050_tp2r", "swing_h1_atr100_tp2r"):
        mult = 0.25 if "025" in arm_name else (0.50 if "050" in arm_name else 1.0)
        swing = recent_h1_swing(history, side, lookback=5)
        if swing is None:
            return None
        stop = swing - direction * mult * atr_val
        risk = direction * (signal_close - stop)
        if risk <= 0:
            return None
        target = signal_close + direction * 2.0 * risk

    elif arm_name in ("fixed_tp_1.5r", "fixed_tp_2.0r", "fixed_tp_3.0r"):
        policy, params = "fixed", None
        r_mult = 1.5 if "1.5" in arm_name else (2.0 if "2.0" in arm_name else 3.0)
        risk = direction * (signal_close - stop)
        if risk <= 0:
            return None
        target = signal_close + direction * r_mult * risk

    elif arm_name in ("tp_structural", "structural_gate_control"):
        st = exits.structural_target(side, signal_close, stop, target, history)
        if st is None:
            return None
        if arm_name == "tp_structural":
            target = st

    elif arm_name == "partial_1r_be":
        policy = "runner"
        params = {"partial_r": 1.0, "partial_fraction": 0.5, "atr_multiple": 3.0}

    elif arm_name == "swing_h1_tp_structural":
        swing = recent_h1_swing(history, side, lookback=5)
        if swing is None:
            return None
        stop = swing - direction * 0.25 * atr_val
        risk = direction * (signal_close - stop)
        if risk <= 0:
            return None
        base_target = signal_close + direction * 2.0 * risk
        st = exits.structural_target(side, signal_close, stop, base_target, history)
        if st is None:
            return None
        target = st

    else:
        raise ValueError(f"Unknown arm: {arm_name}")

    if not all(math.isfinite(value) and value > 0 for value in (signal_close, stop, target)):
        return None
    if direction * (signal_close - stop) <= 0:
        return None
    if direction * (target - signal_close) <= 0:
        return None

    return stop, target, policy, params


def run_sltp_grid():
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

    arms = [
        "baseline",
        "swing_h1_atr025_tp2r",
        "swing_h1_atr050_tp2r",
        "swing_h1_atr100_tp2r",
        "fixed_tp_1.5r",
        "fixed_tp_2.0r",
        "fixed_tp_3.0r",
        "tp_structural",
        "structural_gate_control",
        "partial_1r_be",
        "swing_h1_tp_structural",
    ]

    setup_memo()
    original_aggregate = exits.aggregate
    original_finish = learner.finish
    original_max_hold = learner.MAX_HOLD
    original_prot = learner.protective_stop
    original_part = learner.partial_size
    original_discretionary = learner.discretionary_exit

    results = {arm: {"train": [], "validation": []} for arm in arms}
    excluded = {arm: {"train": 0, "validation": 0} for arm in arms}
    prepared = {}
    for sym, signals in cohort_by_sym.items():
        rows = series[sym]
        positions = {b.time: idx for idx, b in enumerate(rows)}
        fair = [b for b in mexc[sym][3] if b.time >= first]
        funding = mexc[sym][4]
        times = [r["time"] for r in funding]
        if [bar.time for bar in fair] != [bar.time for bar in rows]:
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
        print(f"{sym}: prepared {len(items)} archived signals", flush=True)

    def finish(sample, direction, price, why, bar):
        measured_finish(sample, direction, price, why, bar, original_finish)

    learner.finish = finish
    try:
        for sym, (rows, fair, funding, times, history, items) in prepared.items():
            cached = OrderedDict()

            def aggregate(candles, seconds, *, until=None):
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
            for key, record, index, base_sample in items:
                hist = history(index)
                direction = 1 if base_sample["side"] == "long" else -1
                ref_risk = direction * (rows[index + 1].open * (1 + direction * learner.SLIP)
                                        - base_sample["stop"])
                if ref_risk <= 0:
                    raise ValueError("Invalid baseline reference risk")
                for arm in arms:
                    geom = calculate_arm_geometry(arm, base_sample, hist)
                    if geom is None:
                        excluded[arm][record["part"]] += 1
                        results[arm][record["part"]].append(dict(
                            key=key, time=record["t"], status="resolved", exit_reason="no_geometry",
                            net_r=0.0, ref_net_r=0.0, raw_gross_r=0.0,
                            fee_r=0.0, slip_r=0.0, funding_r=0.0))
                        continue
                    stop, target, pol, par = geom
                    sample = copy.deepcopy(base_sample)
                    sample["stop"] = stop
                    sample["target"] = target
                    sample["exit_policy"] = pol
                    sample["exit_params"] = par

                    learner.MAX_HOLD = 192 * 900
                    learner.protective_stop = original_prot
                    learner.partial_size = original_part
                    learner.discretionary_exit = original_discretionary

                    boundary = split if record["part"] == "train" else end
                    sim = settle_boundary(sample, rows, fair, funding, times, index + 1, boundary, history)

                    if arm == "baseline" and (
                            sim["status"] != "resolved" or not math.isclose(sim["net_r"], record["r"], abs_tol=1e-9)):
                        raise ValueError(f"Baseline replay mismatch: {record}; replay={sim}")

                    sim.update(key=key, time=record["t"])
                    if sim["status"] == "resolved":
                        if sim.get("risk") and ref_risk > 0:
                            sim["ref_net_r"] = sim["net_r"] * (sim["risk"] / ref_risk)
                        else:
                            sim["ref_net_r"] = sim["net_r"]
                        results[arm][record["part"]].append(sim)
                    else:
                        raise ValueError(f"Unresolved geometry: {arm}; replay={sim}")
            print(f"{sym}: tested {len(arms)} SL/TP geometries on {len(items)} signals", flush=True)
    finally:
        learner.finish = original_finish
        learner.MAX_HOLD = original_max_hold
        learner.protective_stop = original_prot
        learner.partial_size = original_part
        learner.discretionary_exit = original_discretionary
        exits.aggregate = original_aggregate

    summary = {}
    baseline = results["baseline"]
    for arm in arms:
        parts = results[arm]
        summary[arm] = {
            "train": {**metrics(parts["train"]), "excluded": excluded[arm]["train"],
                      "ref_mean_r": fmean([r["ref_net_r"] for r in parts["train"]]) if parts["train"] else 0.0,
                      "delta_vs_baseline_ci95": paired_ci(parts["train"], baseline["train"])},
            "validation": {**metrics(parts["validation"]), "excluded": excluded[arm]["validation"],
                           "ref_mean_r": fmean([r["ref_net_r"] for r in parts["validation"]]) if parts["validation"] else 0.0,
                           "delta_vs_baseline_ci95": paired_ci(parts["validation"], baseline["validation"])},
        }
        for part in ("train", "validation"):
            filled = [row for row in parts[part] if row.get("entry") is not None]
            summary[arm][part]["filled_metrics"] = metrics(filled)
            summary[arm][part]["by_symbol"] = {
                symbol: metrics([row for row in parts[part] if row["key"][0] == symbol])
                for symbol in cohort_by_sym}
            keys = {row["key"] for row in parts[part]}
            matched = [row for row in baseline[part] if row["key"] in keys]
            summary[arm][part]["matched_baseline"] = metrics(matched)
            if matched:
                summary[arm][part]["paired_mean_delta_r"] = (
                    summary[arm][part]["mean_r"] - fmean(row["net_r"] for row in matched))
            else:
                summary[arm][part]["paired_mean_delta_r"] = 0.0

        t, v = summary[arm]["train"], summary[arm]["validation"]
        print(f"{arm:24s} | train: n={t['n']:4d} mean_r={t['mean_r']:+.4f} (ref={t['ref_mean_r']:+.4f}) pf={t['pf']:.3f} | val: n={v['n']:3d} mean_r={v['mean_r']:+.4f} (ref={v['ref_mean_r']:+.4f}) pf={v['pf']:.3f}")

    for part in ("train", "validation"):
        structural = results["tp_structural"][part]
        control = results["structural_gate_control"][part]
        summary["tp_structural"][part]["vs_same_gate_original_target"] = {
            "mean_delta_r_per_signal": fmean(row["net_r"] for row in structural)
                                       - fmean(row["net_r"] for row in control),
            "ci95": paired_ci(structural, control),
            "filled_ci95": paired_ci([row for row in structural if row.get("entry") is not None], control),
        }

    OUTPUT.write_text(json.dumps(dict(
        data_sha256=raw["data_sha256"],
        code_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        split_utc=raw["split_utc"],
        data_start_utc=datetime.fromtimestamp(first, timezone.utc).isoformat(),
        data_end_exclusive_utc=datetime.fromtimestamp(end, timezone.utc).isoformat(),
        limitations=[
            "Archived baseline-resolved cohort, 1111 train / 421 validation signals; not current VPS data",
            "Signals overlap; not portfolio ROI; validation reused, not fresh holdout",
            "Swing buffer uses M15 ATR14; H1 swing is last 5 closed H1 bars",
            "Initial targets use signal close, not next execution price; fixed_tp disables adaptive exits",
            "Swing and structural arms retain adaptive exits; initial targets can subsequently move",
            "Structural target caps original/2R target; missing or sub-1.5R barriers score zero, not dropped",
            "Runner takes 50% at a close beyond 1R, then cost-aware BE/chandelier, not exact intrabar 1R fill",
            "48h cap retained; open boundary positions settled at last close with costs",
            "Nominal weekly bootstrap intervals, no multiple-testing correction; reference R uses original executed risk",
        ],
        arms=summary,
    ), indent=2, allow_nan=False) + "\n")


if __name__ == "__main__":
    run_sltp_grid()
