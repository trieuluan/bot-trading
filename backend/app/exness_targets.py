"""Closed H1/H4 barriers for demo exits; fixed 2R remains the ceiling."""

from decimal import Decimal

from .exness import ExnessError


def target_context(candle, hours, higher):
    barriers = []
    buffer = None
    for frame, duration, rows in (("H1", 3600, hours), ("H4", 14400, higher)):
        closed = sorted((row for row in rows if row.time + duration <= candle.time + 900),
                        key=lambda row: row.time)[-60:]
        if len(closed) < 20:
            raise ExnessError(f"Need 20 closed {frame} candles for structural TP")
        if frame == "H1":
            ranges = [max(row.high - row.low, abs(row.high - previous.close), abs(row.low - previous.close))
                      for previous, row in zip(closed[-15:-1], closed[-14:])]
            buffer = sum(ranges) / len(ranges) * 0.1
        for side, price in (("long", max(row.high for row in closed[-20:])),
                            ("short", min(row.low for row in closed[-20:]))):
            barriers.append({"side": side, "price": price, "reason": f"{frame} Donchian20"})
        for index in range(2, len(closed) - 2):
            row = closed[index]
            neighbors = closed[index - 2:index] + closed[index + 1:index + 3]
            if all(row.high > neighbor.high for neighbor in neighbors):
                barriers.append({"side": "long", "price": row.high, "reason": f"{frame} swing high"})
            if all(row.low < neighbor.low for neighbor in neighbors):
                barriers.append({"side": "short", "price": row.low, "reason": f"{frame} swing low"})
    return {"barriers": barriers, "buffer": buffer}


def structural_target(side, entry, stop, context, tick):
    direction = Decimal(1) if side == "long" else Decimal(-1)
    risk = direction * (entry - stop)
    if risk <= 0:
        raise ExnessError("Invalid structure stop")
    target, reason = entry + direction * 2 * risk, "fixed_2r"
    buffer = Decimal(str(context["buffer"]))
    if not buffer.is_finite() or buffer < 0:
        raise ExnessError("Invalid TP buffer")
    for barrier in context["barriers"]:
        price = Decimal(str(barrier["price"]))
        if not price.is_finite() or price <= 0:
            raise ExnessError("Invalid TP barrier")
        if barrier["side"] == side and direction * (price - entry) > 0:
            candidate = price - direction * buffer
            if direction * (candidate - target) < 0:
                target, reason = candidate, f'{barrier["reason"]} @ {price}'
    target = target.quantize(tick)
    if direction * (target - entry) < risk:
        raise ExnessError("skipped_poor_rr: structural TP below 1R")
    return target, reason
