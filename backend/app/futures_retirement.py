"""Forward-only evidence for bounded population replacement, independent of replay."""

import math
from collections import defaultdict
from statistics import mean

from .futures_exits import exit_parameters
from .futures_strategies import NATIVE_SEEDS, exit_policy_enabled, genome_fingerprint

MIN_SAMPLES = 12
MAX_DRAWDOWN_R = 5.0
INACTIVE_SECONDS = 30 * 86400
MIN_SPARSE_DAYS = 5
MIN_SHARED = 50
MIN_OVERLAP = 0.9
MIN_EDGE_R = 0.10
MIN_CONTEXT_SAMPLES = 10


def min_samples_for(genome=None):
    return MIN_SAMPLES


def max_drawdown_for(genome=None):
    return MAX_DRAWDOWN_R


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def forward_evidence(state, now):
    evidence, repeated = defaultdict(dict), defaultdict(set)
    for sample in state["samples"]:
        entry, risk = sample.get("entry"), sample.get("risk")
        stamp, entered, available = (sample.get(key) for key in
                                     ("candle_time", "entry_time", "label_available_at"))
        if (sample.get("status") == "resolved" and finite(entry) and entry > 0
                and finite(risk) and risk > 0 and finite(sample.get("net_r"))
                and all(finite(value) for value in (stamp, entered, available))
                and 0 <= stamp <= entered <= available <= now
                and sample.get("side") in ("long", "short")):
            name, key = sample["strategy"], (stamp, sample["side"])
            if key in evidence[name]:
                repeated[name].add(key)
            evidence[name][key] = sample
    return {name: [row for key, row in rows.items() if key not in repeated[name]]
            for name, rows in evidence.items()}


def metrics(samples):
    total = peak = drawdown = 0.0
    for sample in sorted(samples, key=lambda row: (row["label_available_at"], row["candle_time"], row["side"])):
        total += sample["net_r"]
        peak = max(peak, total)
        drawdown = max(drawdown, peak - total)
    return {"samples": len(samples), "mean_r": mean(row["net_r"] for row in samples) if samples else None,
            "max_drawdown_r": drawdown}


def failure(stats, genome=None):
    min_samples = min_samples_for(genome)
    max_dd = max_drawdown_for(genome)
    if stats["samples"] >= min_samples and stats["mean_r"] < 0:
        return "negative_forward_r"
    if stats["max_drawdown_r"] >= max_dd and stats["samples"] > 0:
        return "forward_drawdown"
    return None


def execution_config(genome):
    policy = genome.get("exit_policy", "fixed")
    return (genome.get("entry_policy", "market"), policy,
            sorted(exit_parameters(policy, genome.get("exit_params")).items()),
            genome.get("exit_profile", "default"), genome["target_r"], genome["stop_buffer"],
            genome.get("signal_frame"))


def redundant(candidate, keeper, samples, retained):
    if execution_config(candidate["genome"]) != execution_config(keeper["genome"]):
        return None
    if not samples or not retained:
        return None
    start = max(candidate["born_at"], keeper["born_at"],
                min(row["candle_time"] for row in samples), min(row["candle_time"] for row in retained))
    end = min(max(row["candle_time"] for row in samples), max(row["candle_time"] for row in retained))
    left_rows = [row for row in samples if start <= row["candle_time"] <= end]
    right_rows = [row for row in retained if start <= row["candle_time"] <= end]
    left = {(row["candle_time"], row["side"]): row for row in left_rows}
    right = {(row["candle_time"], row["side"]): row for row in right_rows}
    if (len(left) != len(left_rows) or len(right) != len(right_rows)
            or min(len(left), len(right)) < MIN_SHARED):
        return None
    shared, union = left.keys() & right.keys(), left.keys() | right.keys()
    if len(shared) < MIN_SHARED or len(shared) / len(union) < MIN_OVERLAP:
        return None
    left_mean = mean(left[key]["net_r"] for key in shared)
    right_mean = mean(right[key]["net_r"] for key in shared)
    if (right_mean - left_mean < MIN_EDGE_R - 1e-12
            or metrics(right_rows)["max_drawdown_r"] > metrics(left_rows)["max_drawdown_r"]):
        return None
    groups, unique = defaultdict(list), defaultdict(list)
    for key in shared:
        first, second = left[key], right[key]
        if not first.get("regime") or first.get("regime") != second.get("regime"):
            return None
        groups[(first["side"], first["regime"])].append((first["net_r"], second["net_r"]))
    if any(len(values) < MIN_CONTEXT_SAMPLES or mean(second for _, second in values)
           < mean(first for first, _ in values) for values in groups.values()):
        return None
    for key in left.keys() - shared:
        row = left[key]
        if not row.get("regime"):
            return None
        unique[(row["side"], row["regime"])].append(row["net_r"])
    if any(mean(values) > 0 for values in unique.values()):
        return None
    return {"shared": len(shared), "union": len(union), "ratio": len(shared) / len(union),
            "start": start, "end": end, "matched_mean_r": left_mean,
            "retained_matched_mean_r": right_mean}


def retirement_candidates(state, evidence, protected, now=None):
    if protected is None:
        return []
    protected = set(protected)
    protected.update(row["strategy"] for row in state["samples"] if row.get("status") == "open")
    decision = state.get("last_decision") or {}
    if decision.get("time") == state.get("cursor") and decision.get("selected"):
        protected.add(decision["selected"])
    stats = {name: metrics(evidence.get(name, [])) for name in state["candidates"]}
    result = []
    for name, candidate in sorted(state["candidates"].items()):
        genome = candidate["genome"]
        min_s = min_samples_for(genome)
        if name in protected or not exit_policy_enabled(genome):
            continue
        if genome_fingerprint(genome) in {genome_fingerprint(seed) for seed in NATIVE_SEEDS}:
            continue
        reason = failure(stats[name], genome)
        metadata = {"reason": reason, **stats[name], "retained_strategy": None, "overlap": None}
        if not reason and stats[name]["samples"] >= min_s:
            for keeper, other in sorted(state["candidates"].items()):
                k_min = min_samples_for(other["genome"])
                if (keeper == name or stats[keeper]["samples"] < k_min or failure(stats[keeper], other["genome"])
                        or not exit_policy_enabled(other["genome"])):
                    continue
                overlap = redundant(candidate, other, evidence[name], evidence[keeper])
                if overlap:
                    metadata.update(reason="redundant_forward", retained_strategy=keeper, overlap=overlap)
                    break
        if not metadata["reason"]:
            cursor = state.get("cursor") if state.get("cursor") is not None else now
            if cursor is not None:
                tree_samples = [row for row in state["samples"] if row.get("strategy") == name]
                last_activity = (max((row["candle_time"] for row in tree_samples), default=None)
                                 if tree_samples else candidate.get("born_at"))
                if last_activity is not None and cursor - last_activity >= INACTIVE_SECONDS:
                    metadata.update(reason="stagnant_inactivity")
        if not metadata["reason"] and stats[name]["samples"] == 0 and not genome.get("signal_frame"):
            cursor = state.get("cursor") if state.get("cursor") is not None else now
            born = candidate.get("born_at")
            if cursor is not None and born is not None and cursor - born >= MIN_SPARSE_DAYS * 86400:
                from .futures_population_audit import eligible
                audit = state.get('population_audit', {}).get(name)
                sampled = any(sample['strategy'] == name for sample in
                              state['samples'] + state.get('sample_archive', []))
                if not sampled and eligible(candidate, audit, cursor):
                    metadata.update(reason="sparse_forward_replay", audit=audit)
        if metadata["reason"]:
            result.append((name, metadata))
    priorities = {"negative_forward_r": 0, "forward_drawdown": 1, "sparse_forward_replay": 2,
                  "stagnant_inactivity": 3, "redundant_forward": 4}
    return sorted(result, key=lambda item: (priorities[item[1]["reason"]], item[1]["mean_r"] or 0.0,
                                          -item[1]["max_drawdown_r"], item[0]))
