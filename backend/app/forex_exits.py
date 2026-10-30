"""Adaptive Forex exits; no broker calls or mutable market state."""

import math
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

from .exness import ExnessError
from .exness_sessions import regular_session_gap

DEFAULTS = {
    "evidence_h1": {"fraction": 0.3, "min_r": 1.0, "trail_r": 1.5},
    "evidence_m15": {"fraction": 0.3, "min_r": 1.0, "trail_r": 1.5},
    "barrier_partial_h1": {"fraction": 0.3, "min_r": 1.0, "trail_r": 1.5},
    "runner_h1": {"trail_r": 1.5, "atr_multiple": 2.0},
    "trail_swing_h1": {"trail_r": 1.5},
    "ladder_h1": {"fraction": 0.3, "step_r": 1.0, "trail_r": 1.5},
}
BOUNDS = {
    "fraction": (0.2, 0.3, 0.4, 0.5, 0.7), "min_r": (1.0, 1.5, 2.0),
    "trail_r": (1.5, 2.0, 3.0), "atr_multiple": (1.5, 2.0, 3.0), "step_r": (1.0, 1.5, 2.0),
}


def parameters(policy, params=None):
    if policy not in DEFAULTS or params is not None and not isinstance(params, dict):
        raise ExnessError("invalid_exit_params")
    values = dict(DEFAULTS[policy]) if params is None else dict(params)
    if set(values) != set(DEFAULTS[policy]):
        raise ExnessError("invalid_exit_params")
    for name, value in values.items():
        if type(value) not in (int, float) or not math.isfinite(value) or value not in BOUNDS[name]:
            raise ExnessError("invalid_exit_params")
    if policy == "ladder_h1" and values["fraction"] > 0.4:
        raise ExnessError("invalid_ladder_fraction")
    return values


def closed_rows(signal, minutes, hours, now):
    duration = 3600 if signal.get("management_frame") == "H1" else 900
    rows = sorted((row for row in (hours if duration == 3600 else minutes)
                   if getattr(row, "price_type", "bid") == "bid" and row.time + duration <= now),
                  key=lambda row: row.time)[-60:]
    if (len(rows) < 21 or not 0 <= now - rows[-1].time - duration < duration
            or any(after.time - before.time != duration
                   and not regular_session_gap(before.time, after.time, duration, signal.get("symbol", "XAUUSD"))
                   for before, after in zip(rows, rows[1:]))):
        return []
    if any(not all(math.isfinite(value) and value > 0 for value in (row.open, row.high, row.low, row.close))
           or row.low > min(row.open, row.close) or row.high < max(row.open, row.close) for row in rows):
        return []
    return rows


def barrier_levels(side, entry, context, frame=None):
    direction = 1 if side == "long" else -1
    levels = [(barrier["price"] - direction * context["buffer"], barrier["reason"])
              for barrier in context["barriers"] if barrier["side"] == side
              and (frame is None or barrier["reason"].startswith(frame))]
    return sorted((item for item in levels if direction * (item[0] - entry) > 0),
                  key=lambda item: direction * item[0])


def barrier_runner(side, entry, stop, context):
    direction = 1 if side == "long" else -1
    h1 = barrier_levels(side, float(entry), context, "H1")
    h4 = barrier_levels(side, float(entry), context, "H4")
    if not h1:
        return None
    return next((item for item in h4 if direction * (item[0] - h1[0][0]) > 0
                 and direction * (item[0] - float(entry)) >= 1.5 * abs(float(entry - stop))), None)


def plan_legs(signal, body, condition, entry, *, normalized=False):
    policy = signal["exit_policy"]
    params = parameters(policy, signal.get("exit_params"))
    runner = {**body, "role": "runner"}
    signal["partial_skip_reason"] = None
    if policy in ("runner_h1", "trail_swing_h1"):
        return [runner]
    direction = 1 if signal["side"] == "long" else -1
    risk = direction * (float(entry) - float(body["stop_loss_price"]))
    target = None
    if policy == "barrier_partial_h1":
        candidates = barrier_levels(signal["side"], float(entry), signal["target_context"], "H1")
        target = next((level for level, _ in candidates if direction * (level - float(entry)) >= params["min_r"] * risk
                       and direction * (float(body["take_profit_price"]) - level) > 0), None)
        if target is None:
            signal["partial_skip_reason"] = "no_partial_barrier"
            return [runner]
    volume = Decimal(str(body["volume"]))
    count = 2 if policy == "ladder_h1" else 1
    part = volume * Decimal(str(params["fraction"]))
    if not normalized:
        minimum = max(Decimal(condition["volume_min"]), Decimal("0.01"))
        step = Decimal(condition["volume_step"])
        part = minimum + ((part - minimum) / step).to_integral_value(rounding=ROUND_FLOOR) * step
    rest = volume - count * part
    if not normalized and (part < minimum or rest < minimum or (rest - Decimal(condition["volume_min"])) % step):
        signal["partial_skip_reason"] = "insufficient_split_lot"
        return [runner]
    legs = [{**body, "volume": format(part, "f"), "role": f"partial{index + 1}"} for index in range(count)]
    if target is not None:
        tick = Decimal(1).scaleb(-condition["point_digits"])
        price = Decimal(str(target)).quantize(tick, rounding=ROUND_FLOOR if direction == 1 else ROUND_CEILING)
        if direction * (float(price) - float(entry)) < params["min_r"] * risk:
            signal["partial_skip_reason"] = "partial_barrier_after_rounding"
            return [runner]
        legs[0].update(role="tp1", take_profit_price=format(price, "f"))
    return [*legs, {**runner, "volume": format(rest, "f")}]


def action(signal, rows, entry, risk, entry_time, stop, condition, mark, slip=0, commission=0):
    policy = signal["exit_policy"]
    params = parameters(policy, signal.get("exit_params"))
    if not rows or rows[-1].time < entry_time:
        return None
    if not all(math.isfinite(value) for value in (entry, risk, mark, slip, commission)) or risk <= 0:
        raise ExnessError("invalid_management_prices")
    direction = 1 if signal["side"] == "long" else -1
    last = rows[-1]
    span = last.high - last.low
    body = direction * (last.close - last.open)
    wick = last.high - max(last.open, last.close) if direction == 1 else min(last.open, last.close) - last.low
    ranges = [max(row.high - row.low, abs(row.high - previous.close), abs(row.low - previous.close))
              for previous, row in zip(rows[-15:-1], rows[-14:])]
    atr = sum(ranges) / len(ranges)
    progress = (direction * (mark * (1 - direction * slip) - entry) - commission) / risk
    strong = span > 0 and body >= 0.6 * span and wick <= 0.2 * span
    rejected = span > 0 and (body <= -0.5 * span or wick >= 0.5 * span and wick >= 2 * max(abs(body), 0.05 * span))
    near = any(0 <= direction * (price - last.close) <= 0.5 * atr
               for price, _ in barrier_levels(signal["side"], last.close, signal["target_context"]))
    stalled = span > 0 and (abs(body) <= 0.35 * span or direction * (last.close - rows[-2].close) <= 0)
    roles = []
    if policy.startswith("evidence_") and progress >= params["min_r"] and not strong and (rejected or near and stalled):
        roles = ["partial1"]
    elif policy == "ladder_h1":
        roles = [f"partial{index + 1}" for index in range(2) if progress >= (index + 1) * params["step_r"]]
    new_stop = entry if progress >= 1 else None
    if progress >= params["trail_r"]:
        if policy == "trail_swing_h1":
            pivots = [row for index, row in enumerate(rows[2:-2], 2)
                      if (all(row.low < other.low for other in rows[index - 2:index] + rows[index + 1:index + 3])
                          if direction == 1 else
                          all(row.high > other.high for other in rows[index - 2:index] + rows[index + 1:index + 3]))]
            candidate = (pivots[-1].low if direction == 1 else pivots[-1].high) - direction * 0.1 * atr if pivots else entry
        elif policy == "runner_h1":
            recent = [row for row in rows if row.time >= entry_time]
            anchor = max(row.high for row in recent) if direction == 1 else min(row.low for row in recent)
            candidate = anchor - direction * params["atr_multiple"] * atr
        else:
            anchor = min(row.low for row in rows[-3:]) if direction == 1 else max(row.high for row in rows[-3:])
            candidate = anchor - direction * 0.1 * atr
        new_stop = max(entry, candidate) if direction == 1 else min(entry, candidate)
    if new_stop is not None:
        tick = Decimal(1).scaleb(-condition["point_digits"])
        new_stop = Decimal(str(new_stop)).quantize(tick, rounding=ROUND_FLOOR if direction == 1 else ROUND_CEILING)
        if direction * (mark - float(new_stop)) <= 0 or direction * (float(new_stop) - stop) <= 0:
            new_stop = None
    return {"close_roles": roles, "stop": str(new_stop) if new_stop is not None else None,
            "at": last.time + (3600 if signal["management_frame"] == "H1" else 900),
            "reason": "weak_close" if policy.startswith("evidence_") else "scale_out"}
