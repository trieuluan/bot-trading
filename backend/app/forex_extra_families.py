"""Closed-M15 research rules and their shared chart levels; tick volume is a proxy."""

import math
from datetime import datetime
from zoneinfo import ZoneInfo


def extra_setup(genome, candle, history):
    family = genome["family"]
    side, levels = None, []
    history = [row for row in history if row.time < candle.time]
    if not history:
        return side, levels
    if family == "orb":
        zone = ZoneInfo("Europe/London" if genome["session"] == "london" else "America/New_York")
        local = datetime.fromtimestamp(candle.time, zone)
        opening = int(local.replace(hour=8, minute=0, second=0, microsecond=0).timestamp())
        end = opening + genome["range_bars"] * 900
        if not end <= candle.time < end + genome["trade_bars"] * 900:
            return side, levels
        window = [row for row in history if opening <= row.time < end]
        if [row.time for row in window] != list(range(opening, end, 900)):
            return side, levels
        high, low = max(row.high for row in window), min(row.low for row in window)
        levels = [("Opening range high", high, opening), ("Opening range low", low, opening)]
        since = [row for row in history if end <= row.time < candle.time]
        if any(row.close > high or row.close < low for row in since):
            return side, levels
        if candle.close > high and candle.close > candle.open:
            side = "long"
        elif candle.close < low and candle.close < candle.open:
            side = "short"
    elif family == "vsa":
        window = history[-genome["lookback"]:]
        volumes = [getattr(row, "tick_volume", None) for row in [*window, candle]]
        if len(window) != genome["lookback"] or any(
                type(value) not in (int, float) or not math.isfinite(value) or value <= 0 for value in volumes):
            return side, levels
        width = candle.high - candle.low
        levels = [("Absorption high", candle.high, candle.time), ("Absorption low", candle.low, candle.time)]
        if width <= 0 or volumes[-1] < genome["vol_mult"] * sum(volumes[:-1]) / len(window):
            return side, levels
        if min(candle.open, candle.close) - candle.low >= genome["wick_ratio"] * width and candle.close > candle.open:
            side = "long"
        elif candle.high - max(candle.open, candle.close) >= genome["wick_ratio"] * width and candle.close < candle.open:
            side = "short"
    elif family == "mss":
        count = genome["lookback"]
        for index in range(len(history) - 1, max(count, len(history) - genome["wait_bars"]) - 1, -1):
            shift = history[index]
            prior = history[index - count:index]
            high, low = max(row.high for row in prior), min(row.low for row in prior)
            sweep = prior[-1]
            older = prior[:-1]
            direction = 1 if sweep.low < min(row.low for row in older) and shift.close > high else \
                -1 if sweep.high > max(row.high for row in older) and shift.close < low else 0
            if not direction:
                continue
            extreme = sweep.low if direction == 1 else sweep.high
            retrace = shift.close - direction * genome["retrace"] * abs(shift.close - extreme)
            levels = [("MSS structure", high if direction == 1 else low, shift.time),
                      ("MSS retracement", retrace, shift.time), ("MSS invalidation", extreme, sweep.time)]
            since = history[index + 1:]
            if any((row.low <= extreme or row.low <= retrace) if direction == 1 else
                   (row.high >= extreme or row.high >= retrace) for row in since):
                return side, levels
            if direction == 1 and extreme < candle.low <= retrace < candle.close and candle.close > candle.open:
                side = "long"
            elif direction == -1 and extreme > candle.high >= retrace > candle.close and candle.close < candle.open:
                side = "short"
            return side, levels
    elif family == "vwap":
        from .forex_strategies import rsi
        window = history[-genome["lookback"]:]
        volumes = [getattr(row, "tick_volume", None) for row in window]
        if len(window) != genome["lookback"] or any(
                type(value) not in (int, float) or not math.isfinite(value) or value <= 0 for value in volumes):
            return side, levels
        prices = [(row.high + row.low + row.close) / 3 for row in window]
        total = sum(volumes)
        center = sum(price * volume for price, volume in zip(prices, volumes)) / total
        deviation = math.sqrt(sum(volume * (price - center) ** 2 for price, volume in zip(prices, volumes)) / total)
        lower, upper = center - genome["dev_mult"] * deviation, center + genome["dev_mult"] * deviation
        levels = [("Rolling tick-VWAP", center, window[0].time),
                  ("Deviation upper", upper, window[0].time), ("Deviation lower", lower, window[0].time)]
        if deviation <= 0:
            return side, levels
        closes = [row.close for row in history]
        momentum = rsi(closes + [candle.close], 14)
        low_index = min(range(max(14, len(history) - len(window)), len(history)), key=lambda index: history[index].low)
        high_index = max(range(max(14, len(history) - len(window)), len(history)), key=lambda index: history[index].high)
        if (candle.low < lower < candle.close < center and candle.close > candle.open
                and candle.low < history[low_index].low and momentum > rsi(closes[:low_index + 1], 14)):
            side = "long"
        elif (candle.high > upper > candle.close > center and candle.close < candle.open
              and candle.high > history[high_index].high and momentum < rsi(closes[:high_index + 1], 14)):
            side = "short"
    return side, levels
