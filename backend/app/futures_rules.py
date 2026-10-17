"""Closed-bar futures rules shared by frozen research and the shadow learner.

Defaults reproduce docs/futures-hypotheses-v1.md exactly; kwargs exist only for the learner."""

from bisect import bisect_left
from statistics import fmean, pstdev

from .strategy import Setup, aggregate


def atr14(candles):
    return fmean(
        max(
            current.high - current.low,
            abs(current.high - previous.close),
            abs(current.low - previous.close),
        )
        for previous, current in zip(candles[-15:-1], candles[-14:])
    )


def ema50(candles):
    value = candles[0].close
    for candle in candles[1:]:
        value += 2 / 51 * (candle.close - value)
    return value


def setup_at_close(last, direction, stop, target, reason):
    if (
        direction * (last.close - stop) <= 0
        or direction * (target - last.close) <= 0
        or target <= 0
    ):
        return None
    return Setup("long" if direction == 1 else "short", last.close, stop, target, reason)


def mean_reversion(candles, allow_short=False, max_er=0.30):
    """4h efficiency <= .30; 1h outside prior BB20/2; first 15m reclaims band, target mean."""
    hours, higher = aggregate(candles, 3600), aggregate(candles, 14400)
    if len(hours) < 21 or len(higher) < 21:
        return None
    last = candles[-1]
    touch = hours[-1]
    if last.time != touch.time + 3600:
        return None
    closes = [candle.close for candle in higher[-21:]]
    path = sum(abs(current - previous) for previous, current in zip(closes, closes[1:]))
    if path == 0 or abs(closes[-1] - closes[0]) / path > max_er:
        return None
    reference = [candle.close for candle in hours[-21:-1]]
    mean, deviation = fmean(reference), pstdev(reference)
    if deviation <= 0:
        return None
    lower, upper = mean - 2 * deviation, mean + 2 * deviation
    buffer = atr14(candles) * 0.1
    if touch.close < lower < last.close and last.close > last.open:
        return setup_at_close(last, 1, min(touch.low, last.low) - buffer, mean, "MR v1")
    if allow_short and touch.close > upper > last.close and last.close < last.open:
        return setup_at_close(last, -1, max(touch.high, last.high) + buffer, mean, "MR v1")
    return None


def liquidity_sweep(candles, allow_short=False, lookback=20):
    """4h EMA50 direction; latest 15m wick sweeps prior 20 closed 1h bars and reclaims."""
    hours = aggregate(candles[:-1], 3600)
    higher = aggregate(candles, 14400)
    if len(hours) < lookback or len(higher) < 50:
        return None
    last = candles[-1]
    lower, upper = (
        min(candle.low for candle in hours[-lookback:]),
        max(candle.high for candle in hours[-lookback:]),
    )
    buffer = atr14(candles) * 0.1
    if (
        higher[-1].close > ema50(higher)
        and last.low < lower < last.close < upper
        and last.close > last.open
    ):
        stop = last.low - buffer
        return setup_at_close(last, 1, stop, last.close + 2 * (last.close - stop), "Sweep v1")
    if (
        allow_short
        and higher[-1].close < ema50(higher)
        and lower < last.close < upper < last.high
        and last.close < last.open
    ):
        stop = last.high + buffer
        return setup_at_close(last, -1, stop, last.close - 2 * (stop - last.close), "Sweep v1")
    return None


def funding_filter(records, threshold=0.0003):
    times = [record["time"] for record in records]

    def signal(candles, allow_short=False):
        last = candles[-1]
        index = bisect_left(times, last.time + 900) - 1
        if index < 0 or len(candles) < 15:
            return None
        record = records[index]
        if last.time + 900 - record["time"] > record["cycle_hours"] * 3600:
            return None
        higher = aggregate(candles, 14400)
        if len(higher) < 50:
            return None
        trend = higher[-1].close - ema50(higher)
        buffer = atr14(candles) * 0.1
        if (
            record["rate"] <= -threshold
            and trend > 0
            and last.close > max(candle.high for candle in candles[-11:-1])
        ):
            stop = min(candle.low for candle in candles[-6:]) - buffer
            return setup_at_close(last, 1, stop, last.close + 2 * (last.close - stop), "Funding v1")
        if (
            allow_short
            and record["rate"] >= threshold
            and trend < 0
            and last.close < min(candle.low for candle in candles[-11:-1])
        ):
            stop = max(candle.high for candle in candles[-6:]) + buffer
            return setup_at_close(
                last, -1, stop, last.close - 2 * (stop - last.close), "Funding v1"
            )
        return None

    return signal


def rsi14(candles):
    if len(candles) < 15:
        return 50.0
    gains, losses = 0.0, 0.0
    for prev, cur in zip(candles[-15:-1], candles[-14:]):
        diff = cur.close - prev.close
        if diff > 0:
            gains += diff
        else:
            losses -= diff
    avg_gain = gains / 14
    avg_loss = losses / 14
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def donchian(candles, lookback=20):
    if len(candles) < lookback + 1:
        return None, None
    history = candles[-lookback - 1 : -1]
    return max(c.high for c in history), min(c.low for c in history)

