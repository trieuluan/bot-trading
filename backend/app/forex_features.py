"""Closed-bar research features for the bidirectional Forex engine."""

import math
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from .exness_sessions import _closed_bucket, regular_session_gap

SMT_REFERENCES = {"EURUSD": "GBPUSD", "GBPUSD": "EURUSD", "AUDUSD": "NZDUSD",
                  "NZDUSD": "AUDUSD", "USDCAD": "USDCHF", "USDCHF": "USDCAD"}
FILTER_DEFAULTS = {
    "adx_regime": {"threshold": 25}, "price_zone": {"lookback": 20},
    "adr_headroom": {"days": 5, "threshold": 0.85},
    "spread_guard": {"lookback": 20, "multiple": 1.5},
    "smt_divergence": {"lookback": 20}, "liquid_session": {},
}
FILTER_BOUNDS = {
    "adx_regime": {"threshold": (20, 25, 30)}, "price_zone": {"lookback": (20, 30, 40)},
    "adr_headroom": {"days": (5, 10), "threshold": (0.75, 0.85, 1.0)},
    "spread_guard": {"lookback": (20, 30), "multiple": (1.25, 1.5, 2.0)},
    "smt_divergence": {"lookback": (10, 20, 30)}, "liquid_session": {},
}


def valid_rows(rows, duration, symbol):
    return bool(rows) and all(
        type(row.time) is int and row.time % duration == 0
        and all(math.isfinite(value) and value > 0 for value in (row.open, row.high, row.low, row.close))
        and row.low <= min(row.open, row.close) <= max(row.open, row.close) <= row.high
        for row in rows
    ) and all(after.time - before.time == duration
              or regular_session_gap(before.time, after.time, duration, symbol)
              for before, after in zip(rows, rows[1:]))


def atr14(rows):
    if len(rows) < 15:
        return 0.0
    return sum(max(curr.high - curr.low, abs(curr.high - prev.close), abs(curr.low - prev.close))
               for prev, curr in zip(rows[-15:-1], rows[-14:])) / 14


def adx14(rows):
    if len(rows) < 42:
        return None
    series = ([], [], [])
    for prev, row in zip(rows, rows[1:]):
        up, down = row.high - prev.high, prev.low - row.low
        series[0].append(max(row.high - row.low, abs(row.high - prev.close), abs(row.low - prev.close)))
        series[1].append(up if up > down and up > 0 else 0.0)
        series[2].append(down if down > up and down > 0 else 0.0)
    smooth = [sum(values[:14]) for values in series]
    values = []
    for index in range(14, len(series[0]) + 1):
        if index > 14:
            smooth = [old - old / 14 + data[index - 1] for old, data in zip(smooth, series)]
        total = smooth[1] + smooth[2]
        values.append(100 * abs(smooth[1] - smooth[2]) / total if total else 0.0)
    result = sum(values[:14]) / 14
    for value in values[14:]:
        result = (result * 13 + value) / 14
    return result


def daily_ranges(hours, as_of, symbol, count):
    """Complete scheduled UTC trading days only; missing bars fail closed."""
    indexed = {row.time: row for row in hours if row.time + 3600 <= as_of}
    if not indexed:
        return [], "daily_history_missing"
    today = as_of // 86400 * 86400
    days = []
    for day in range(today - 86400, max(min(indexed) - 86400, today - 60 * 86400), -86400):
        expected = [stamp for stamp in range(day, day + 86400, 3600)
                    if not _closed_bucket(stamp, 3600, symbol)]
        if len(expected) < 20:
            continue
        rows = [indexed[stamp] for stamp in expected if stamp in indexed]
        if len(rows) != len(expected) or not valid_rows(rows, 3600, symbol):
            return [], "daily_history_missing"
        days.append({"day": day, "high": max(row.high for row in rows), "low": min(row.low for row in rows)})
        if len(days) == count:
            return days, None
    return [], "daily_history_missing"


def setup_result(rows, side, level, invalidation, **audit):
    atr = atr14(rows)
    if atr <= 0:
        return None
    return {"level": level, "invalidation": invalidation, "atr": atr,
            "setup_time": rows[-1].time, "confirmed_h1": rows[-1].time, **audit}


def detect_order_block(rows, side, lookback=20, impulse_mult=1.5):
    if len(rows) < lookback + 6:
        return None
    direction = 1 if side == "long" else -1
    curr = rows[-1]
    for index in range(len(rows) - 2, max(5, len(rows) - lookback - 1), -1):
        block, impulse = rows[index - 1:index + 1]
        prior = rows[index - 5:index]
        level = max(row.high for row in prior) if direction == 1 else min(row.low for row in prior)
        atr = atr14(rows[:index])
        if (atr <= 0 or direction * (block.close - block.open) >= 0
                or direction * (impulse.close - impulse.open) < impulse_mult * atr
                or direction * (impulse.close - level) <= 0):
            continue
        lower, upper = sorted((block.open, block.close))
        invalid = block.low if direction == 1 else block.high
        if any(direction * (row.close - invalid) <= 0 for row in rows[index + 1:]):
            continue
        edge = upper if direction == 1 else lower
        if curr.low <= upper and curr.high >= lower and direction * (curr.close - edge) > 0:
            return setup_result(rows, side, edge, invalid, zone_low=lower, zone_high=upper,
                                origin_time=block.time)
    return None


def sweep_range(rows, side, high, low, **audit):
    curr = rows[-1]
    if side == "long" and curr.low < low < curr.close < high:
        return setup_result(rows, side, low, curr.low, zone_low=low, zone_high=high, **audit)
    if side == "short" and curr.high > high > curr.close > low:
        return setup_result(rows, side, high, curr.high, zone_low=low, zone_high=high, **audit)
    return None


def detect_pdh_pdl(rows, side, symbol="XAUUSD"):
    days, reason = daily_ranges(rows, rows[-1].time, symbol, 1)
    if reason:
        return None
    day = days[0]
    return sweep_range(rows, side, day["high"], day["low"], reference_day=day["day"])


def detect_asia_sweep(rows, side):
    curr = rows[-1]
    day = curr.time // 86400 * 86400
    if not day + 7 * 3600 <= curr.time < day + 10 * 3600:
        return None
    asian = [row for row in rows if day <= row.time < day + 6 * 3600]
    if [row.time for row in asian] != list(range(day, day + 6 * 3600, 3600)):
        return None
    return sweep_range(rows, side, max(row.high for row in asian), min(row.low for row in asian),
                       reference_session={"start": day, "end": day + 6 * 3600, "timezone": "UTC"})


def detect_inside_bar(rows, side, nr7=False):
    if len(rows) < 15:
        return None
    curr, mother = rows[-1], rows[-2]
    if nr7:
        if curr.high - curr.low > min(row.high - row.low for row in rows[-7:]):
            return None
        mother = curr
    elif not (mother.low < curr.low and curr.high < mother.high):
        return None
    return setup_result(rows, side, mother.high if side == "long" else mother.low,
                        mother.low if side == "long" else mother.high,
                        zone_low=mother.low, zone_high=mother.high)


def detect_round_level(rows, side, step=50):
    curr = rows[-1]
    if side == "long":
        level = math.floor(curr.close / step) * step
        if level > 0 and curr.low < level < curr.close and curr.close > curr.open:
            return setup_result(rows, side, level, curr.low, round_step=step)
    else:
        level = math.ceil(curr.close / step) * step
        if curr.high > level > curr.close and curr.close < curr.open:
            return setup_result(rows, side, level, curr.high, round_step=step)
    return None


NEW_H1_DETECTORS = {"order_block": detect_order_block, "pdh_pdl_sweep": detect_pdh_pdl,
                    "asia_sweep": detect_asia_sweep, "inside_bar": detect_inside_bar,
                    "round_level": detect_round_level}


def filter_result(node, candle, hours, higher, side, symbol, market):
    name, params = node["name"], node.get("params", {})
    available = candle.time + 900
    direction = 1 if side == "long" else -1
    audit = {"name": name, "as_of": available, "params": params}
    reason = None
    if name == "session":
        bucket = datetime.fromtimestamp(available, timezone.utc).hour // 6
        audit["bucket"] = bucket
        reason = None if bucket in node["buckets"] else "session_filtered"
    elif name == "liquid_session":
        active = any(8 <= datetime.fromtimestamp(available, ZoneInfo(zone)).hour < 17
                     for zone in ("Europe/London", "America/New_York"))
        reason = None if active else "outside_liquid_session"
    elif name == "adx_regime":
        value = adx14(higher)
        audit["adx"] = value
        reason = "adx_history_missing" if value is None else None if value > params["threshold"] else "adx_weak"
    elif name == "price_zone":
        rows = higher[-params["lookback"]:]
        if len(rows) < params["lookback"]:
            reason = "h4_stale"
        else:
            midpoint = (max(row.high for row in rows) + min(row.low for row in rows)) / 2
            audit["midpoint"] = midpoint
            reason = None if direction * (candle.close - midpoint) < 0 else "price_zone_opposed"
    elif name == "adr_headroom":
        days, reason = daily_ranges(hours, available, symbol, params["days"])
        today = available // 86400 * 86400
        current = [row for row in market.get("minutes", []) if today <= row.time and row.time + 900 <= available]
        expected = [stamp for stamp in range(today, available, 900) if not _closed_bucket(stamp, 900, symbol)]
        if not reason and ([row.time for row in current] != expected or not valid_rows(current, 900, symbol)):
            reason = "daily_history_missing"
        if not reason:
            adr = sum(day["high"] - day["low"] for day in days) / len(days)
            span = max(row.high for row in current) - min(row.low for row in current)
            audit.update(adr=adr, day_range=span, reference_days=days)
            reason = None if adr > 0 and span < params["threshold"] * adr else "adr_exhausted"
    elif name == "spread_guard":
        bids = [row for row in market.get("minutes", []) if row.time < candle.time][-params["lookback"]:]
        asks = market.get("asks", {})
        spreads = [asks[row.time].close - row.close for row in bids if row.time in asks]
        ask = asks.get(candle.time)
        if (len(spreads) != params["lookback"] or not valid_rows(bids, 900, symbol)
                or bids[-1].time != candle.time - 900 or ask is None
                or any(not math.isfinite(value) or value < 0 for value in spreads)):
            reason = "spread_history_missing"
        else:
            average = sum(spreads) / len(spreads)
            spread = ask.close - candle.close
            audit.update(spread=spread, average=average)
            reason = None if math.isfinite(spread) and 0 <= spread <= params["multiple"] * average else "spread_wide"
    elif name == "smt_divergence":
        reference = SMT_REFERENCES.get(symbol)
        audit["reference_symbol"] = reference
        ref = [row for row in market.get("reference_hours", []) if row.time + 3600 <= available]
        count = params["lookback"]
        main, ref = hours[-count - 1:], ref[-count - 1:]
        if reference is None:
            reason = "smt_no_reference"
        elif (len(ref) != count + 1 or len(main) != count + 1
              or [row.time for row in main] != [row.time for row in ref]
              or not valid_rows(ref, 3600, reference) or not valid_rows(main, 3600, symbol)):
            reason = "smt_history_missing"
        else:
            current, previous = main[-1], main[:-1]
            other, old = ref[-1], ref[:-1]
            level = min(row.low for row in previous) if side == "long" else max(row.high for row in previous)
            ref_level = min(row.low for row in old) if side == "long" else max(row.high for row in old)
            extreme = current.low if side == "long" else current.high
            ref_extreme = other.low if side == "long" else other.high
            audit.update(level=level, reference_level=ref_level, reference_time=other.time)
            passed = direction * (extreme - level) < 0 < direction * (current.close - level)
            passed = passed and direction * (ref_extreme - ref_level) >= 0
            reason = None if passed else "smt_not_confirmed"
    return reason, {**audit, "reason": reason, "passed": reason is None}
