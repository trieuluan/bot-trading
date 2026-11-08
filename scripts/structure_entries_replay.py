"""Local paired structure-entry replay on archived signals; no DB or VPS writes.

Run: /private/tmp/btvenv3/bin/python -m scripts.structure_entries_replay
Unfilled/no-level signals score 0R in all-signal expectations so lower fill rates
cannot hide behind filled-only selection bias. Both own-risk R and reference-risk R
(normalized to baseline market risk) are reported.
"""

import bisect
import copy
import hashlib
import json
import math
import random
from collections import OrderedDict, defaultdict
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from statistics import fmean

from backend.app import futures_entries as entries
from backend.app import futures_exits as exits
from backend.app import futures_learner as learner
from backend.app.futures_rule_tree import confirmed_pivots, frame_rows, structure_level
from backend.app.mexc import validate_candles
from backend.app.futures_strategies import strategy_setup
from backend.app.futures_symbols import symbol_scope
from scripts.futures_replay_340d import setup_memo

DATA = Path("research-output/futures-nov01/mexc_10pairs_340d_compare.pkl")
COHORT = Path("research-output/futures-gene-ablation.json")
MANIFEST = COHORT.with_suffix(".manifest.json")
OUTPUT = Path("research-output/structure-entries-replay.json")
BASES = {"exit_0": 0, "exit_1": 1, "dryup": 11, "cdir_10": 10, "cdir_23": 23}
POLICIES = (
    "market",
    "body38",
    "body50",
    "ema20_m15",
    "breakout_h1",
    "retest_h1",
    "sweep_h1",
    "pivot_retest_h1",
    "pivot_sweep_h1",
    "micro_bos_m15",
    "sweep_m15",
    "body38_confirm",
    "body50_confirm",
    "ema20_m15_confirm",
)


def valid_level(level, signal_close, stop, direction):
    return (level is not None and math.isfinite(level) and level > 0
            and direction * (signal_close - level) > 0
            and direction * (level - stop) > 0)


def support_pivot(rows, direction):
    pivots = confirmed_pivots(rows, -direction, 20)
    if not pivots:
        return None
    pivot = pivots[-1]
    level = rows[pivot].low if direction == 1 else rows[pivot].high
    if any((bar.low < level if direction == 1 else bar.high > level)
           for bar in rows[pivot + 1:]):
        return None
    return level


def custom_entry_level(policy, history, side, stop):
    if policy in (*entries.ENTRY_POLICIES, *entries.LEGACY_POLICIES) and policy != "market":
        return entries.entry_level(policy, history, side, stop)
    direction = 1 if side == "long" else -1
    signal = history[-1]
    if policy in ("body38_confirm", "body50_confirm"):
        base = "body38" if policy.startswith("body38") else "body50"
        return entries.entry_level(base, history, side, stop)
    if policy == "ema20_m15_confirm":
        return entries.entry_level("ema20_m15", history, side, stop)
    if policy in ("pivot_retest_h1", "pivot_sweep_h1"):
        hours = frame_rows(history, "H1")
        if len(hours) < 22:
            return None
        pivot = structure_level(hours, direction, 20)
        if pivot is None:
            return None
        level = pivot[0]
        if direction * (hours[-1].close - level) <= 0:
            return None
        return level if valid_level(level, signal.close, stop, direction) else None
    if policy == "micro_bos_m15":
        level = support_pivot(frame_rows(history, "H1"), direction)
        return level if valid_level(level, signal.close, stop, direction) else None
    if policy == "sweep_m15":
        level = support_pivot(history, direction)
        return level if valid_level(level, signal.close, stop, direction) else None
    raise ValueError(f"Unknown policy: {policy}")


def wait_confirm(sample, bar, require_body_align=True, require_strict_wick=True):
    """Shared M15 close-confirmation logic matching production retest_h1 / sweep_h1."""
    direction = 1 if sample["side"] == "long" else -1
    level = sample["level"]
    touched = (bar.low < level if require_strict_wick else bar.low <= level) if direction == 1 else (
        bar.high > level if require_strict_wick else bar.high >= level)
    target_touched = bar.high >= sample["target"] if direction == 1 else bar.low <= sample["target"]
    stop_touched = bar.low <= sample["stop"] if direction == 1 else bar.high >= sample["stop"]
    if stop_touched:
        sample.update(status="cancelled", exit_reason="stop_before_confirmation")
    elif direction * (bar.close - level) < 0:
        sample.update(status="cancelled", exit_reason="failed_retest" if require_body_align else "closed_through_level")
    elif target_touched:
        sample.update(status="cancelled", exit_reason="target_before_confirmation")
    elif touched and direction * (bar.close - level) > 0 and (
            not require_body_align or direction * (bar.close - bar.open) > 0):
        sample.update(confirmed=True, entry_time=bar.time + 900)


def custom_wait(sample, bar, funding, history):
    policy = sample["entry_policy"]
    if policy in (*entries.ENTRY_POLICIES, *entries.LEGACY_POLICIES) and policy != "market":
        return entries.wait(sample, bar, funding, history)
    if bar.time < sample["entry_time"]:
        return None
    if bar.time >= sample["first_entry_time"] + entries.WAIT_BARS * 900:
        sample.update(status="cancelled", exit_reason="expired")
        return None
    if policy in ("pivot_retest_h1", "body38_confirm", "body50_confirm", "ema20_m15_confirm"):
        wait_confirm(sample, bar, require_body_align=True, require_strict_wick=True)
        return None
    if policy in ("pivot_sweep_h1", "sweep_m15"):
        wait_confirm(sample, bar, require_body_align=False, require_strict_wick=True)
        return None
    if policy == "micro_bos_m15":
        direction = 1 if sample["side"] == "long" else -1
        level = sample["level"]
        stop_touched = bar.low <= sample["stop"] if direction == 1 else bar.high >= sample["stop"]
        target_touched = bar.high >= sample["target"] if direction == 1 else bar.low <= sample["target"]
        if stop_touched:
            sample.update(status="cancelled", exit_reason="stop_before_confirmation")
            return None
        if target_touched:
            sample.update(status="cancelled", exit_reason="target_before_confirmation")
            return None
        touched = bar.low <= level if direction == 1 else bar.high >= level
        if touched and not sample.get("zone_touched"):
            pivot = structure_level(history, direction, 20)
            if pivot is None or direction * (bar.close - pivot[0]) > 0:
                sample.update(status="cancelled", exit_reason="no_valid_micro_pivot")
                return None
            sample["zone_touched"] = True
            sample["bos_trigger"] = pivot[0]
            return None
        if sample.get("zone_touched"):
            trigger = sample["bos_trigger"]
            if direction * (bar.close - trigger) > 0 and direction * (bar.close - bar.open) > 0:
                sample.update(confirmed=True, entry_time=bar.time + 900)
        return None
    raise ValueError(f"Unknown policy: {policy}")


def prepare_sample(base_sample, policy, history, stamp):
    sample = copy.deepcopy(base_sample)
    sample["entry_time"] = stamp + 900
    if policy == "market":
        return sample
    level = custom_entry_level(policy, history, sample["side"], sample["stop"])
    sample.update(entry_policy=policy, level=level, first_entry_time=stamp + 900)
    if level is None:
        sample.update(status="resolved", exit_reason="no_valid_level", net_r=0.0,
                      ref_r=0.0, filled=False, label_available_at=stamp + 900)
    return sample


def settle_at_boundary(sample, rows, boundary):
    if sample.get("entry") is None:
        sample.update(status="resolved", exit_reason="unfilled_at_boundary",
                      net_r=0.0, ref_r=0.0, filled=False)
        return
    index = bisect.bisect_left([bar.time for bar in rows], boundary) - 1
    if index < 0 or rows[index].time + 900 != boundary or rows[index].time < sample["entry_time"]:
        raise ValueError("Missing final closed candle for administrative settlement")
    bar = rows[index]
    direction = 1 if sample["side"] == "long" else -1
    learner.finish(sample, direction, bar.close, "boundary_mark", bar)
    sample["filled"] = True


def simulate_policy(base_sample, policy, rows, fair, funding, times, signal_index, boundary, history):
    stamp = rows[signal_index].time
    sample = prepare_sample(base_sample, policy, history(signal_index), stamp)
    if sample["status"] != "open":
        return sample
    for cursor in range(signal_index + 1, len(rows)):
        bar = rows[cursor]
        if bar.time >= boundary:
            settle_at_boundary(sample, rows, boundary)
            break
        high = bisect.bisect_right(times, bar.time)
        recent_funding = funding[max(0, high - 4):high]
        if sample["entry"] is None and sample.get("entry_policy") and not sample.get("confirmed"):
            filled = custom_wait(sample, bar, recent_funding, history(cursor))
            if sample["status"] == "cancelled":
                sample.update(status="resolved", net_r=0.0, ref_r=0.0, filled=False,
                              label_available_at=bar.time + 900)
                break
            if filled is None:
                continue
            bar, recent_funding = filled
        learner.advance(sample, bar, fair[cursor], recent_funding, history(cursor))
        if sample.get("entry") is not None:
            sample["filled"] = True
        if sample["status"] == "excluded":
            sample.update(status="resolved", exit_reason=sample.get("reason", "invalid_entry_geometry"),
                          net_r=0.0, ref_r=0.0, filled=False)
            break
        if sample["status"] != "open":
            break
    if sample["status"] == "open":
        settle_at_boundary(sample, rows, boundary)
    if sample.get("filled") and sample.get("risk") and sample.get("reference_risk"):
        sample["ref_r"] = sample["net_r"] * sample["risk"] / sample["reference_risk"]
    else:
        sample.setdefault("ref_r", 0.0)
    return sample


def summarize_arm(rows, baseline_rows):
    if not rows:
        return {"n_signals": 0}
    base_by_key = {r["key"]: r for r in baseline_rows}
    matched = [r for r in rows if r["key"] in base_by_key]
    all_r = [r["net_r"] for r in matched]
    all_ref_r = [r["ref_r"] for r in matched]
    filled = [r for r in matched if r.get("filled")]
    filled_r = [r["net_r"] for r in filled]
    filled_ref_r = [r["ref_r"] for r in filled]
    losses_all = -sum(v for v in all_r if v < 0)
    losses_filled = -sum(v for v in filled_r if v < 0)
    reasons = defaultdict(int)
    for r in matched:
        reasons[r.get("exit_reason") or r["status"]] += 1

    weeks = defaultdict(list)
    ref_weeks = defaultdict(list)
    for r in matched:
        weeks[r["time"] // (7 * 86400)].append(r["net_r"] - base_by_key[r["key"]]["net_r"])
        ref_weeks[r["time"] // (7 * 86400)].append(r["ref_r"] - base_by_key[r["key"]]["ref_r"])
    blocks, rng, draws = list(weeks.values()), random.Random(42), []
    ref_blocks, ref_rng, ref_draws = list(ref_weeks.values()), random.Random(42), []
    for _ in range(1000):
        picked = [rng.choice(blocks) for _ in blocks]
        draws.append(sum(map(sum, picked)) / sum(map(len, picked)))
        ref_picked = [ref_rng.choice(ref_blocks) for _ in ref_blocks]
        ref_draws.append(sum(map(sum, ref_picked)) / sum(map(len, ref_picked)))
    draws.sort()
    ref_draws.sort()

    return dict(
        n_signals=len(matched),
        n_filled=len(filled),
        fill_rate=len(filled) / len(matched) if matched else 0.0,
        mean_r_per_signal=fmean(all_r) if all_r else 0.0,
        mean_ref_r_per_signal=fmean(all_ref_r) if all_ref_r else 0.0,
        total_r=sum(all_r),
        total_ref_r=sum(all_ref_r),
        pf_all=sum(v for v in all_r if v > 0) / losses_all if losses_all > 0 else None,
        mean_r_when_filled=fmean(filled_r) if filled_r else None,
        mean_ref_r_when_filled=fmean(filled_ref_r) if filled_ref_r else None,
        win_rate_when_filled=sum(1 for v in filled_r if v > 0) / len(filled_r) if filled_r else None,
        pf_when_filled=sum(v for v in filled_r if v > 0) / losses_filled if losses_filled > 0 else None,
        delta_vs_reference_per_signal=fmean(r["net_r"] - base_by_key[r["key"]]["net_r"] for r in matched),
        delta_vs_reference_ci95=[draws[25], draws[974]],
        delta_ref_r_vs_reference_per_signal=fmean(r["ref_r"] - base_by_key[r["key"]]["ref_r"] for r in matched),
        delta_ref_r_vs_reference_ci95=[ref_draws[25], ref_draws[974]],
        reference_mean_r_on_filled_keys=fmean(base_by_key[r["key"]]["net_r"] for r in filled) if filled else None,
        unfilled_reference_winners=sum(base_by_key[r["key"]]["net_r"] > 0 for r in matched if not r.get("filled")),
        reasons=dict(reasons),
    )


def main():
    raw = json.loads(MANIFEST.read_text())
    if hashlib.sha256(DATA.read_bytes()).hexdigest() != raw["data_sha256"]:
        raise ValueError("Dataset differs from archived cohort")
    import pickle
    mexc = pickle.loads(DATA.read_bytes())
    first = max(value[2][0].time for value in mexc.values())
    series = {s: [b for b in v[2] if b.time >= first] for s, v in mexc.items()}
    expected = list(range(first, min(rows[-1].time for rows in series.values()) + 900, 900))
    for sym, rows in series.items():
        fair_rows = [b for b in mexc[sym][3] if b.time >= first]
        validate_candles(rows)
        validate_candles(fair_rows)
        if [b.time for b in rows] != expected or [b.time for b in fair_rows] != expected:
            raise ValueError(f"Unaligned candle series: {sym}")
    split = int(datetime.fromisoformat(raw["split_utc"]).timestamp())
    end = min(rows[-1].time for rows in series.values()) + 900

    cohort = [r for r in json.loads(COHORT.read_text())
              if r["arm"] in BASES and r["var"] == ("adaptive" if r["arm"].startswith("exit_") else "orig")]
    cohort_by_sym = defaultdict(list)
    for r in cohort:
        cohort_by_sym[r["sym"]].append(r)

    setup_memo()
    original_aggregate = exits.aggregate
    results = {p: {"train": [], "validation": []} for p in POLICIES}

    try:
        for sym, signals in cohort_by_sym.items():
            rows = series[sym]
            positions = {b.time: idx for idx, b in enumerate(rows)}
            fair = [b for b in mexc[sym][3] if b.time >= first]
            funding = mexc[sym][4]
            times = [r["time"] for r in funding]

            @lru_cache(maxsize=512)
            def history(index, rows=rows):
                return rows[max(0, index - learner.WARMUP + 1):index + 1]

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
                direction = 1 if setup.side == "long" else -1
                ref_risk = direction * (rows[index + 1].open * (1 + direction * learner.SLIP) - setup.stop)
                base_sample = dict(
                    status="open", entry=None, side=setup.side, stop=setup.stop,
                    target=setup.target, exit_policy=genome.get("exit_policy", "fixed"),
                    exit_params=genome.get("exit_params"), exit_rule=genome["rule"],
                    reference_risk=ref_risk,
                )
                key = (sym, record["arm"], record["t"], order)
                boundary = split if record["part"] == "train" else end
                for policy in POLICIES:
                    sim = simulate_policy(base_sample, policy, rows, fair, funding, times, index, boundary, history)
                    if policy == "market" and (
                            sim["status"] != "resolved" or not math.isclose(sim["net_r"], record["r"], abs_tol=1e-9)):
                        raise ValueError(f"Market baseline mismatch: {record}; sim={sim}")
                    sim.update(key=key, time=record["t"])
                    results[policy][record["part"]].append(sim)
            print(f"{sym}: {len(signals)} signals evaluated across {len(POLICIES)} entry policies", flush=True)
    finally:
        exits.aggregate = original_aggregate

    summary = {}
    market = results["market"]
    print(f"\n=== SUMMARY (Validation n={len(market['validation'])}, Train n={len(market['train'])}) ===")
    print(f"{'policy':20s} | {'val_fill':>8s} | {'val_r/sig':>9s} | {'val_refR':>8s} | {'val_r/fill':>10s} | {'val_pf':>6s} | {'val_totR':>8s} | {'train_r/sig':>11s} | {'train_pf':>8s}")
    for policy in POLICIES:
        summary[policy] = {
            "train": summarize_arm(results[policy]["train"], market["train"]),
            "validation": summarize_arm(results[policy]["validation"], market["validation"]),
        }
        if policy.endswith("_confirm"):
            parent = policy.removesuffix("_confirm")
            summary[policy]["passive_control"] = {
                "policy": parent,
                **{part: summarize_arm(results[policy][part], results[parent][part])
                   for part in ("train", "validation")},
            }
        t, v = summary[policy]["train"], summary[policy]["validation"]
        pf_v = f"{v['pf_when_filled']:.3f}" if v["pf_when_filled"] is not None else "  N/A "
        pf_t = f"{t['pf_when_filled']:.3f}" if t["pf_when_filled"] is not None else "  N/A "
        rf_v = f"{v['mean_r_when_filled']:+.4f}" if v["mean_r_when_filled"] is not None else "   N/A    "
        print(f"{policy:20s} | {v['fill_rate']*100:7.1f}% | {v['mean_r_per_signal']:+9.4f} | {v['mean_ref_r_per_signal']:+8.4f} | {rf_v:>10s} | {pf_v:>6s} | {v['total_r']:+8.2f} | {t['mean_r_per_signal']:+11.4f} | {pf_t:>8s}")

    OUTPUT.write_text(json.dumps(dict(
        data_sha256=raw["data_sha256"],
        code_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        split_utc=raw["split_utc"],
        data_start_utc=datetime.fromtimestamp(first, timezone.utc).isoformat(),
        data_end_exclusive_utc=datetime.fromtimestamp(end, timezone.utc).isoformat(),
        definitions={
            "pivot_h1": "Latest still-unbroken 2-left/2-right H1 pivot, lookback20, broken by latest closed H1; frozen level; retest/sweep M15; next-open entry",
            "micro_bos_m15": "Touch unswept confirmed H1 support/resistance pivot; freeze latest unbroken confirmed M15 opposing pivot known at touch; require a later M15 close through that pivot; next-open entry",
            "sweep_m15": "Freeze latest unswept confirmed counter-direction M15 pivot, lookback20; wick through and close reclaim; next-open entry",
            "limit_confirm": "Original body38/body50/EMA20 level; strict trade-through plus aligned-body reclaim close; next-open entry",
        },
        limitations=[
            "Archived baseline-resolved 5-seed cohort, not entire live evolving v4 population; conditional on baseline resolution",
            "Same original signals, initial SL/TP, exit policies, fees, slippage and settled funding; 48h learner holding limit retained",
            "All pending policies use same 4-M15-bar waiting window; micro-BOS requires a separate post-touch candle and may rarely fit",
            "Unfilled and rejected entry geometry score zero; filled boundary positions marked at last closed candle, not discarded",
            "No production or VPS changes; market baseline must reproduce archive to 1e-9",
            "Validation reused in prior research, not fresh holdout; nominal week-block bootstrap CI, no multiple-testing correction",
            "Signal-level overlapping trades, not portfolio PnL/drawdown; own-risk R differs from fixed baseline-risk reference R",
            "Confirmation cannot guarantee avoiding stop-outs; OHLC simulation cannot establish intrabar order",
        ],
        policies=summary,
        samples={policy: {part: [{field: row.get(field) for field in (
            "key", "time", "net_r", "ref_r", "filled", "exit_reason", "entry", "risk", "reference_risk")}
            for row in parts[part]] for part in ("train", "validation")}
            for policy, parts in results.items()},
    ), indent=2, allow_nan=False) + "\n")


if __name__ == "__main__":
    main()
