"""Paper exit policies shared by teaching labels, validation and execution."""

import math
from decimal import Decimal, ROUND_DOWN

from .futures_rules import atr14
from .strategy import aggregate

EXIT_POLICIES = ("fixed", "breakeven", "trail", "structure", "adaptive")

def adaptive_levels(side, candles, target_r=2.0, stop_buffer=0.0):
    """Stop from swing; TP = target_r cap, pulled in front of the nearest barrier. None if barrier < 1.5R."""
    direction = 1 if side == "long" else -1
    entry = candles[-1].close
    h1_bars = aggregate(candles[-80:], 3600) if len(candles) >= 80 else []
    if len(h1_bars) >= 5:
        atr = atr14(h1_bars)
        swing = (min(row.low for row in h1_bars[-5:]) if direction == 1
                 else max(row.high for row in h1_bars[-5:]))
    else:
        atr = atr14(candles)
        swing = (min(row.low for row in candles[-20:]) if direction == 1
                 else max(row.high for row in candles[-20:]))
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
    """Nearest prior M15/H1 swing or range barrier, using closed candles only."""
    if len(candles) < 21:
        return None
    direction = 1 if position["side"] == "long" else -1
    close, buffer = candles[-1].close, atr14(candles) * 0.1
    levels = []
    for prior in (candles[-21:-1], aggregate(candles[-100:-1], 3600)[-20:]):
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
    if (position.get("exit_policy") != "adaptive" or position.get("partial_exits")
            or not candles):
        return 0.0
    direction = 1 if position["side"] == "long" else -1
    if direction * (candles[-1].close - position["entry"]) < position["risk"] or not weak_close(position, candles):
        return 0.0
    quantity = position.get("quantity", 1.0)
    if not all(math.isfinite(value) for value in (quantity, step, minimum)) or quantity <= 0 or min(step, minimum) < 0:
        raise ValueError("Invalid futures partial quantity")
    half = Decimal(str(quantity)) / 2
    if step:
        unit = Decimal(str(step))
        half = (half / unit).to_integral_value(rounding=ROUND_DOWN) * unit
    half = float(half)
    return half if 0 < half < quantity and min(half, quantity - half) >= minimum else 0.0

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
    if profit_r < 1:
        return stop
    quantity = position.get("quantity", 1.0)
    if quantity <= 0 or not math.isfinite(quantity):
        raise ValueError("Invalid futures exit quantity")
    costs = (position.get("entry_fee", entry * quantity * fee)
             + position.get("funding_cost", 0)) / quantity
    if not math.isfinite(costs):
        raise ValueError("Invalid futures exit costs")
    breakeven = (direction * entry + costs) / (direction - fee) / (1 - direction * slip)
    proposed = breakeven
    contiguous = (len(candles) >= 15 and all(
        right.time - left.time == 900 for left, right in zip(candles[-15:-1], candles[-14:])
    ))
    if policy in ("trail", "adaptive") and profit_r >= 1.5 and contiguous:
        recent = candles[-3:]
        swing = (min(row.low for row in recent) - 0.1 * atr14(candles) if direction == 1
                 else max(row.high for row in recent) + 0.1 * atr14(candles))
        proposed = max(breakeven, swing) if direction == 1 else min(breakeven, swing)
    if proposed <= 0 or not direction * (close - proposed) > 0:
        return stop
    return max(stop, proposed) if direction == 1 else min(stop, proposed)
