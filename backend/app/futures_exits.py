"""Paper exit policies shared by teaching labels, validation and execution."""

import math
from decimal import Decimal, ROUND_DOWN

from .futures_rules import atr14
from .strategy import aggregate

EVOLVABLE_EXITS = ("fixed", "trail", "adaptive", "atr_chandelier", "time_stop",
                   "scale_out_ladder", "signal_invalidation", "runner", "volatility_stop", "funding_exit")
EXIT_POLICIES = (*EVOLVABLE_EXITS, "breakeven", "structure")
EXIT_DEFAULTS = {
    "atr_chandelier": {"atr_multiple": 3.0},
    "time_stop": {"max_bars": 32, "min_progress_r": 0.5},
    "scale_out_ladder": {"step_r": 1.0, "partial_fraction": 0.3},
    "signal_invalidation": {"confirm_bars": 2},
    "runner": {"partial_r": 1.0, "partial_fraction": 0.5, "atr_multiple": 3.0},
    "volatility_stop": {"atr_multiple": 2.0},
    "funding_exit": {"funding_r": 0.1},
}
EXIT_BOUNDS = {
    "atr_multiple": (1.0, 5.0, 0.5), "max_bars": (4, 192, 4),
    "min_progress_r": (0.1, 1.0, 0.1), "step_r": (0.5, 2.0, 0.5),
    "partial_fraction": (0.1, 0.7, 0.1), "partial_r": (1.0, 3.0, 0.5),
    "confirm_bars": (1, 4, 1), "funding_r": (0.05, 0.5, 0.05),
}

def exit_parameters(policy, params=None):
    if policy not in EXIT_POLICIES or params is not None and not isinstance(params, dict):
        raise ValueError("Invalid futures exit parameters")
    defaults = EXIT_DEFAULTS.get(policy, {})
    if set(params or {}) - defaults.keys():
        raise ValueError("Unexpected futures exit parameter")
    result = {**defaults, **(params or {})}
    for key, value in result.items():
        low, high, _ = EXIT_BOUNDS[key]
        if (type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high
                or key in ("max_bars", "confirm_bars") and type(value) is not int):
            raise ValueError(f"Invalid futures exit parameter: {key}")
    return result

def has_target(position):
    return position.get("exit_policy", "fixed") not in ("runner", "scale_out_ladder")

def discretionary_exit(position, candles):
    if not candles:
        return None
    policy = position.get("exit_policy", "fixed")
    params = exit_parameters(policy, position.get("exit_params"))
    direction = 1 if position["side"] == "long" else -1
    progress = direction * (candles[-1].close - position["entry"]) / position["risk"]
    if policy == "time_stop":
        elapsed = (candles[-1].time + 900 - position["entry_time"]) // 3600
        if elapsed >= params["max_bars"] and progress < params["min_progress_r"]:
            return "time_stop"
    if policy == "scale_out_ladder" and progress >= 3 * params["step_r"]:
        return "ladder_complete"
    if policy == "funding_exit":
        original = position.get("original_quantity", position.get("quantity", 1.0))
        if position.get("funding_cost", 0) >= params["funding_r"] * position["risk"] * original:
            return "funding_exit"
    if policy == "signal_invalidation":
        from .futures_rule_tree import matches
        rule = position.get("exit_rule", {})
        def trends(node):
            if node.get("feature") == "trend" and node.get("frame") in ("H1", "H4"):
                return [node]
            if node.get("op") in ("all", "then"):
                return [leaf for child in node["children"] for leaf in trends(child)]
            return []
        gates = trends(rule) or [{"feature": "trend", "frame": "H4"}]
        count = params["confirm_bars"]
        histories = [candles[:len(candles) - offset] for offset in range(count)]
        if all(len(history) >= 800 and any(matches(gate, history, -direction, 20)
                                           for gate in gates) for history in histories):
            return "signal_invalidation"
    return None

def adaptive_levels(side, candles, target_r=2.0, stop_buffer=0.0):
    """Stop from swing; TP = target_r cap, pulled in front of the nearest barrier. None if barrier < 1.5R."""
    direction = 1 if side == "long" else -1
    h1_bars, atr = h1_view(candles)
    if atr is None:
        return None
    entry = candles[-1].close
    swing = (min(row.low for row in h1_bars[-5:]) if direction == 1
             else max(row.high for row in h1_bars[-5:]))
    risk = max(direction * (entry - swing) + atr * 0.25, atr) + stop_buffer * atr
    stop = entry - direction * risk
    target = entry + direction * max(2.0, target_r) * risk
    barrier = resistance_ahead({"side": side}, candles)
    if barrier is not None and direction * (barrier - target) < 0:
        if direction * (barrier - entry) < 1.5 * risk:
            return None
        target = barrier
    return stop, target

def resistance_ahead(position, candles):
    """Nearest prior closed H1/H4 swing or range barrier."""
    h1, atr = h1_view(candles)
    if atr is None:
        return None
    direction = 1 if position["side"] == "long" else -1
    close, buffer = candles[-1].close, atr * 0.1
    levels = []
    for prior in (h1[-21:-1], aggregate(candles[-1600:], 14400)[-21:-1]):
        if len(prior) < 5:
            continue
        levels.append(max(row.high for row in prior) if direction == 1 else min(row.low for row in prior))
        for index in range(2, len(prior) - 2):
            row = prior[index]
            neighbors = prior[index - 2:index] + prior[index + 1:index + 3]
            if direction == 1 and all(row.high > other.high for other in neighbors):
                levels.append(row.high)
            elif direction == -1 and all(row.low < other.low for other in neighbors):
                levels.append(row.low)
    ahead = [level for level in levels if direction * (level - close) > 0]
    if not ahead:
        return None
    return min(ahead, key=lambda level: direction * level) - direction * buffer

def weak_close(position, candles):
    """Take profit only on evidence: opposite body, rejection wick, or stalling just under a barrier."""
    last, direction = candles[-1], 1 if position["side"] == "long" else -1
    span = max(last.high - last.low, 1e-12)
    body = direction * (last.close - last.open)
    wick = (last.high - max(last.open, last.close) if direction == 1
            else min(last.open, last.close) - last.low)
    if body >= 0.6 * span and wick <= 0.2 * span:
        return False
    if (body <= -0.5 * span
            or wick >= 0.5 * span and wick >= 2 * max(abs(body), 0.05 * span)):
        return True
    barrier = resistance_ahead(position, candles)
    stalled = (abs(body) <= 0.35 * span or len(candles) >= 2
               and direction * (last.close - candles[-2].close) <= 0)
    return stalled and barrier is not None and direction * (barrier - last.close) <= 0.5 * atr14(candles)

def partial_size(position, candles, step=0.0, minimum=0.0):
    if not candles:
        return 0.0
    policy = position.get("exit_policy", "fixed")
    if policy not in ("adaptive", "runner", "scale_out_ladder"):
        return 0.0
    params = exit_parameters(policy, position.get("exit_params"))
    direction = 1 if position["side"] == "long" else -1
    progress = direction * (candles[-1].close - position["entry"]) / position["risk"]
    fraction = 0.0
    partials = position.get("partial_exits") or []
    if policy == "adaptive":
        if not partials and progress >= 1.0 and weak_close(position, candles):
            fraction = 0.5
    elif policy == "runner":
        if not partials and progress >= params["partial_r"]:
            fraction = params["partial_fraction"]
    elif policy == "scale_out_ladder":
        if len(partials) == 0 and progress >= params["step_r"]:
            fraction = params["partial_fraction"]
        elif len(partials) == 1 and progress >= 2 * params["step_r"]:
            fraction = params["partial_fraction"]
    if fraction <= 0:
        return 0.0
    current_qty = position.get("quantity", 1.0)
    if not all(math.isfinite(value) for value in (current_qty, step, minimum)) or current_qty <= 0 or min(step, minimum) < 0:
        raise ValueError("Invalid futures partial quantity")
    original = position.get("original_quantity", current_qty)
    target_qty = Decimal(str(original)) * Decimal(str(fraction))
    if target_qty >= Decimal(str(current_qty)):
        return 0.0
    if step:
        unit = Decimal(str(step))
        target_qty = (target_qty / unit).to_integral_value(rounding=ROUND_DOWN) * unit
    target_f = float(target_qty)
    return target_f if 0 < target_f < current_qty and min(target_f, current_qty - target_f) >= minimum else 0.0

def adaptive_target(position, candles):
    target = position["target"]
    if (position.get("exit_policy") != "adaptive" or len(candles) < 21
            or not weak_close(position, candles)):
        return target
    direction = 1 if position["side"] == "long" else -1
    proposed = resistance_ahead(position, candles)
    if (proposed is None or direction * (proposed - candles[-1].close) <= 0
            or direction * (proposed - position["entry"]) < 1.5 * position["risk"]):
        return target
    return min(target, proposed) if direction == 1 else max(target, proposed)


def structural_target(side, entry, stop, target, candles):
    direction = 1 if side == "long" else -1
    barriers = []
    for duration in (3600, 14400):
        rows = aggregate(candles, duration)[-60:]
        if len(rows) < 20:
            return None
        barriers.append(max(row.high for row in rows[-20:]) if direction == 1
                        else min(row.low for row in rows[-20:]))
        for index in range(2, len(rows) - 2):
            row = rows[index]
            neighbors = rows[index - 2:index] + rows[index + 1:index + 3]
            if direction == 1 and all(row.high > other.high for other in neighbors):
                barriers.append(row.high)
            elif direction == -1 and all(row.low < other.low for other in neighbors):
                barriers.append(row.low)
    ahead = [price for price in barriers if direction * (price - entry) > 0]
    if ahead:
        barrier = min(ahead, key=lambda price: direction * (price - entry))
        capped = barrier - direction * 0.1 * atr14(candles)
        target = min(target, capped) if direction == 1 else max(target, capped)
    if target <= 0 or direction * (target - entry) < 1.5 * direction * (entry - stop):
        return None
    return target


def h1_view(candles):
    """Use only contiguous closed H1 bars; never substitute M15 history."""
    h1 = aggregate(candles[-1600:], 3600)
    if (len(h1) >= 15 and all(after.time - before.time == 3600
                              for before, after in zip(h1[-15:-1], h1[-14:]))):
        return h1, atr14(h1)
    return [], None

def protective_stop(position, candles, fee, slip):
    policy = position.get("exit_policy", "fixed")
    if policy not in EXIT_POLICIES:
        raise ValueError("Invalid futures exit policy")
    stop = position["stop"]
    if policy == "fixed" or not candles:
        return stop
    entry, risk = position["entry"], position["risk"]
    direction = 1 if position["side"] == "long" else -1
    close = candles[-1].close
    if not all(math.isfinite(value) for value in (entry, risk, stop, close, fee, slip)):
        raise ValueError("Invalid futures exit prices/costs")
    if entry <= 0 or risk <= 0 or not 0 <= fee < 1 or not 0 <= slip < 1:
        raise ValueError("Invalid futures exit prices/costs")
    profit_r = direction * (close - entry) / risk
    quantity = position.get("quantity", 1.0)
    if quantity <= 0 or not math.isfinite(quantity):
        raise ValueError("Invalid futures exit quantity")
    costs = (position.get("entry_fee", entry * quantity * fee)
             + position.get("funding_cost", 0)) / quantity
    if not math.isfinite(costs):
        raise ValueError("Invalid futures exit costs")
    breakeven = (direction * entry + costs) / (direction - fee) / (1 - direction * slip)
    params = exit_parameters(policy, position.get("exit_params"))
    view, atr = h1_view(candles)
    if policy in ("atr_chandelier", "volatility_stop", "runner") and atr is None:
        return stop
    if policy == "atr_chandelier":
        extreme = (max(c.high for c in view[-20:]) if direction == 1
                   else min(c.low for c in view[-20:]))
        chandelier = extreme - direction * params["atr_multiple"] * atr
        proposed = (max(breakeven, chandelier) if direction == 1
                    else min(breakeven, chandelier)) if profit_r >= 1 else chandelier
    elif policy == "volatility_stop":
        if profit_r < 1:
            return stop
        trail_vol = close - direction * params["atr_multiple"] * atr
        proposed = max(breakeven, trail_vol) if direction == 1 else min(breakeven, trail_vol)
    elif policy == "runner":
        if profit_r < params["partial_r"]:
            return stop
        extreme = (max(c.high for c in view[-20:]) if direction == 1
                   else min(c.low for c in view[-20:]))
        chandelier = extreme - direction * params["atr_multiple"] * atr
        proposed = max(breakeven, chandelier) if direction == 1 else min(breakeven, chandelier)
    elif policy == "scale_out_ladder":
        if profit_r < params["step_r"]:
            return stop
        proposed = breakeven
        contiguous = (len(candles) >= 15 and all(
            right.time - left.time == 900 for left, right in zip(candles[-15:-1], candles[-14:])
        ))
        if profit_r >= 2 * params["step_r"] and contiguous and atr is not None:
            recent = view[-3:]
            swing = (min(row.low for row in recent) - 0.1 * atr if direction == 1
                     else max(row.high for row in recent) + 0.1 * atr)
            proposed = max(breakeven, swing) if direction == 1 else min(breakeven, swing)
    else:
        if profit_r < 1:
            return stop
        proposed = breakeven
        contiguous = (len(candles) >= 15 and all(
            right.time - left.time == 900 for left, right in zip(candles[-15:-1], candles[-14:])
        ))
        if policy in ("trail", "adaptive") and profit_r >= 1.5 and contiguous and atr is not None:
            recent = view[-3:]
            swing = (min(row.low for row in recent) - 0.1 * atr if direction == 1
                     else max(row.high for row in recent) + 0.1 * atr)
            proposed = max(breakeven, swing) if direction == 1 else min(breakeven, swing)
    if proposed <= 0 or not direction * (close - proposed) > 0:
        return stop
    return max(stop, proposed) if direction == 1 else min(stop, proposed)
