"""Shared Forex indicators on closed candles."""

def ema(values, period):
    if not values:
        return 0.0
    out = values[0]
    for value in values[1:]:
        out += 2 / (period + 1) * (value - out)
    return out


def rsi(closes, period=14):
    if len(closes) <= period:
        return 50.0
    deltas = [b - a for a, b in zip(closes, closes[1:])]
    gain = sum(max(d, 0) for d in deltas[:period]) / period
    loss = sum(max(-d, 0) for d in deltas[:period]) / period
    for d in deltas[period:]:
        gain = (gain * (period - 1) + max(d, 0)) / period
        loss = (loss * (period - 1) + max(-d, 0)) / period
    return 100.0 if loss == 0 else 100 - 100 / (1 + gain / loss)


def _closed_hours(candle, hours):
    available = candle.time + 900
    return [row for row in hours if row.time + 3600 <= available][-60:]


