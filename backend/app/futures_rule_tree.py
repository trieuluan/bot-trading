"""Bounded, data-only entry rules. No generated code or live-order authority."""

import copy
from statistics import fmean

from .futures_candles import LABELS, PATTERNS, wedge
from .futures_rules import atr14, ema50, funding_filter, mean_reversion, rsi14
from .strategy import aggregate, mtf_retest
from .strategy import retest as legacy_retest

FEATURES = ("trend", "breakout", "volume", "compression", "sweep", "rsi", "retest",
            "engulfing", "pin_bar", "star", "soldiers", "ema_cross", "swing_zone",
            *PATTERNS, "wedge", "ema_bounce", "ema_touch", "wick_sweep",
            "retest_seq", "mtf_seq", "band_reclaim", "funding_extreme",
            "compression_current", "ema_cross_n", "trend_optional", "candle_direction")
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
]

RULES = HTF_RULES
FAMILY_RULES = {}
FAMILY_PARITY = {}
