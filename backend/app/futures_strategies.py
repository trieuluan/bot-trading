"""Bounded rule genomes, not arbitrary code generation or a claim of profitability."""

import copy
import hashlib
import json
import math
from dataclasses import replace
from statistics import fmean, pstdev

from .futures_rules import atr14, donchian, ema50, funding_filter, liquidity_sweep, mean_reversion, rsi14
from .strategy import Setup, aggregate, breakout, mtf_retest, mtf_scan, retest
from .futures_exits import EXIT_POLICIES, structural_target
from .futures_rule_tree import (FAMILY_RULES, RULES, breakout_level, describe_rule, matches, mutate_rule,
                                validate_rule)

RULE_FAMILY = "rule_tree"
FAMILIES = ("breakout", "mtf_retest", "mean_reversion", "liquidity_sweep", "funding_filter",
            "retest", "squeeze", "trend_pullback", "volume_spike", "donchian_trend", "ema_cross", "rsi_reversal")
SEEDS = [{"family": family, "lookback": 20, "target_r": 2.0,
          "stop_buffer": 0.0, "max_er": 0.30, "funding_threshold": 0.0003}
         for family in FAMILIES]
# ponytail: search fixed exits plus squeeze trailing; expand only after separate validation.
SEEDS.append({**next(seed for seed in SEEDS if seed["family"] == "squeeze"), "exit_policy": "trail"})
MUTABLE_EXITS = ("fixed", "trail")
RULE_SEEDS = [{"family": RULE_FAMILY, "lookback": 20, "target_r": 2.0, "stop_buffer": 0.0,
               "max_er": 0.30, "funding_threshold": 0.0003, "rule": rule} for rule in RULES]
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
    extra = {"exit_policy", "rule", "exit_profile"} if rule_tree else {"exit_policy"}
    if (set(genome) - extra != {"family", *BOUNDS} or ("rule" in genome) != rule_tree
            or genome["family"] not in (*FAMILIES, RULE_FAMILY)):
        raise ValueError("Invalid futures genome family/fields")
    if rule_tree:
        validate_rule(genome["rule"])
        if genome.get("exit_profile", "default") not in ("default", *FAMILIES):
            raise ValueError("Invalid rule-tree exit profile")
        if (genome.get("exit_policy", "fixed") != "fixed" or genome["target_r"] != 2.0
                or genome["stop_buffer"] != 0.0):
            raise ValueError("Rule-tree exits must remain fixed at the control settings")
    if genome.get("exit_policy", "fixed") not in EXIT_POLICIES:
        raise ValueError("Invalid futures genome: exit_policy")
    for key, (low, high, _) in BOUNDS.items():
        value = genome[key]
        if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
            raise ValueError(f"Invalid futures genome: {key}")
    if type(genome["lookback"]) is not int:
        raise ValueError("lookback must be integer")


def to_rule_tree(genome):
    """Convert any legacy family genome to a unified rule_tree genome."""
    if is_rule_tree(genome):
        return copy.deepcopy(genome)
    family = genome.get("family")
    rule = FAMILY_RULES.get(family, {"feature": "breakout", "frame": "M15"})
    return {
        "family": RULE_FAMILY,
        "exit_profile": family if family in FAMILY_RULES else "default",
        "lookback": genome.get("lookback", 20),
        "target_r": 2.0,
        "stop_buffer": 0.0,
        "max_er": genome.get("max_er", 0.30),
        "funding_threshold": genome.get("funding_threshold", 0.0003),
        "rule": copy.deepcopy(rule),
    }

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

def exit_policy_enabled(genome):
    policy = genome.get("exit_policy", "fixed")
    return policy == "fixed" or (genome["family"] == "squeeze" and policy == "trail")

def crossover(parent, mate):
    validate_genome(parent)
    validate_genome(mate)
    child = {**parent, "target_r": mate["target_r"], "stop_buffer": mate["stop_buffer"]}
    if is_rule_tree(parent) and is_rule_tree(mate):
        child["target_r"], child["stop_buffer"] = 2.0, 0.0
        joined = {"op": "all", "children": [parent["rule"], mate["rule"]]}
        try:
            validate_rule(joined)
            child["rule"] = joined
        except ValueError:
            pass
    child.pop("exit_policy", None)
    if parent["family"] == "squeeze" and mate.get("exit_policy") == "trail":
        child["exit_policy"] = "trail"
    return child

def mutate(genome, rng):
    validate_genome(genome)
    child = dict(genome)
    if not exit_policy_enabled(child):
        child.pop("exit_policy", None)
    relevant = ["target_r", "stop_buffer"]
    if is_rule_tree(genome):
        relevant = ["rule", "rule", "lookback"]
    if genome["family"] == "squeeze":
        relevant.append("exit_policy")
    if genome["family"] in ("breakout", "retest", "mtf_retest", "liquidity_sweep", "squeeze", "volume_spike", "donchian_trend", "ema_cross"):
        relevant.append("lookback")
    if genome["family"] == "mean_reversion":
        relevant.append("max_er")
    if genome["family"] == "funding_filter":
        relevant.append("funding_threshold")
    key = rng.choice(relevant)
    if key == "exit_policy":
        mode = rng.choice([policy for policy in MUTABLE_EXITS
                           if policy != genome.get("exit_policy", "fixed")])
        child.pop("exit_policy", None)
        if mode != "fixed":
            child["exit_policy"] = mode
        validate_genome(child)
        return child
    if key == "rule":
        child["rule"] = mutate_rule(genome["rule"], rng)
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
    family, lookback = genome["family"], genome["lookback"]
    if family == RULE_FAMILY:
        return []
    if family == "mean_reversion":
        hours = aggregate(candles, 3600)
        if len(hours) < 21:
            return []
        closes = [bar.close for bar in hours[-21:-1]]
        average, deviation = fmean(closes), pstdev(closes)
        return [{"title": title, "price": price} for title, price in (
            ("BB20 H1 · trên", average + 2 * deviation),
            ("BB20 H1 · giữa", average), ("BB20 H1 · dưới", average - 2 * deviation))]
    if family == "trend_pullback":
        hours = aggregate(candles, 3600)
        return [{"title": "EMA50 H1", "price": ema50(hours)}] if len(hours) >= 50 else []
    if family == "mtf_retest":
        watch = mtf_scan(candles, lookback=lookback)["current"]
        return [{"title": "Vùng retest H1", "price": watch["level"]}] if watch else []
    if family == "liquidity_sweep":
        history, frame = aggregate(candles[:-1], 3600)[-lookback:], "H1"
    else:
        count = 10 if family == "funding_filter" else lookback
        history, frame = candles[-count - 1:-1], "M15"
        lookback = count
    if len(history) < lookback:
        return []
    return [{"title": f"Đỉnh {lookback} {frame}", "price": max(bar.high for bar in history)},
            {"title": f"Đáy {lookback} {frame}", "price": min(bar.low for bar in history)}]

def strategy_setup(genome, candles, funding):
    validate_genome(genome)
    if len(candles) < 21:
        return None
    family, lookback = genome["family"], genome["lookback"]
    if family == RULE_FAMILY:
        context = {"funding": funding, "funding_threshold": genome["funding_threshold"],
                   "max_er": genome["max_er"]}
        found = [side for side in (1, -1)
                 if matches(genome["rule"], candles, side, lookback, context=context)]
        if len(found) != 1:
            return None
        direction, last = found[0], candles[-1]
        stop = (min(bar.low for bar in candles[-5:]) if direction == 1
                else max(bar.high for bar in candles[-5:]))
        target = last.close + direction * 2 * abs(last.close - stop)
        custom = profile_exit(genome.get("exit_profile", "default"), candles, direction, lookback,
                              genome, funding)
        if custom:
            stop, target = custom
        setup = Setup("long" if direction == 1 else "short", last.close, stop, target,
                      describe_rule(genome["rule"]))
    elif family == "breakout":
        setup = breakout(candles, lookback, allow_short=True)
    elif family == "mtf_retest":
        setup = mtf_retest(candles, allow_short=True, lookback=lookback)
    elif family == "mean_reversion":
        setup = mean_reversion(candles, allow_short=True, max_er=genome["max_er"])
    elif family == "liquidity_sweep":
        setup = liquidity_sweep(candles, allow_short=True, lookback=lookback)
    elif family == "funding_filter":
        setup = funding_filter(funding, genome["funding_threshold"])(candles, allow_short=True)
    elif family == "retest":
        setup = retest(candles, lookback=lookback, allow_short=True)
    elif family == "volume_spike":
        history = candles[-lookback - 1 : -1]
        mean_vol = fmean(c.volume for c in history) if history else 0
        last = candles[-1]
        if last.volume >= 2.0 * mean_vol and mean_vol > 0:
            high, low = max(c.high for c in history), min(c.low for c in history)
            if last.close > high and last.close > last.open:
                stop = min(c.low for c in candles[-5:])
                setup = Setup("long", last.close, stop, last.close + 2 * (last.close - stop), "Vol Spike Long")
            elif last.close < low and last.close < last.open:
                stop = max(c.high for c in candles[-5:])
                setup = Setup("short", last.close, stop, last.close - 2 * (stop - last.close), "Vol Spike Short")
            else:
                setup = None
        else:
            setup = None
    elif family == "donchian_trend":
        history = candles[-lookback - 1 : -1]
        upper, lower = max(c.high for c in history), min(c.low for c in history)
        hours = aggregate(candles, 3600)
        ema = ema50(hours) if len(hours) >= 50 else None
        last = candles[-1]
        mid = (upper + lower) / 2
        if last.close > upper and (ema is None or last.close > ema):
            setup = Setup("long", last.close, mid, last.close + 2 * (last.close - mid), "Donchian Breakout Long")
        elif last.close < lower and (ema is None or last.close < ema):
            setup = Setup("short", last.close, mid, last.close - 2 * (mid - last.close), "Donchian Breakout Short")
        else:
            setup = None
    elif family == "ema_cross":
        fast_span = max(5, lookback // 2)
        slow_span = lookback
        if len(candles) >= slow_span + 2:
            def calc_ema(span, data):
                mult = 2 / (span + 1)
                val = data[0].close
                for b in data[1:]:
                    val = val * (1 - mult) + b.close * mult
                return val
            prev_fast = calc_ema(fast_span, candles[:-1])
            prev_slow = calc_ema(slow_span, candles[:-1])
            curr_fast = calc_ema(fast_span, candles)
            curr_slow = calc_ema(slow_span, candles)
            last = candles[-1]
            if prev_fast <= prev_slow and curr_fast > curr_slow:
                stop = min(c.low for c in candles[-5:])
                setup = Setup("long", last.close, stop, last.close + 2 * (last.close - stop), "EMA Cross Long")
            elif prev_fast >= prev_slow and curr_fast < curr_slow:
                stop = max(c.high for c in candles[-5:])
                setup = Setup("short", last.close, stop, last.close - 2 * (stop - last.close), "EMA Cross Short")
            else:
                setup = None
        else:
            setup = None
    elif family == "rsi_reversal":
        if len(candles) >= 20:
            prev_rsi = rsi14(candles[:-1])
            curr_rsi = rsi14(candles)
            last = candles[-1]
            if prev_rsi <= 30 and curr_rsi > 30 and last.close > last.open:
                stop = min(c.low for c in candles[-5:])
                setup = Setup("long", last.close, stop, last.close + 2 * (last.close - stop), "RSI Oversold Long")
            elif prev_rsi >= 70 and curr_rsi < 70 and last.close < last.open:
                stop = max(c.high for c in candles[-5:])
                setup = Setup("short", last.close, stop, last.close - 2 * (stop - last.close), "RSI Overbought Short")
            else:
                setup = None
        else:
            setup = None
    else:
        last = candles[-1]
        history = candles[-lookback - 1:-1]
        high, low = max(bar.high for bar in history), min(bar.low for bar in history)
        side = None
        if family == "squeeze":
            if high - low <= 3 * atr14(candles):
                side = "long" if last.close > high else "short" if last.close < low else None
        else:
            hours = aggregate(candles, 3600)
            if len(hours) < 50:
                return None
            ema = hours[0].close
            for bar in hours[1:]:
                ema += 2 / 51 * (bar.close - ema)
            if hours[-1].close > ema and last.low <= ema < last.close and last.close > last.open:
                side = "long"
            elif hours[-1].close < ema and last.high >= ema > last.close and last.close < last.open:
                side = "short"
        direction = 1 if side == "long" else -1
        stop = min(bar.low for bar in candles[-5:]) if direction == 1 else max(bar.high for bar in candles[-5:])
        setup = Setup(side, last.close, stop, last.close + direction * 2 * abs(last.close - stop), family) if side else None
    if setup is None:
        return None
    direction = 1 if setup.side == "long" else -1
    stop = setup.stop - direction * genome["stop_buffer"] * atr14(candles)
    risk = direction * (setup.entry - stop)
    target = (setup.target if family == "mean_reversion" or genome.get("exit_profile") == "mean_reversion"
              else setup.entry + direction * genome["target_r"] * risk)
    if genome.get("exit_policy") == "structure":
        target = structural_target(setup.side, setup.entry, stop, target, candles)
        if target is None:
            return None
    if risk <= 0 or target <= 0 or direction * (target - setup.entry) <= 0:
        return None
    return replace(setup, stop=stop, target=target)

def strategy_chart(genome, candles):
    family, lookback = genome["family"], genome["lookback"]
    if family == RULE_FAMILY:
        levels = []
        for direction, title in ((1, "Vùng breakout trên"), (-1, "Vùng breakout dưới")):
            price = breakout_level(genome["rule"], candles, direction, lookback)
            if price is not None and candles:
                levels.append({"title": title, "price": price,
                               "from": candles[max(0, len(candles) - lookback - 1)].time,
                               "to": candles[-1].time})
        return {"levels": levels, "curves": [], "note": "Bot tự ghép: " + describe_rule(genome["rule"])}
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
    result = {"levels": [], "curves": [], "note": descriptions[family]}
    if not candles:
        return result
    end = candles[-1].time
    levels = strategy_levels(genome, candles)
    history = (aggregate(candles[:-1], 3600)[-lookback:]
               if family == "liquidity_sweep" else
               candles[-(10 if family == "funding_filter" else lookback) - 1:-1])
    start = history[0].time if history else end
    if family == "mtf_retest":
        watch = mtf_scan(candles, lookback=lookback)["current"]
        start = watch["range_start"] if watch else end
    if family not in ("mean_reversion", "trend_pullback"):
        result["levels"] = [{**level, "from": start, "to": end} for level in levels]
    if family in ("mean_reversion", "trend_pullback", "liquidity_sweep", "funding_filter"):
        frame = 14400 if family in ("liquidity_sweep", "funding_filter") else 3600
        bars = aggregate(candles, frame)
        if family == "mean_reversion":
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
        result["note"] += " Chưa có vùng retest hợp lệ." if family == "mtf_retest" else " Thiếu nến để vẽ vùng tham chiếu."
    return result
