"""Decoupled Forex feature detectors for H1 setups and M15 triggers.

Pure functions operating on closed bars; no SL/TP calculation, no lot sizing,
and no live order execution.
"""

import math
from datetime import datetime, timezone
from functools import partial
from zoneinfo import ZoneInfo

from .forex_indicators import ema, rsi


def atr_14(bars):
    if len(bars) < 15:
        return 0.0
    ranges = [
        max(curr.high - curr.low, abs(curr.high - prev.close), abs(curr.low - prev.close))
        for prev, curr in zip(bars[-15:-1], bars[-14:])
    ]
    return sum(ranges) / 14


# ---------------------------------------------------------------------------
# 7 H1 Setups (Run on closed H1 bars)
# ---------------------------------------------------------------------------

def detect_h1_channel_breakout(closed_bars, side, lookback=20):
    """Breakout of Donchian high/low of prior lookback H1 bars."""
    if len(closed_bars) < lookback + 1:
        return None
    window = closed_bars[-lookback - 1:-1]
    curr = closed_bars[-1]
    high = max(r.high for r in window)
    low = min(r.low for r in window)
    atr = atr_14(closed_bars)
    if atr <= 0:
        return None
    if side == "long" and curr.close > high and curr.close > curr.open:
        return {
            "level": high,
            "invalidation": curr.low,
            "atr": atr,
            "setup_time": curr.time,
            "confirmed_h1": curr.time,
        }
    if side == "short" and curr.close < low and curr.close < curr.open:
        return {
            "level": low,
            "invalidation": curr.high,
            "atr": atr,
            "setup_time": curr.time,
            "confirmed_h1": curr.time,
        }
    return None


def detect_h1_retest(closed_bars, side, lookback=20, wait_hours=6):
    """Breakout of prior lookback H1 range followed by retest within wait_bars."""
    if len(closed_bars) < lookback + 2:
        return None
    direction = 1 if side == "long" else -1
    atr = atr_14(closed_bars)
    if atr <= 0:
        return None

    for index in range(len(closed_bars) - 2, max(lookback - 1, len(closed_bars) - wait_hours - 1), -1):
        prior = closed_bars[index - lookback:index]
        breakout = closed_bars[index]
        later = closed_bars[index + 1:]
        level = max(r.high for r in prior) if side == "long" else min(r.low for r in prior)

        if direction * (breakout.close - level) <= 0:
            continue
        if any(direction * (r.close - level) < -0.2 * atr for r in later):
            continue
        if not any(r.low <= level <= r.high for r in later):
            continue

        invalidation = min(r.low for r in later) if side == "long" else max(r.high for r in later)
        return {
            "level": level,
            "invalidation": invalidation,
            "atr": atr,
            "setup_time": breakout.time,
            "confirmed_h1": closed_bars[-1].time,
        }
    return None


def detect_h1_sweep(closed_bars, side, lookback=20):
    """Sweep of prior lookback H1 extreme and close back inside."""
    if len(closed_bars) < lookback + 1:
        return None
    curr = closed_bars[-1]
    prior = closed_bars[-lookback - 1:-1]
    level = min(r.low for r in prior) if side == "long" else max(r.high for r in prior)
    extreme = curr.low if side == "long" else curr.high
    direction = 1 if side == "long" else -1
    atr = atr_14(closed_bars)
    if atr <= 0:
        return None

    if direction * (extreme - level) < 0 and direction * (curr.close - level) > 0:
        return {
            "level": level,
            "invalidation": extreme,
            "atr": atr,
            "setup_time": curr.time,
            "confirmed_h1": curr.time,
        }
    return None


def detect_h1_pullback(closed_bars, side, entry_ema=20, fast_ema=20):
    """Pullback to EMA on H1 in direction of H1 EMA trend."""
    if len(closed_bars) < 50:
        return None
    closes = [r.close for r in closed_bars]
    e_entry = ema(closes, entry_ema)
    e_fast = ema(closes, fast_ema)
    e_slow = ema(closes, 50)
    atr = atr_14(closed_bars)
    if atr <= 0:
        return None
    curr = closed_bars[-1]

    if side == "long" and e_fast > e_slow and curr.low <= e_entry <= curr.high and curr.close > e_entry:
        return {
            "level": e_entry,
            "invalidation": curr.low,
            "atr": atr,
            "setup_time": curr.time,
            "confirmed_h1": curr.time,
        }
    if side == "short" and e_fast < e_slow and curr.low <= e_entry <= curr.high and curr.close < e_entry:
        return {
            "level": e_entry,
            "invalidation": curr.high,
            "atr": atr,
            "setup_time": curr.time,
            "confirmed_h1": curr.time,
        }
    return None


def detect_h1_fvg(closed_bars, side, min_gap=0.1):
    """H1 Fair Value Gap formation probed by current candle."""
    if len(closed_bars) < 18:
        return None
    atr = atr_14(closed_bars)
    if atr <= 0:
        return None
    curr = closed_bars[-1]
    for idx in range(len(closed_bars) - 2, max(2, len(closed_bars) - 6), -1):
        b1, b2, b3 = closed_bars[idx - 2], closed_bars[idx - 1], closed_bars[idx]
        if side == "long" and b3.low > b1.high and b3.low - b1.high >= min_gap * atr and b2.close > b2.open:
            gap_bottom, gap_top = b1.high, b3.low
            if gap_bottom < curr.low <= gap_top and curr.close > gap_top and not any(row.low <= gap_bottom for row in closed_bars[idx + 1:-1]):
                return {
                    "level": gap_bottom,
                    "invalidation": gap_bottom,
                    "atr": atr,
                    "setup_time": b2.time,
                    "confirmed_h1": curr.time,
                }
        elif side == "short" and b1.low > b3.high and b1.low - b3.high >= min_gap * atr and b2.close < b2.open:
            gap_top, gap_bottom = b1.low, b3.high
            if gap_top > curr.high >= gap_bottom and curr.close < gap_bottom and not any(row.high >= gap_top for row in closed_bars[idx + 1:-1]):
                return {
                    "level": gap_top,
                    "invalidation": gap_top,
                    "atr": atr,
                    "setup_time": b2.time,
                    "confirmed_h1": curr.time,
                }
    return None


def detect_h1_squeeze(closed_bars, side, lookback=40, bars=10, ratio=3.0):
    """H1 Low-volatility consolidation followed by directional breakout."""
    if len(closed_bars) < lookback + 1:
        return None
    window = closed_bars[-lookback - 1:-1]
    recent, prior = window[-bars:], window[:-bars]
    if not prior:
        return None
    typical = sum(r.high - r.low for r in prior) / len(prior)
    high, low = max(r.high for r in recent), min(r.low for r in recent)
    atr = atr_14(closed_bars)
    if atr <= 0 or typical <= 0:
        return None
    curr = closed_bars[-1]
    if (high - low) <= ratio * typical:
        if side == "long" and curr.close > high:
            return {
                "level": high,
                "invalidation": low,
                "atr": atr,
                "setup_time": curr.time,
                "confirmed_h1": curr.time,
            }
        if side == "short" and curr.close < low:
            return {
                "level": low,
                "invalidation": high,
                "atr": atr,
                "setup_time": curr.time,
                "confirmed_h1": curr.time,
            }
    return None


def detect_h1_mss(closed_bars, side, lookback=10, wait_bars=6, retrace=0.5):
    """H1 Market structure shift with displacement and retracement."""
    if len(closed_bars) < lookback + wait_bars + 2:
        return None
    atr = atr_14(closed_bars)
    if atr <= 0:
        return None
    curr = closed_bars[-1]
    for idx in range(len(closed_bars) - 2, max(lookback, len(closed_bars) - wait_bars - 1), -1):
        shift = closed_bars[idx]
        prior = closed_bars[idx - lookback:idx]
        high, low = max(r.high for r in prior), min(r.low for r in prior)
        sweep = prior[-1]
        older = prior[:-1]
        if not older:
            continue
        direction = 1 if (sweep.low < min(r.low for r in older) and shift.close > high) else \
            -1 if (sweep.high > max(r.high for r in older) and shift.close < low) else 0
        if (side == "long" and direction != 1) or (side == "short" and direction != -1):
            continue
        extreme = sweep.low if direction == 1 else sweep.high
        target_retrace = shift.close - direction * retrace * abs(shift.close - extreme)
        if any((row.low <= target_retrace if direction == 1 else row.high >= target_retrace)
               for row in closed_bars[idx + 1:-1]):
            continue
        if direction * ((curr.low if direction == 1 else curr.high) - extreme) <= 0:
            continue
        if direction == 1 and curr.low <= target_retrace < curr.close:
            return {
                "level": target_retrace,
                "invalidation": extreme,
                "atr": atr,
                "setup_time": shift.time,
                "confirmed_h1": curr.time,
            }
        if direction == -1 and curr.high >= target_retrace > curr.close:
            return {
                "level": target_retrace,
                "invalidation": extreme,
                "atr": atr,
                "setup_time": shift.time,
                "confirmed_h1": curr.time,
            }
    return None


H1_DETECTORS = {
    "channel_breakout": detect_h1_channel_breakout,
    "retest": detect_h1_retest,
    "sweep": detect_h1_sweep,
    "pullback": detect_h1_pullback,
    "fvg": detect_h1_fvg,
    "squeeze": detect_h1_squeeze,
    "mss": detect_h1_mss,
}


def family_context(family, params, candle, hours, higher=()):
    """Explicit H1 context, computed before the M15 detector. No execution levels."""
    closed = [row for row in hours if row.time + 3600 <= candle.time + 900][-60:]
    if len(closed) < 50:
        return None
    fast = ema([row.close for row in closed], 20)
    slow = ema([row.close for row in closed], 50)
    bias = "long" if fast > slow else "short" if fast < slow else None
    context = {"frame": "H1", "trend_side": bias, "trend_required": params["trend"]}
    if family == "pullback":
        fast = ema([row.close for row in closed], params["fast_ema"])
        context["pullback_side"] = ("long" if fast > slow and closed[-1].close > fast else
                                    "short" if fast < slow and closed[-1].close < fast else None)
    if family in ("range", "router"):
        window = [row for row in closed if row.time + 3600 <= candle.time][-params.get("lookback", 20):]
        high, low = max(row.high for row in window), min(row.low for row in window)
        width = high - low
        context["range"] = {"high": high, "low": low, "width": width,
                            "valid": len(window) == params.get("lookback", 20) and width > 0 and
                            abs(window[-1].close - window[0].close) <= params.get("efficiency", 0.5) * width,
                            "edge": params.get("edge", 0.1)}
    if family == "router":
        h1_range = sum(row.high - row.low for row in closed[-14:]) / 14
        context["router_mode"] = "trend" if abs(fast - slow) > params["separation"] * h1_range else "range"
        context["pullback_side"] = ("long" if fast > slow and closed[-1].close > fast else
                                    "short" if fast < slow and closed[-1].close < fast else None)
    if family == "retest":
        h4 = [row for row in higher if row.time + 14400 <= candle.time + 900]
        if len(h4) < 50 or h4[-1].time + 28800 <= candle.time + 900:
            return None
        average = ema([row.close for row in h4], 50)
        direction = 1 if h4[-1].close > average else -1 if h4[-1].close < average else 0
        context["retest"] = None
        for index in range(len(closed) - 1, params["lookback"] - 1, -1):
            breakout = closed[index]
            available = breakout.time + 3600
            if candle.time < available or candle.time + 900 - available > params["wait_hours"] * 3600:
                continue
            window = closed[index - params["lookback"]:index]
            level = max(row.high for row in window) if direction == 1 else min(row.low for row in window)
            if direction and direction * (breakout.close - level) > 0:
                context["retest"] = {"available": available, "level": level, "direction": direction}
                break
    return context


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

def trigger(candle, history, side, setup, params, context=None, *, family):
    """Legacy recognition only; H1 dependencies supplied explicitly by context."""
    needed = max(20, params.get("lookback", 20) + (params.get("wait_bars", 0) if family == "mss" else 0))
    history = [row for row in history if row.time < candle.time][-96:]
    if (len(history) < needed or history[-1].time != candle.time - 900 or
            any(after.time - before.time != 900 for before, after in zip(history[-needed:], history[-needed + 1:]))):
        return False
    atr = sum(row.high - row.low for row in history[-14:]) / 14
    if atr <= 0:
        return False
    context = context or {}
    if context.get("trend_required") and side != context.get("trend_side"):
        return False
    found = None
    if family in ("orb", "vsa", "mss", "vwap"):
        found, _ = extra_setup({"family": family, **params}, candle, history)
    elif family in ("channel", "sweep"):
        window = history[-params["lookback"]:]
        high, low = max(row.high for row in window), min(row.low for row in window)
        if family == "channel" and params["mode"] == "breakout":
            found = "long" if candle.close > high else "short" if candle.close < low else None
        elif candle.low < low < candle.close:
            found = "long"
        elif candle.high > high > candle.close:
            found = "short"
        if family == "sweep" and (found == "long" and (candle.close <= candle.open or candle.high > high) or
                                  found == "short" and (candle.close >= candle.open or candle.low < low)):
            found = None
    elif family == "rsi":
        value = rsi([row.close for row in history], params["period"])
        if value < params["oversold"] and candle.close > candle.open and candle.close > history[-1].close:
            found = "long"
        elif value > params["overbought"] and candle.close < candle.open and candle.close < history[-1].close:
            found = "short"
    elif family == "fvg":
        first, middle, last = history[-3:]
        if (last.low - first.high >= params["min_gap"] * atr and last.low > first.high and middle.close > middle.open
                and candle.low <= last.low < candle.close and candle.close > candle.open):
            found = "long"
        elif (first.low - last.high >= params["min_gap"] * atr and last.high < first.low and middle.close < middle.open
                and candle.high >= last.high > candle.close and candle.close < candle.open):
            found = "short"
    elif family == "session":
        stamp = datetime.fromtimestamp(candle.time, timezone.utc)
        if not params["start_hour"] <= stamp.hour < params["end_hour"]:
            return False
        day = int(stamp.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
        asian = [row for row in history if day <= row.time < day + params["start_hour"] * 3600]
        if [row.time for row in asian] != list(range(day, day + params["start_hour"] * 3600, 900)):
            return False
        high, low = max(row.high for row in asian), min(row.low for row in asian)
        if any(row.close > high or row.close < low for row in history if row.time >= day + params["start_hour"] * 3600):
            return False
        found = "long" if candle.close > high and candle.close > candle.open else \
                "short" if candle.close < low and candle.close < candle.open else None
    elif family == "squeeze":
        window = history[-params["lookback"]:]
        recent, prior = window[-params["bars"]:], window[:-params["bars"]]
        typical = sum(row.high - row.low for row in prior) / len(prior)
        high, low = max(row.high for row in recent), min(row.low for row in recent)
        if typical > 0 and high - low <= params["ratio"] * typical:
            found = "long" if candle.close > high else "short" if candle.close < low else None
    elif family == "pullback" or family == "router" and context.get("router_mode") == "trend":
        level = ema([row.close for row in history[-60:]], params.get("entry_ema", 20))
        if context.get("pullback_side") == "long" and candle.low <= level < candle.close and candle.close > candle.open:
            found = "long"
        elif context.get("pullback_side") == "short" and candle.high >= level > candle.close and candle.close < candle.open:
            found = "short"
    elif family in ("range", "router"):
        region = context.get("range", {})
        if not region.get("valid"):
            return False
        high, low, width, edge = (region[key] for key in ("high", "low", "width", "edge"))
        if candle.high >= high - edge * width > candle.close and candle.close < candle.open:
            found = "short"
        elif candle.low <= low + edge * width < candle.close and candle.close > candle.open:
            found = "long"
    elif family == "retest":
        region = context.get("retest")
        if not region:
            return False
        available, level, direction = (region[key] for key in ("available", "level", "direction"))
        since = [row for row in history if row.time >= available]
        if candle.time != available and (not since or since[0].time != available):
            return False
        if any(direction * (row.close - level) < 0 for row in since):
            return False
        if any(row.low <= level < row.close if direction == 1 else row.high >= level > row.close for row in since):
            return False
        if candle.low <= level < candle.close and direction == 1 and candle.close > candle.open:
            found = "long"
        elif candle.high >= level > candle.close and direction == -1 and candle.close < candle.open:
            found = "short"
    return found == side


M15_TRIGGERS = {family: partial(trigger, family=family) for family in (
    "channel", "rsi", "fvg", "session", "squeeze", "sweep", "pullback", "range",
    "retest", "router", "orb", "vsa", "mss", "vwap")}
