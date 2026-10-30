"""Demo-only sizing and closed-candle position management rules."""

from decimal import ROUND_FLOOR, Decimal, InvalidOperation

from .exness import ExnessError
from .forex_indicators import ema


def risk_budget(equity):
    try:
        value = Decimal(str(equity))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ExnessError("Invalid account equity for 1% risk sizing") from exc
    if not value.is_finite() or value <= 0:
        raise ExnessError("Invalid account equity for 1% risk sizing")
    return value / 100

def structure_stop_buffer(rows, multiplier=Decimal("0.25")):
    if len(rows) < 15:
        raise ExnessError("Need 15 closed M15 candles for SL buffer")
    factor = Decimal(str(multiplier))
    if not factor.is_finite() or factor < 0 or factor > Decimal("1.5") or isinstance(multiplier, bool):
        raise ExnessError("Invalid SL buffer multiplier; must be in [0, 1.5]")
    ranges = []
    for previous, row in zip(rows[-15:-1], rows[-14:]):
        high, low, close = (Decimal(str(value)) for value in (row.high, row.low, previous.close))
        if not all(value.is_finite() and value > 0 for value in (high, low, close)) or high < low:
            raise ExnessError("Invalid M15 prices for SL buffer")
        ranges.append(max(high - low, abs(high - close), abs(low - close)))
    return float(sum(ranges) / 14 * factor)

def size_lot(distance, condition, conversion_rate=Decimal("1"), *, equity):
    budget = risk_budget(equity)
    try:
        conv = Decimal(str(condition.get("quote_to_account_loss", condition.get("conversion_rate", conversion_rate))))
        broker_min = Decimal(str(condition["volume_min"]))
        step = Decimal(str(condition["volume_step"]))
        contract = Decimal(str(condition["contract_size"]))
        maximum = Decimal(str(condition["volume_max"]))
        distance = Decimal(str(distance))
        if (not all(value.is_finite() and value > 0 for value in
                    (conv, broker_min, step, contract, maximum, distance))
                or maximum < broker_min):
            raise ExnessError("Invalid broker lot, risk or conversion")
    except (InvalidOperation, ValueError, TypeError, KeyError) as exc:
        raise ExnessError("Invalid broker lot, risk or conversion") from exc
    minimum = max(Decimal("0.01"), broker_min)
    usd_risk = distance * contract * conv
    raw = min(maximum, budget / usd_risk)
    if raw < minimum:
        raise ExnessError("Minimum lot exceeds risk budget; skip signal")
    volume = broker_min + (
        (raw - broker_min) / step
    ).to_integral_value(rounding=ROUND_FLOOR) * step
    if volume < minimum or volume * usd_risk > budget:
        raise ExnessError("No valid lot within risk budget")
    return volume


def split_bodies(body, condition, entry, partial_be=True, *, normalized=False):
    if type(partial_be) is not bool:
        raise ExnessError("Invalid partial_be")
    if not partial_be:
        return [{**body, "role": "runner"}]
    volume = Decimal(body["volume"])
    half = volume / 2
    if not normalized:
        minimum = Decimal(condition["volume_min"])
        step = Decimal(condition["volume_step"])
        half = minimum + ((half - minimum) / step).to_integral_value(rounding=ROUND_FLOOR) * step
    rest = volume - half
    if not normalized and (half < max(minimum, Decimal("0.01")) or rest < max(minimum, Decimal("0.01")) or (rest - minimum) % step):
        return [{**body, "role": "runner"}]
    stop = Decimal(body["stop_loss_price"])
    target = (entry + (entry - stop))
    target = target.quantize(Decimal(1).scaleb(-condition["point_digits"]))
    return [
        {**body, "volume": format(half, "f"), "take_profit_price": format(target, "f"), "role": "tp1"},
        {**body, "volume": format(rest, "f"), "role": "runner"},
    ]


def closed_minutes(frames, now):
    rows = [row for row in frames.get("15m", []) if row.price_type == "bid" and row.time + 900 <= now]
    rows.sort(key=lambda row: row.time)
    if len(rows) < 21 or not 0 <= now - rows[-1].time - 900 <= 120:
        return []
    rows = rows[-60:]
    if any(right.time - left.time != 900 for left, right in zip(rows, rows[1:])):
        return []
    return rows


def exit_reason(side, entry_time, rows):
    if not rows or rows[-1].time < entry_time:
        return None
    last, previous = rows[-1], rows[-2]
    prior_ema = ema([row.close for row in rows[:-1]], 20)
    current_ema = ema([row.close for row in rows], 20)
    crossed = (previous.close >= prior_ema and last.close < current_ema and last.close < last.open
               if side == "buy" else
               previous.close <= prior_ema and last.close > current_ema and last.close > last.open)
    broken = (last.close < min(row.low for row in rows[-4:-1]) and last.close < last.open
              if side == "buy" else
              last.close > max(row.high for row in rows[-4:-1]) and last.close > last.open)
    return "early_risk_exit" if crossed or broken else None


def protective_stop(position, initial_stop, quote, order, condition, rows, partial_done=False):
    entry = Decimal(position["open_price"])
    original = Decimal(initial_stop)
    long = position["direction"] == "buy"
    risk = entry - original if long else original - entry
    if risk <= 0:
        return None
    price = Decimal(str(quote["bid" if long else "ask"]))
    profit_r = (price - entry if long else entry - price) / risk
    if profit_r < 1 and not partial_done:
        return None
    stop, stage = entry, "breakeven"
    if profit_r >= Decimal("1.5") and rows:
        recent = rows[-3:]
        buffer = Decimal(str(sum(row.high - row.low for row in rows[-14:]) / min(14, len(rows)))) / 10
        trailing = (Decimal(str(min(row.low for row in recent))) - buffer if long else
                    Decimal(str(max(row.high for row in recent))) + buffer)
        stop = max(entry, trailing) if long else min(entry, trailing)
        stage = "trailing" if stop != entry else stage
    stop = stop.quantize(Decimal(1).scaleb(-condition["point_digits"]), rounding=ROUND_FLOOR)
    current = Decimal(order["stop_loss_price"])
    if not (current < stop < price if long else price < stop < current):
        return None
    return {"stop": format(stop, "f"), "stage": stage}
