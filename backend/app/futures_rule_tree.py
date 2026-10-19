"""Bounded, data-only entry rules. No generated code or live-order authority."""

import copy
from statistics import fmean

from .futures_candles import LABELS, PATTERNS, wedge
from .macro_calendar import is_macro_safe
from .futures_rules import atr14, ema50, funding_filter, mean_reversion, rsi14
from .strategy import aggregate, mtf_retest
from .strategy import retest as legacy_retest

FEATURES = ("trend", "breakout", "volume", "compression", "sweep", "rsi", "retest",
            "engulfing", "pin_bar", "star", "soldiers", "ema_cross", "swing_zone",
            *PATTERNS, "wedge", "ema_bounce", "ema_touch", "wick_sweep",
            "retest_seq", "mtf_seq", "band_reclaim", "funding_extreme",
            "compression_current", "ema_cross_n", "trend_optional", "candle_direction",
            "bb_pierce", "volume_dryup", "session_active", "basis_aligned", "macro_safe", "relative_strength", "funding_aligned", "oi_surge",
            "fib_retrace", "rsi_divergence", "fvg", "bos", "session_vwap",
            "pdh_pdl", "adx_regime")
LEAF_FEATURES = tuple(feature for feature in FEATURES if feature != "retest")
FRAMES = {"M15": 900, "H1": 3600, "H4": 14400}
MAX_DEPTH, MAX_NODES = 3, 11
HTF_CONTEXT_FEATURES = tuple(feature for feature in LEAF_FEATURES if feature not in (
    "retest_seq", "mtf_seq", "band_reclaim", "funding_extreme", "trend_optional"))
HTF_ENTRY_FEATURES = (
    "volume", "breakout", "candle_direction", "engulfing", "pin_bar", "star", "soldiers", *PATTERNS)


def validate_rule(rule, depth=1, retest_allowed=False):
    if not isinstance(rule, dict) or depth > MAX_DEPTH:
        raise ValueError("Invalid rule tree/depth")
    if "feature" in rule:
        if (set(rule) != {"feature", "frame"} or rule["feature"] not in FEATURES
                or not isinstance(rule["frame"], str) or rule["frame"] not in FRAMES
                or rule["feature"] in ("pdh_pdl", "adx_regime") and rule["frame"] == "M15"
                or rule["feature"] == "retest" and not retest_allowed):
            raise ValueError("Invalid rule predicate")
        return 1
    operation = rule.get("op")
    keys = {"op", "children", "within"} if operation == "then" else {"op", "children"}
    children = rule.get("children")
    if (set(rule) != keys or operation not in ("all", "any", "then")
            or not isinstance(children, list) or not 2 <= len(children) <= 3):
        raise ValueError("Invalid rule operator")
    if operation == "then" and (len(children) != 2 or type(rule["within"]) is not int
                                or not 1 <= rule["within"] <= 8):
        raise ValueError("Invalid rule confirmation window")
    count = 1 + sum(validate_rule(child, depth + 1,
                                retest_allowed or operation == "then" and index == 1)
                    for index, child in enumerate(children))
    if count > MAX_NODES:
        raise ValueError("Rule tree too complex")
    return count


def leaf(rng, features=LEAF_FEATURES, frames=tuple(FRAMES)):
    return {"feature": rng.choice(features), "frame": rng.choice(frames)}


def nodes(rule, path=()):
    yield path, rule
    for index, child in enumerate(rule.get("children", [])):
        yield from nodes(child, (*path, index))


def replace_node(rule, path, replacement):
    result = copy.deepcopy(rule)
    if not path:
        return copy.deepcopy(replacement)
    parent = result
    for index in path[:-1]:
        parent = parent["children"][index]
    parent["children"][path[-1]] = copy.deepcopy(replacement)
    return result


def mutate_rule(rule, rng, mate=None, features=LEAF_FEATURES, frames=tuple(FRAMES)):
    validate_rule(rule)
    if mate is not None:
        validate_rule(mate)
    for _ in range(24):
        path, node = rng.choice(list(nodes(rule)))
        action = rng.choice(("replace", "add", "remove", "regroup", "cross"))
        replacement = leaf(rng, features, frames)
        if action == "cross" and mate is not None:
            replacement = rng.choice(list(nodes(mate)))[1]
        elif action == "add":
            replacement = {"op": rng.choice(("all", "any", "then")),
                           "children": [copy.deepcopy(node), leaf(rng, features, frames)]}
            if replacement["op"] == "then":
                replacement["within"] = rng.randint(1, 8)
        elif "children" in node:
            if action == "remove":
                replacement = rng.choice(node["children"])
            elif action == "regroup":
                replacement = {"op": "any" if node["op"] == "all" else "all",
                               "children": copy.deepcopy(node["children"])}
        result = replace_node(rule, path, replacement)
        try:
            validate_rule(result)
        except ValueError:
            continue
        if result != rule:
            return result
    return leaf(rng, features, frames)

def validate_htf_rule(rule):
    validate_rule(rule)
    if (rule.get("op") != "all" or len(rule["children"]) != 3
            or rule["children"][0] != {"feature": "trend", "frame": "H4"}):
        raise ValueError("HTF rule requires H4 trend, H1 setup and M15 trigger")
    for index, features, frame in ((1, (*HTF_CONTEXT_FEATURES, "retest"), "H1"),
                                    (2, HTF_ENTRY_FEATURES, "M15")):
        if any(node.get("feature") not in features or node.get("frame") != frame
               for _, node in nodes(rule["children"][index]) if "feature" in node):
            raise ValueError("Invalid HTF setup/entry timeframe or feature")

def mutate_htf_rule(rule, rng):
    for _ in range(24):
        result = copy.deepcopy(rule)
        index = rng.choice((1, 2))
        features, frames = ((HTF_CONTEXT_FEATURES, ("H1",)) if index == 1
                            else (HTF_ENTRY_FEATURES, ("M15",)))
        result["children"][index] = mutate_rule(result["children"][index], rng,
                                               features=features, frames=frames)
        try:
            validate_htf_rule(result)
        except ValueError:
            continue
        if result != rule:
            return result
    return copy.deepcopy(rule)

def is_htf_rule(rule):
    try:
        validate_htf_rule(rule)
    except ValueError:
        return False
    return True


def frame_rows(candles, frame):
    return candles if frame == "M15" else aggregate(candles, FRAMES[frame])


def breakout_level(rule, candles, direction, lookback):
    for _, node in nodes(rule):
        if node.get("feature") != "breakout":
            continue
        rows = frame_rows(candles, node["frame"])
        if len(rows) >= lookback + 1:
            prior = rows[-lookback - 1:-1]
            return max(row.high for row in prior) if direction == 1 else min(row.low for row in prior)
    return None


def body(row):
    return abs(row.close - row.open)

def engulfing(rows, direction):
    previous, last = rows[-2], rows[-1]
    return (direction * (last.close - last.open) > 0 and direction * (previous.close - previous.open) < 0
            and body(last) > body(previous)
            and (last.close >= previous.open and last.open <= previous.close if direction == 1
                 else last.close <= previous.open and last.open >= previous.close))

def pin_bar(rows, direction):
    """Hammer (long) / shooting star (short): long rejection wick, small opposite wick."""
    last = rows[-1]
    top, bottom = last.high - max(last.open, last.close), min(last.open, last.close) - last.low
    rejected, other = (bottom, top) if direction == 1 else (top, bottom)
    return (last.high > last.low and rejected >= 2 * max(body(last), 1e-12)
            and other <= body(last) and direction * (last.close - last.open) >= 0)

def star(rows, direction):
    """Morning (long) / evening (short) star: big candle, small body, reversal past its midpoint."""
    first, middle, last = rows[-3:]
    return (direction * (first.close - first.open) < 0 and body(middle) <= body(first) / 3
            and direction * (last.close - last.open) > 0
            and direction * (last.close - (first.open + first.close) / 2) > 0)

def soldiers(rows, direction):
    """Three white soldiers / three black crows: three closes in sequence, each opening inside the last body."""
    a, b, c = rows[-3:]
    return all(direction * (row.close - row.open) > 0 for row in (a, b, c)) and all(
        direction * (row.close - prior.close) > 0
        and min(prior.open, prior.close) <= row.open <= max(prior.open, prior.close)
        for prior, row in ((a, b), (b, c)))

CANDLE_PATTERNS = {"engulfing": engulfing, "pin_bar": pin_bar, "star": star, "soldiers": soldiers,
                   **PATTERNS}

def ema(values, period):
    value, weight, result = values[0], 2 / (period + 1), []
    for item in values:
        value += weight * (item - value)
        result.append(value)
    return result

def ema_cross(rows, direction):
    closes = [row.close for row in rows]
    fast, slow = ema(closes, 9), ema(closes, 21)
    return direction * (fast[-2] - slow[-2]) <= 0 < direction * (fast[-1] - slow[-1])

def swing_zone(rows, direction, lookback):
    """Close rejects the nearest prior swing pivot (support for long, resistance for short)."""
    window, last = rows[-lookback - 1:-1], rows[-1]
    key = (lambda row: row.low) if direction == 1 else (lambda row: row.high)
    pivots = [key(row) for index, row in enumerate(window[2:-2], 2)
              if all(direction * (key(row) - key(window[index + step])) <= 0 for step in (-2, -1, 1, 2))]
    if not pivots:
        return False
    level = pivots[-1]
    reach = 0.25 * atr14(rows[:-1])
    return (abs(last.low - level) <= reach and last.close > level and last.close > last.open if direction == 1
            else abs(last.high - level) <= reach and last.close < level and last.close < last.open)

def ema_bounce(rows, direction):
    """Trend-aligned EMA21/EMA50 acts as dynamic support/resistance; last candle rejects it."""
    closes = [row.close for row in rows]
    fast, slow, last = ema(closes, 21)[-1], ema(closes, 50)[-1], rows[-1]
    if direction * (fast - slow) <= 0 or direction * (last.close - last.open) <= 0:
        return False
    reach = 0.25 * atr14(rows[:-1])
    if direction == 1:
        return any(last.low <= level + reach and last.low >= level - 2 * reach and last.close > level
                   for level in (fast, slow))
    return any(last.high >= level - reach and last.high <= level + 2 * reach and last.close < level
               for level in (fast, slow))

CLASSIC_FEATURES = ("fib_retrace", "rsi_divergence", "fvg", "bos", "session_vwap",
                    "pdh_pdl", "adx_regime")

def adx_state(rows, period=14):
    """Wilder ADX and DI spread (+DI - -DI) of the last closed row; None when history is short."""
    if len(rows) < 3 * period:
        return None
    tr, plus, minus = [], [], []
    for prev, row in zip(rows, rows[1:]):
        up, down = row.high - prev.high, prev.low - row.low
        tr.append(max(row.high - row.low, abs(row.high - prev.close), abs(row.low - prev.close)))
        plus.append(up if up > down and up > 0 else 0.0)
        minus.append(down if down > up and down > 0 else 0.0)
    smooth = [sum(series[:period]) for series in (tr, plus, minus)]
    dx = []
    for index in range(period, len(tr) + 1):
        if index > period:
            smooth = [old - old / period + series[index - 1] for old, series in zip(smooth, (tr, plus, minus))]
        di_plus, di_minus = ((100 * smooth[1] / smooth[0], 100 * smooth[2] / smooth[0])
                             if smooth[0] > 0 else (0.0, 0.0))
        total = di_plus + di_minus
        dx.append((100 * abs(di_plus - di_minus) / total if total else 0.0, di_plus - di_minus))
    adx = sum(value for value, _ in dx[:period]) / period
    for value, _ in dx[period:]:
        adx = (adx * (period - 1) + value) / period
    return adx, dx[-1][1]

def prior_day_range(rows, seconds):
    """High/low of the complete previous UTC day; None unless every bar of that day is present."""
    if not rows:
        return None
    day = rows[-1].time // 86400 * 86400
    prior = [row for row in rows if day - 86400 <= row.time < day]
    if len(prior) != 86400 // seconds or any(
            row.time != day - 86400 + index * seconds for index, row in enumerate(prior)):
        return None
    return max(row.high for row in prior), min(row.low for row in prior)

def confirmed_pivots(rows, direction, lookback):
    values = [row.high if direction == 1 else row.low for row in rows]
    return [index for index in range(max(2, len(rows) - lookback), len(rows) - 2)
            if all(direction * (values[index] - values[index + offset]) > 0
                   for offset in (-2, -1, 1, 2))]

def fib_zone(rows, direction, lookback):
    ends = confirmed_pivots(rows, direction, lookback)
    starts = confirmed_pivots(rows, -direction, lookback)
    if not ends:
        return None
    end = ends[-1]
    starts = [index for index in starts if index < end]
    if not starts:
        return None
    start = starts[-1]
    origin = rows[start].low if direction == 1 else rows[start].high
    peak = rows[end].high if direction == 1 else rows[end].low
    if direction * (peak - origin) <= 0:
        return None
    if any((row.low <= origin if direction == 1 else row.high >= origin)
           for row in rows[end + 1:]):
        return None
    return (peak - (peak - origin) * 0.5, peak - (peak - origin) * 0.618, rows[end].time)

def fresh_fvg(rows, direction, lookback):
    for end in range(len(rows) - 2, max(1, len(rows) - lookback - 1), -1):
        first, middle, third = rows[end - 2:end + 1]
        lower, upper = ((first.high, third.low) if direction == 1 else (third.high, first.low))
        if lower >= upper or direction * (middle.close - middle.open) <= 0:
            continue
        if any(row.low <= upper and row.high >= lower for row in rows[end + 1:-1]):
            continue
        last = rows[-1]
        if (last.low < lower if direction == 1 else last.high > upper):
            continue
        return lower, upper, third.time
    return None

def structure_level(rows, direction, lookback):
    pivots = confirmed_pivots(rows[:-1], direction, lookback)
    if not pivots:
        return None
    pivot = pivots[-1]
    price = rows[pivot].high if direction == 1 else rows[pivot].low
    if any(direction * (row.close - price) > 0 for row in rows[pivot + 1:-1]):
        return None
    return price, rows[pivot].time

def session_vwap_points(rows, seconds):
    if not rows:
        return []
    start = rows[-1].time // 86400 * 86400
    session = [row for row in rows if row.time >= start]
    if not session or any(row.time != start + index * seconds for index, row in enumerate(session)):
        return []
    volume, weighted, points = 0.0, 0.0, []
    for row in session:
        if row.volume < 0:
            return []
        volume += row.volume
        weighted += (row.high + row.low + row.close) / 3 * row.volume
        if volume > 0:
            points.append({"time": row.time, "value": weighted / volume})
    return points

def classic_matches(feature, rows, direction, lookback, seconds):
    if len(rows) < 2 or feature in ("pdh_pdl", "adx_regime") and seconds < 3600:
        return False
    last = rows[-1]
    if feature == "fib_retrace":
        zone = fib_zone(rows, direction, lookback)
        return zone is not None and min(zone[:2]) <= last.close <= max(zone[:2])
    if feature == "rsi_divergence":
        pivots = [index for index in confirmed_pivots(rows, -direction, lookback) if index >= 14]
        if len(pivots) < 2 or len(rows) - 1 - pivots[-1] > 4:
            return False
        first, second = pivots[-2:]
        old_price = rows[first].low if direction == 1 else rows[first].high
        new_price = rows[second].low if direction == 1 else rows[second].high
        return (direction * (new_price - old_price) < 0
                and direction * (rsi14(rows[:second + 1]) - rsi14(rows[:first + 1])) > 2
                and direction * (last.close - last.open) > 0)
    if feature == "fvg":
        gap = fresh_fvg(rows, direction, lookback)
        return (gap is not None and last.low <= gap[1] and last.high >= gap[0]
                and direction * (last.close - last.open) > 0)
    if feature == "bos":
        level = structure_level(rows, direction, lookback)
        return level is not None and direction * (last.close - level[0]) > 0
    if feature == "adx_regime":
        state = adx_state(rows)
        return state is not None and state[0] > 25 and direction * state[1] > 0
    if feature == "pdh_pdl":
        levels = prior_day_range(rows, seconds)
        if levels is None:
            return False
        level, previous = levels[0 if direction == 1 else 1], rows[-2]
        return (direction * (last.close - level) > 0 and direction * (previous.close - level) <= 0
                and direction * (last.close - last.open) > 0)
    points = session_vwap_points(rows, seconds)
    if len(points) < 2 or points[-2]["time"] != rows[-2].time:
        return False
    return (direction * (rows[-2].close - points[-2]["value"]) <= 0
            and direction * (last.close - points[-1]["value"]) > 0
            and direction * (last.close - last.open) > 0)

def matches(rule, candles, direction, lookback, anchor=None, context=None):
    if not candles:
        return False
    if "op" in rule:
        children = rule["children"]
        if rule["op"] == "all":
            return all(matches(child, candles, direction, lookback, anchor, context) for child in children)
        if rule["op"] == "any":
            return any(matches(child, candles, direction, lookback, anchor, context) for child in children)
        step = (4 if (context or {}).get("htf") and all(
            node["frame"] == "H1" for _, node in nodes(rule) if "feature" in node) else 1)
        for lag in range(step, min(rule["within"] * step, len(candles) - 1) + 1, step):
            prior = candles[:-lag]
            if not matches(children[0], prior, direction, lookback, anchor, context):
                continue
            level = breakout_level(children[0], prior, direction, lookback)
            holding = aggregate(candles, 3600)[-lag // 4:] if step == 4 else candles[-lag:]
            if level is not None and any(direction * (row.close - level) < 0 for row in holding):
                continue
            if matches(children[1], candles, direction, lookback, level, context):
                return True
        return False
    rows = frame_rows(candles, rule["frame"])
    feature = rule["feature"]
    if feature in CLASSIC_FEATURES:
        return classic_matches(feature, rows, direction, lookback, FRAMES[rule["frame"]])
    if feature == "retest":
        if anchor is None or not rows:
            return False
        last = rows[-1]
        return (last.time + FRAMES[rule["frame"]] == candles[-1].time + 900
                and (last.low <= anchor < last.close if direction == 1 else last.high >= anchor > last.close)
                and direction * (last.close - last.open) > 0)
    side = "long" if direction == 1 else "short"
    context = context or {}
    if feature == "candle_direction":
        return bool(rows) and direction * (rows[-1].close - rows[-1].open) > 0
    if feature == "trend_optional":
        return len(rows) < 50 or direction * (candles[-1].close - ema50(rows)) > 0
    if feature == "ema_cross_n":
        if len(rows) < lookback + 2:
            return False
        closes = [row.close for row in rows]
        fast, slow = ema(closes, max(5, lookback // 2)), ema(closes, lookback)
        return direction * (fast[-2] - slow[-2]) <= 0 < direction * (fast[-1] - slow[-1])
    if feature == "compression_current":
        if len(rows) < lookback + 1:
            return False
        prior = rows[-lookback - 1:-1]
        return max(row.high for row in prior) - min(row.low for row in prior) <= 3 * atr14(rows)
    if feature == "retest_seq":
        found = legacy_retest(candles, lookback=lookback, allow_short=True)
        return found is not None and found.side == side
    if feature == "mtf_seq":
        found = mtf_retest(candles, allow_short=True, lookback=lookback)
        return found is not None and found.side == side
    if feature == "band_reclaim":
        found = mean_reversion(candles, allow_short=True, max_er=context.get("max_er", 0.30))
        return found is not None and found.side == side
    if feature == "funding_extreme":
        signal = funding_filter(context.get("funding", []), context.get("funding_threshold", 0.0003))
        found = signal(candles, allow_short=True)
        return found is not None and found.side == side
    if feature == "ema_touch":
        if len(rows) < 50 or not candles:
            return False
        level, last = ema50(rows), rows[-1] if context.get("htf") else candles[-1]
        return (last.low <= level < last.close and last.close > last.open if direction == 1
                else last.high >= level > last.close and last.close < last.open)
    if feature == "wick_sweep":
        history = rows[-lookback - 1:-1] if context.get("htf") else frame_rows(candles[:-1], rule["frame"])[-lookback:]
        last = rows[-1] if context.get("htf") and rows else candles[-1]
        if len(history) < lookback:
            return False
        low, high = min(r.low for r in history), max(r.high for r in history)
        return (last.low < low < last.close < high and last.close > last.open if direction == 1
                else low < last.close < high < last.high and last.close < last.open)
    if feature == "session_active":
        return 7 * 3600 <= (candles[-1].time + 900) % 86400 < 21 * 3600
    if feature == "basis_aligned":
        mark = next((bar for bar in reversed(context.get("fair_candles") or [])
                     if bar.time == candles[-1].time), None)
        price = candles[-1].close
        if mark is None or not (0 < mark.close < float("inf") and 0 < price < float("inf")):
            return False
        return direction * (price / mark.close - 1) <= 0.0005
    if feature == "macro_safe":
        return is_macro_safe(candles[-1].time + 900, 4 * 3600)
    if feature == "funding_aligned":
        funding = context.get("funding") or []
        known = [f for f in funding if f["time"] <= candles[-1].time]
        if not known:
            return False
        latest = max(known, key=lambda record: record["time"])
        if candles[-1].time + 900 - latest["time"] > 86400:
            return False
        rate = latest.get("rate")
        return (isinstance(rate, (float, int)) and abs(rate) < float("inf")
                and direction * rate <= context.get("funding_threshold", 0.0001))
    if feature == "oi_surge":
        oi = context.get("oi_history") or []
        now = candles[-1].time + 900
        known = sorted(p for p in oi if p[0] <= now and 0 < p[1] < float("inf"))
        if not known or now - known[-1][0] > 900:
            return False
        past = [p for p in known if p[0] <= now - 3600]
        if (not past or now - 3600 - past[-1][0] > 900
                or known[-1][1] / past[-1][1] - 1 < 0.01):
            return False
        return bool(rows) and direction * (rows[-1].close - rows[-1].open) > 0
    if feature == "relative_strength":
        if len(rows) < lookback + 1:
            return False
        reference = rows[-lookback - 1:]
        returns = []
        benchmarks = context.get("benchmarks") or {"BTC_USDT": context.get("btc_candles") or []}
        for benchmark in benchmarks.values():
            closed = [bar for bar in benchmark if bar.time <= candles[-1].time]
            peer = frame_rows(closed, rule["frame"])[-lookback - 1:]
            if [bar.time for bar in peer] != [bar.time for bar in reference]:
                return False
            returns.append(peer[-1].close / peer[0].close - 1)
        if not returns or reference[0].close <= 0:
            return False
        strength = reference[-1].close / reference[0].close - 1 - fmean(returns)
        return direction * strength > 0
    if feature in ("bb_pierce", "volume_dryup"):
        if len(rows) < max(lookback, 20) + 4:
            return False
        last = rows[-1]
        if feature == "bb_pierce":
            band = rows[-20:]
            mid = fmean(row.close for row in band)
            dev = 2 * (sum((row.close - mid) ** 2 for row in band) / 20) ** 0.5
            return (last.low < mid - dev < last.close and last.close > last.open if direction == 1
                    else last.high > mid + dev > last.close and last.close < last.open)
        base = rows[-lookback - 3:-3]
        pullback = direction * (rows[-4].close - rows[-1].close) > 0
        average = fmean(row.volume for row in base)
        return pullback and average > 0 and fmean(row.volume for row in rows[-3:]) <= 0.7 * average
    size = max(lookback, 20)
    required = (50 if feature in ("trend", "ema_bounce") else 30 if feature == "ema_cross"
                else size + 1 if feature == "wedge" else max(16, lookback + 1))
    if len(rows) < required:
        return False
    last, prior = rows[-1], rows[-lookback - 1:-1]
    if feature == "trend":
        return direction * (last.close - ema50(rows)) > 0
    if feature == "breakout":
        return last.close > max(row.high for row in prior) if direction == 1 else last.close < min(row.low for row in prior)
    if feature == "volume":
        average = fmean(row.volume for row in prior)
        return average > 0 and last.volume >= 2 * average
    if feature == "compression":
        return max(row.high for row in prior) - min(row.low for row in prior) <= 3 * atr14(rows[:-1])
    if feature == "sweep":
        return (last.low < min(row.low for row in prior) < last.close if direction == 1 else
                last.high > max(row.high for row in prior) > last.close)
    if feature in CANDLE_PATTERNS:
        return CANDLE_PATTERNS[feature](rows, direction)
    if feature == "ema_cross":
        return ema_cross(rows, direction)
    if feature == "ema_bounce":
        return ema_bounce(rows, direction)
    if feature == "wedge":
        return wedge(rows, direction, size, atr14(rows[:-1]))
    if feature == "swing_zone":
        return swing_zone(rows, direction, lookback)
    previous, current = rsi14(rows[:-1]), rsi14(rows)
    return previous <= 30 < current if direction == 1 else previous >= 70 > current


def describe_rule(rule):
    if "feature" in rule:
        labels = {"trend": "Cùng xu hướng EMA50", "breakout": "Đóng phá vùng N nến",
                  "volume": "Volume ≥ 2× trung bình N nến", "compression": "Vùng N nến ≤ 3 ATR14",
                  "sweep": "Quét râu và đóng lại trong vùng N nến", "rsi": "RSI14 thoát quá mua/bán",
                  "retest": "Retest giữ vùng breakout, nến xác nhận cùng chiều",
                  "engulfing": "Nến nhấn chìm (Engulfing)", "pin_bar": "Nến râu từ chối (Hammer/Shooting Star)",
                  "star": "Mô hình sao 3 nến (Morning/Evening Star)",
                  "soldiers": "Ba nến liên tiếp cùng chiều (Soldiers/Crows)",
                  "ema_touch": "Nến M15 chạm EMA50 khung này rồi đóng cùng chiều",
                  "wick_sweep": "Râu M15 quét đỉnh/đáy N nến khung này rồi đóng lại trong vùng",
                  "retest_seq": "Breakout, retest rồi nến xác nhận (logic gốc)",
                  "mtf_seq": "H4 xu hướng, H1 breakout, M15 retest (logic gốc)",
                  "band_reclaim": "Ra ngoài BB20 H1 rồi M15 đóng lại trong dải (logic gốc)",
                  "funding_extreme": "Funding cực đoan ngược chiều, phá vùng 10 nến (logic gốc)",
                  "compression_current": "Vùng N nến ≤ 3 ATR14 gồm nến xác nhận",
                  "ema_cross_n": "EMA nhanh (N/2, tối thiểu 5) cắt EMA chậm (N)",
                  "trend_optional": "Giá M15 cùng EMA50; bỏ lọc khi chưa đủ 50 nến",
                  "candle_direction": "Thân nến cùng chiều lệnh",
                  "bb_pierce": "Râu chọc ra ngoài BB20 (2σ) rồi đóng lại trong dải, nến cùng chiều",
                  "volume_dryup": "Nhịp hồi ngược xu hướng 3 nến với volume ≤ 0.7× trung bình N nến",
                  "session_active": "Khung giờ cố định 07:00-21:00 UTC (không đổi theo DST)",
                  "basis_aligned": "Lệch giá trade/fair cùng nến: long không premium quá 5 bps, short không discount quá 5 bps (không phải index basis)",
                  "macro_safe": "Cách tin vĩ mô lớn (CPI, FOMC, NFP) tối thiểu ±4h",
                  "relative_strength": "Sức mạnh tương đối so với BTC trong N nến",
                  "oi_surge": "Open interest tăng ≥1% trong 1h, nến cùng chiều lệnh",
                  "funding_aligned": "Funding rate thuận chiều (không trả phí cực đoan)",
                  "fib_retrace": "Đóng trong vùng Fibonacci 50-61.8% của swing đã xác nhận 2 nến",
                  "rsi_divergence": "Phân kỳ RSI14 >2 điểm giữa 2 pivot đã xác nhận; pivot mới cách ≤4 nến",
                  "fvg": "Chạm lần đầu FVG 3 nến, không xuyên biên xa, thân nến cùng chiều",
                  "bos": "Đóng phá swing đã xác nhận 2 nến, chưa từng đóng phá trước đó",
                  "pdh_pdl": "Đóng phá đỉnh/đáy ngày UTC trước (cần đủ nến cả ngày), nến cùng chiều",
                  "adx_regime": "ADX14 > 25 và +DI/-DI cùng chiều (chỉ H1/H4)",
                  "session_vwap": "Đóng cắt lại VWAP theo volume nến từ 00:00 UTC (xấp xỉ, không phải từng giao dịch)",
                  "ema_cross": "EMA9 cắt EMA21", "swing_zone": "Giá bật khỏi đỉnh/đáy swing N nến",
                  **LABELS}
        return f'{labels[rule["feature"]]} ({rule["frame"]})'
    descriptions = [describe_rule(child) for child in rule["children"]]
    if rule["op"] == "then":
        frame = "H1" if all(node["frame"] == "H1" for _, node in nodes(rule) if "feature" in node) else "M15"
        return f'({descriptions[0]}) rồi trong {rule["within"]} nến {frame} ({descriptions[1]})'
    return "(" + (" VÀ " if rule["op"] == "all" else " HOẶC ").join(descriptions) + ")"



HTF_RULES = [
    {"op": "all", "children": [
        {"feature": "trend", "frame": "H4"},
        {"feature": "breakout", "frame": "H1"},
        {"feature": "volume", "frame": "M15"},
    ]},
    {"op": "all", "children": [
        {"feature": "trend", "frame": "H4"},
        {"feature": "ema_touch", "frame": "H1"},
        {"feature": "pin_bar", "frame": "M15"},
    ]},
    {"op": "all", "children": [
        {"feature": "trend", "frame": "H4"},
        {"feature": "wedge", "frame": "H1"},
        {"feature": "candle_direction", "frame": "M15"},
    ]},
    {"op": "all", "children": [
        {"feature": "trend", "frame": "H4"},
        {"op": "then", "within": 3, "children": [
            {"feature": "breakout", "frame": "H1"},
            {"feature": "retest", "frame": "H1"},
        ]},
        {"feature": "engulfing", "frame": "M15"},
    ]},
    {"op": "all", "children": [
        {"feature": "trend", "frame": "H4"},
        {"feature": "swing_zone", "frame": "H1"},
        {"op": "any", "children": [
            {"feature": "pin_bar", "frame": "M15"},
            {"feature": "star", "frame": "M15"},
        ]},
    ]},
    {"op": "all", "children": [
        {"feature": "trend", "frame": "H4"},
        {"feature": "compression", "frame": "H1"},
        {"feature": "breakout", "frame": "M15"},
    ]},
    {"op": "all", "children": [
        {"feature": "trend", "frame": "H4"},
        {"feature": "ema_cross_n", "frame": "H1"},
        {"feature": "candle_direction", "frame": "M15"},
    ]},
    {"op": "all", "children": [
        {"feature": "trend", "frame": "H4"},
        {"feature": "wick_sweep", "frame": "H1"},
        {"feature": "pin_bar", "frame": "M15"},
    ]},
    {"op": "all", "children": [
        {"feature": "trend", "frame": "H4"},
        {"feature": "trend", "frame": "H1"},
        {"feature": "pin_bar", "frame": "M15"},
    ]},
    {"op": "all", "children": [
        {"feature": "trend", "frame": "H4"},
        {"feature": "sweep", "frame": "H1"},
        {"feature": "engulfing", "frame": "M15"},
    ]},
    {"op": "all", "children": [
        {"feature": "trend", "frame": "H4"},
        {"feature": "bb_pierce", "frame": "H1"},
        {"feature": "candle_direction", "frame": "M15"},
    ]},
    {"op": "all", "children": [
        {"feature": "trend", "frame": "H4"},
        {"feature": "volume_dryup", "frame": "H1"},
        {"feature": "breakout", "frame": "M15"},
    ]},
    {"op": "all", "children": [
        {"feature": "trend", "frame": "H4"},
        {"op": "all", "children": [
            {"feature": "relative_strength", "frame": "H1"},
            {"feature": "breakout", "frame": "H1"},
        ]},
        {"feature": "volume", "frame": "M15"},
    ]},
    {"op": "all", "children": [
        {"feature": "trend", "frame": "H4"},
        {"op": "all", "children": [
            {"feature": "funding_aligned", "frame": "H1"},
            {"feature": "ema_touch", "frame": "H1"},
        ]},
        {"feature": "pin_bar", "frame": "M15"},
    ]},
    {"op": "all", "children": [
        {"feature": "trend", "frame": "H4"},
        {"op": "all", "children": [
            {"feature": "macro_safe", "frame": "H1"},
            {"feature": "compression", "frame": "H1"},
        ]},
        {"feature": "breakout", "frame": "M15"},
    ]},
]

HTF_RULES.extend([
    {"op": "all", "children": [
        {"feature": "trend", "frame": "H4"},
        {"op": "all", "children": [
            {"feature": feature, "frame": "H1"},
            {"feature": "breakout", "frame": "H1"},
        ]},
        {"feature": "volume", "frame": "M15"},
    ]} for feature in ("session_active", "basis_aligned", "oi_surge")
])

HTF_RULES.extend([
    {"op": "all", "children": [
        {"feature": "trend", "frame": "H4"},
        {"op": "all", "children": [
            {"feature": "adx_regime", "frame": "H1"},
            {"feature": "pdh_pdl", "frame": "H1"},
        ]},
        {"feature": "volume", "frame": "M15"},
    ]},
    {"op": "all", "children": [
        {"feature": "trend", "frame": "H4"},
        {"op": "all", "children": [
            {"feature": "adx_regime", "frame": "H1"},
            {"feature": "ema_touch", "frame": "H1"},
        ]},
        {"feature": "pin_bar", "frame": "M15"},
    ]},
])

HTF_RULES.extend([
    {"op": "all", "children": [
        {"feature": "trend", "frame": "H4"},
        {"feature": "fib_retrace", "frame": "H1"},
        {"feature": "pin_bar", "frame": "M15"},
    ]},
    {"op": "all", "children": [
        {"feature": "trend", "frame": "H4"},
        {"feature": "rsi_divergence", "frame": "H1"},
        {"feature": "engulfing", "frame": "M15"},
    ]},
    {"op": "all", "children": [
        {"feature": "trend", "frame": "H4"},
        {"feature": "fvg", "frame": "H1"},
        {"feature": "candle_direction", "frame": "M15"},
    ]},
    {"op": "all", "children": [
        {"feature": "trend", "frame": "H4"},
        {"feature": "bos", "frame": "H1"},
        {"feature": "volume", "frame": "M15"},
    ]},
    {"op": "all", "children": [
        {"feature": "trend", "frame": "H4"},
        {"feature": "session_vwap", "frame": "H1"},
        {"feature": "pin_bar", "frame": "M15"},
    ]},
])

RULES = HTF_RULES
FAMILY_RULES = {}
FAMILY_PARITY = {}
