"""Bounded rule genomes, not arbitrary code generation or a claim of profitability."""

import copy
import hashlib
import json
import math
from dataclasses import replace
from statistics import fmean, pstdev

from .futures_rules import atr14, ema50, funding_filter, mean_reversion
from .strategy import Setup, aggregate, mtf_retest, mtf_scan, retest
from .futures_chart import rule_overlays
from .futures_exits import (EXIT_POLICIES, EVOLVABLE_EXITS, EXIT_BOUNDS, EXIT_DEFAULTS,
                           exit_parameters, adaptive_levels, structural_target)
from .futures_rule_tree import (FAMILY_RULES, RULES, breakout_level, describe_rule, matches, mutate_rule,
                                validate_rule, is_htf_rule, mutate_htf_rule, validate_htf_rule,
                                consensus_slot, h4_consensus)

RULE_FAMILY = "rule_tree"
FAMILIES = ()
MUTABLE_EXITS = EVOLVABLE_EXITS
BOUNDS = {
    "lookback": (10, 40, 5), "target_r": (1.5, 3.0, 0.5),
    "stop_buffer": (0.0, 0.5, 0.1), "max_er": (0.2, 0.4, 0.05),
    "funding_threshold": (0.0001, 0.0005, 0.0001),
}

def is_rule_tree(genome):
    return genome.get("family") == RULE_FAMILY

def shadow_only(genome):
    return False

def validate_genome(genome):
    rule_tree = is_rule_tree(genome)
    extra = {"exit_policy", "exit_params", "rule", "exit_profile"} if rule_tree else {"exit_policy", "exit_params"}
    if (set(genome) - extra != {"family", *BOUNDS} or ("rule" in genome) != rule_tree
            or genome["family"] not in (*FAMILIES, RULE_FAMILY)):
        raise ValueError("Invalid futures genome family/fields")
    if rule_tree:
        validate_rule(genome["rule"])
        if genome.get("exit_profile", "default") not in ("default", *FAMILIES):
            raise ValueError("Invalid rule-tree exit profile")
        if (genome.get("exit_profile", "default") == "default" and genome.get("exit_policy") != "adaptive"
                and (genome["target_r"] != 2.0 or genome["stop_buffer"] != 0.0)):
            raise ValueError("Default rule-tree exits must keep the control R/buffer")
    if genome.get("exit_policy", "fixed") not in EXIT_POLICIES:
        raise ValueError("Invalid futures genome: exit_policy")
    exit_parameters(genome.get("exit_policy", "fixed"), genome.get("exit_params"))
    for key, (low, high, _) in BOUNDS.items():
        value = genome[key]
        if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
            raise ValueError(f"Invalid futures genome: {key}")
    if type(genome["lookback"]) is not int:
        raise ValueError("lookback must be integer")


def to_rule_tree(genome):
    """Convert any legacy family genome to a unified rule_tree genome."""
    validate_genome(genome)
    if is_rule_tree(genome):
        return copy.deepcopy(genome)
    family = genome["family"]
    return {
        **copy.deepcopy(genome),
        "family": RULE_FAMILY,
        "exit_profile": family,
        "rule": copy.deepcopy(FAMILY_RULES[family]),
    }

RULE_SEEDS = [
    {"family": RULE_FAMILY, "lookback": 20, "target_r": 2.0, "stop_buffer": 0.0,
     "max_er": 0.30, "funding_threshold": 0.0003, "exit_policy": "adaptive", "rule": rule}
    for rule in RULES
]
SEEDS = copy.deepcopy(RULE_SEEDS)

def _exit_seeds():
    # Same entry trees (breakout, EMA pullback, sweep) x every other exit mode, so exit modes compare cleanly.
    out = []
    for base in (RULE_SEEDS[0], RULE_SEEDS[1], RULE_SEEDS[7]):
        for policy in EVOLVABLE_EXITS:
            if policy == "adaptive":
                continue
            genome = {key: value for key, value in base.items() if key != "exit_policy"}
            if policy != "fixed":
                genome["exit_policy"] = policy
            if policy in EXIT_DEFAULTS:
                genome["exit_params"] = dict(EXIT_DEFAULTS[policy])
            out.append(genome)
    return out

EXIT_SEEDS = _exit_seeds()

def profile_exit(profile, candles, direction, lookback, genome, funding):
    """Stop/target of a legacy family, so its tree twin exits identically. None = use default."""
    last, buffer = candles[-1], atr14(candles) * 0.1
    if profile in ("mtf_retest", "mean_reversion", "retest", "funding_filter"):
        found = {"mtf_retest": lambda: mtf_retest(candles, allow_short=True, lookback=lookback),
                 "mean_reversion": lambda: mean_reversion(candles, allow_short=True, max_er=genome["max_er"]),
                 "retest": lambda: retest(candles, lookback=lookback, allow_short=True),
                 "funding_filter": lambda: funding_filter(funding, genome["funding_threshold"])(
                     candles, allow_short=True)}[profile]()
        same = found is not None and found.side == ("long" if direction == 1 else "short")
        return (found.stop, found.target) if same else None
    if profile == "breakout":
        prior = candles[-lookback - 1:-1][-5:]
        stop = min(c.low for c in prior) if direction == 1 else max(c.high for c in prior)
    elif profile == "donchian_trend":
        prior = candles[-lookback - 1:-1]
        stop = (max(c.high for c in prior) + min(c.low for c in prior)) / 2
    elif profile == "liquidity_sweep":
        stop = last.low - buffer if direction == 1 else last.high + buffer
    else:
        return None
    return stop, last.close + direction * 2 * abs(last.close - stop)

def genome_name(genome):
    validate_genome(genome)
    digest = hashlib.sha256(json.dumps(genome, sort_keys=True).encode()).hexdigest()[:12]
    return f"{genome['family']}:{digest}"

def canonical_rule(rule):
    if "feature" in rule:
        return rule
    children = [canonical_rule(child) for child in rule["children"]]
    if rule["op"] in ("all", "any"):
        children = [nested for child in children
                    for nested in (child["children"] if child.get("op") == rule["op"] else [child])]
        unique = {json.dumps(child, sort_keys=True): child for child in children}
        children = [unique[key] for key in sorted(unique)]
        if len(children) == 1:
            return children[0]
    return {**rule, "children": children}

def genome_fingerprint(genome):
    normalized = {**genome, "exit_policy": genome.get("exit_policy", "fixed"),
                  "exit_profile": genome.get("exit_profile", "default")}
    if is_rule_tree(genome):
        normalized["rule"] = canonical_rule(genome["rule"])
    if genome.get("exit_policy") in EXIT_DEFAULTS:
        normalized["exit_params"] = exit_parameters(genome["exit_policy"], genome.get("exit_params"))
    return json.dumps(normalized, sort_keys=True)

def exit_policy_enabled(genome):
    return genome.get("exit_policy", "fixed") in MUTABLE_EXITS

def crossover(parent, mate):
    validate_genome(parent)
    validate_genome(mate)
    child = {**parent, "target_r": mate["target_r"], "stop_buffer": mate["stop_buffer"]}
    if is_rule_tree(parent) and is_rule_tree(mate):
        if parent.get("exit_profile", "default") == "default" and mate.get("exit_policy") != "adaptive":
            child["target_r"], child["stop_buffer"] = 2.0, 0.0
        if is_htf_rule(parent["rule"]) and is_htf_rule(mate["rule"]):
            joined = copy.deepcopy(parent["rule"])
            slot = mate["rule"]["children"][0]
            joined["children"][0] = copy.deepcopy(slot) if consensus_slot(slot) else h4_consensus()
            joined["children"][1] = copy.deepcopy(mate["rule"]["children"][1])
        else:
            joined = {"op": "all", "children": [parent["rule"], mate["rule"]]}
        try:
            (validate_htf_rule if is_htf_rule(parent["rule"]) else validate_rule)(joined)
            child["rule"] = joined
        except ValueError:
            pass
    child.pop("exit_policy", None)
    child.pop("exit_params", None)
    if mate.get("exit_policy") in MUTABLE_EXITS and mate.get("exit_policy") != "fixed":
        child["exit_policy"] = mate["exit_policy"]
        if mate.get("exit_params"):
            child["exit_params"] = copy.deepcopy(mate["exit_params"])
    return child

def mutate(genome, rng):
    validate_genome(genome)
    child = dict(genome)
    if not exit_policy_enabled(child):
        child.pop("exit_policy", None)
    profile = genome.get("exit_profile", "default") if is_rule_tree(genome) else genome["family"]
    relevant = ["target_r", "stop_buffer"]
    if is_rule_tree(genome):
        relevant = ["rule", "rule", "lookback"] + (["target_r", "stop_buffer"] if profile != "default" or genome.get("exit_policy") == "adaptive" else [])
    relevant.append("exit_policy")
    if genome.get("exit_policy") in EXIT_DEFAULTS:
        relevant.append("exit_params")
    if profile in ("breakout", "retest", "mtf_retest", "liquidity_sweep", "squeeze", "volume_spike", "donchian_trend", "ema_cross"):
        relevant.append("lookback")
    if profile == "mean_reversion":
        relevant.append("max_er")
    if profile == "funding_filter":
        relevant.append("funding_threshold")
    key = rng.choice(relevant)
    if key == "exit_policy":
        mode = rng.choice([policy for policy in MUTABLE_EXITS
                           if policy != genome.get("exit_policy", "fixed")])
        child.pop("exit_policy", None)
        child.pop("exit_params", None)
        if mode != "fixed":
            child["exit_policy"] = mode
        if profile == "default" and mode != "adaptive":
            child.update(target_r=2.0, stop_buffer=0.0)
        validate_genome(child)
        return child
    if key == "exit_params":
        params = exit_parameters(genome["exit_policy"], genome.get("exit_params"))
        param = rng.choice(list(params))
        low, high, step = EXIT_BOUNDS[param]
        value = rng.choice([value for value in (params[param] - step, params[param] + step)
                            if low - 1e-9 <= value <= high + 1e-9])
        params[param] = int(value) if param in ("max_bars", "confirm_bars") else round(value, 6)
        child["exit_params"] = params
        validate_genome(child)
        return child
    if key == "rule":
        mutation = mutate_htf_rule if is_htf_rule(genome["rule"]) else mutate_rule
        child["rule"] = mutation(genome["rule"], rng)
        validate_genome(child)
        return child
    low, high, step = BOUNDS[key]
    value = rng.choice([v for v in (genome[key] - step, genome[key] + step) if low - 1e-9 <= v <= high + 1e-9])
    child[key] = int(value) if key == "lookback" else round(value, 6)
    validate_genome(child)
    return child

def regime(candles):
    if len(candles) < 21:
        return "warming_up"
    closes = [candle.close for candle in candles[-21:]]
    path = sum(abs(after - before) for before, after in zip(closes, closes[1:]))
    efficiency = abs(closes[-1] - closes[0]) / path if path else 0
    return "range" if efficiency <= 0.30 else "trend_up" if closes[-1] > closes[0] else "trend_down"

def strategy_levels(genome, candles):
    if not candles:
        return []
    lookback = genome.get("lookback", 20)
    h1 = aggregate(candles[:-1], 3600)
    if len(h1) >= lookback:
        history, frame = h1[-lookback:], "H1"
    elif len(candles) >= lookback + 1:
        history, frame = candles[-lookback - 1:-1], "M15"
    else:
        return []
    return [{"title": f"Đỉnh {lookback} {frame}", "price": max(bar.high for bar in history)},
            {"title": f"Đáy {lookback} {frame}", "price": min(bar.low for bar in history)}]

def strategy_setup(genome, candles, funding, btc_candles=None, oi_history=None, benchmarks=None,
                   fair_candles=None):
    validate_genome(genome)
    if len(candles) < 21:
        return None
    tree = genome if is_rule_tree(genome) else to_rule_tree(genome)
    lookback = genome["lookback"]
    context = {"funding": funding, "funding_threshold": genome["funding_threshold"],
               "max_er": genome["max_er"], "htf": is_htf_rule(tree["rule"]),
               "btc_candles": btc_candles, "oi_history": oi_history, "benchmarks": benchmarks,
               "fair_candles": fair_candles}
    found = [side for side in (1, -1)
             if matches(tree["rule"], candles, side, lookback, context=context)]
    if len(found) != 1:
        return None
    direction, last = found[0], candles[-1]
    levels = adaptive_levels("long" if direction == 1 else "short", candles,
                             genome["target_r"], genome["stop_buffer"])
    if levels is None:
        return None
    stop, target = levels
    setup = Setup("long" if direction == 1 else "short", last.close, stop, target,
                  describe_rule(tree["rule"]))
    direction = 1 if setup.side == "long" else -1
    risk = direction * (setup.entry - stop)
    if genome.get("exit_policy") == "structure":
        target = structural_target(setup.side, setup.entry, stop, target, candles)
        if target is None:
            return None
    if risk <= 0 or target <= 0 or direction * (target - setup.entry) <= 0:
        return None
    return replace(setup, stop=stop, target=target)

def strategy_chart(genome, candles, context=None):
    family, lookback = genome["family"], genome["lookback"]
    profile = genome.get("exit_profile", "default") if is_rule_tree(genome) else family
    if family == RULE_FAMILY:
        return {**rule_overlays(genome["rule"], candles, lookback, context),
                "note": "Bot tự ghép: " + describe_rule(genome["rule"])}
    descriptions = {
        "breakout": f"Chờ nến M15 đóng vượt đỉnh/đáy {lookback} nến trước.",
        "retest": f"Chờ phá vùng {lookback} nến rồi retest; chạm vùng chưa phải tín hiệu.",
        "mtf_retest": "Chờ breakout H1, retest và xác nhận M15 cùng xu hướng H4.",
        "mean_reversion": f"Chờ H1 ra ngoài BB20, M15 quay lại dải; ER H4 ≤ {genome['max_er']:g}.",
        "liquidity_sweep": f"Chờ quét đỉnh/đáy {lookback} H1 rồi đóng lại trong vùng, cùng EMA50 H4.",
        "funding_filter": f"Chờ funding vượt ±{genome['funding_threshold'] * 100:g}%, phá vùng 10 M15 cùng EMA50 H4.",
        "squeeze": f"Chờ vùng {lookback} M15 hẹp ≤ 3 ATR14 rồi đóng phá vùng.",
        "trend_pullback": "Chờ M15 chạm EMA50 H1 rồi đóng trở lại theo xu hướng H1.",
        "volume_spike": f"Chờ volume đột biến ≥ 2× TB và nến M15 đóng phá đỉnh/đáy {lookback} nến.",
        "donchian_trend": f"Chờ nến M15 phá kênh Donchian {lookback} nến theo xu hướng EMA50 H1.",
        "ema_cross": f"Chờ giao cắt EMA nhanh ({max(5, lookback // 2)}) và EMA chậm ({lookback}).",
        "rsi_reversal": "Chờ RSI14 quá bán (< 30) cắt lên hoặc quá mua (> 70) cắt xuống.",
    }
    note = descriptions[profile]
    result = {"levels": [], "curves": [], "note": note}
    if not candles:
        return result
    end = candles[-1].time
    levels = strategy_levels(genome, candles)
    history = (aggregate(candles[:-1], 3600)[-lookback:]
               if profile == "liquidity_sweep" else
               candles[-(10 if profile == "funding_filter" else lookback) - 1:-1])
    start = history[0].time if history else end
    if profile == "mtf_retest":
        watch = mtf_scan(candles, lookback=lookback)["current"]
        start = watch["range_start"] if watch else end
    if profile not in ("mean_reversion", "trend_pullback"):
        result["levels"] = [{**level, "from": start, "to": end} for level in levels]
    if profile in ("mean_reversion", "trend_pullback", "liquidity_sweep", "funding_filter"):
        frame = 14400 if profile in ("liquidity_sweep", "funding_filter") else 3600
        bars = aggregate(candles, frame)
        if profile == "mean_reversion":
            for multiplier, label in ((2, "BB20 H1 · trên"), (0, "BB20 H1 · giữa"), (-2, "BB20 H1 · dưới")):
                points = []
                for index in range(max(20, len(bars) - 100), len(bars)):
                    closes = [bar.close for bar in bars[index - 20:index]]
                    points.append({"time": bars[index].time + frame - 900,
                                   "value": fmean(closes) + multiplier * pstdev(closes)})
                if points:
                    result["curves"].append({"title": label, "points": points})
        elif len(bars) >= 50:
            value, points = bars[0].close, []
            for index, bar in enumerate(bars):
                if index:
                    value += 2 / 51 * (bar.close - value)
                if index >= max(49, len(bars) - 100):
                    points.append({"time": bar.time + frame - 900, "value": value})
            result["curves"].append({"title": f"EMA50 {'H4' if frame == 14400 else 'H1'}", "points": points})
        for curve in result["curves"]:
            if curve["points"][-1]["time"] < end:
                curve["points"].append({"time": end, "value": curve["points"][-1]["value"]})
    if not result["levels"] and not result["curves"]:
        result["note"] += " Chưa có vùng retest hợp lệ." if profile == "mtf_retest" else " Thiếu nến để vẽ vùng tham chiếu."
    return result
