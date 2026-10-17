"""Paper exit policies shared by teaching labels, validation and execution."""

import math

from .futures_rules import atr14
from .strategy import aggregate

EXIT_POLICIES = ("fixed", "breakeven", "trail", "structure")


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
    if policy == "trail" and profit_r >= 1.5 and contiguous:
        recent = candles[-3:]
        swing = (min(row.low for row in recent) - 0.1 * atr14(candles) if direction == 1
                 else max(row.high for row in recent) + 0.1 * atr14(candles))
        proposed = max(breakeven, swing) if direction == 1 else min(breakeven, swing)
    if proposed <= 0 or not direction * (close - proposed) > 0:
        return stop
    return max(stop, proposed) if direction == 1 else min(stop, proposed)
