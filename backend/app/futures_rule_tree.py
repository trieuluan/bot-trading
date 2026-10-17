"""Bounded, data-only entry rules. No generated code or live-order authority."""

import copy
from statistics import fmean

from .futures_rules import atr14, ema50, rsi14
from .strategy import aggregate

FEATURES = ("trend", "breakout", "volume", "compression", "sweep", "rsi", "retest")
FRAMES = {"M15": 900, "H1": 3600, "H4": 14400}
MAX_DEPTH, MAX_NODES = 3, 11


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


def leaf(rng):
    return {"feature": rng.choice(FEATURES[:-1]), "frame": rng.choice(tuple(FRAMES))}


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


def mutate_rule(rule, rng, mate=None):
    validate_rule(rule)
    if mate is not None:
        validate_rule(mate)
    for _ in range(24):
        path, node = rng.choice(list(nodes(rule)))
        action = rng.choice(("replace", "add", "remove", "regroup", "cross"))
        replacement = leaf(rng)
        if action == "cross" and mate is not None:
            replacement = rng.choice(list(nodes(mate)))[1]
        elif action == "add":
            replacement = {"op": rng.choice(("all", "any", "then")),
                           "children": [copy.deepcopy(node), leaf(rng)]}
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
    return {"feature": "trend" if rule.get("feature") != "trend" else "volume", "frame": "H1"}


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


def matches(rule, candles, direction, lookback, anchor=None):
    if not candles:
        return False
    if "op" in rule:
        children = rule["children"]
        if rule["op"] == "all":
            return all(matches(child, candles, direction, lookback, anchor) for child in children)
        if rule["op"] == "any":
            return any(matches(child, candles, direction, lookback, anchor) for child in children)
        for lag in range(1, min(rule["within"], len(candles) - 1) + 1):
            prior = candles[:-lag]
            if not matches(children[0], prior, direction, lookback, anchor):
                continue
            level = breakout_level(children[0], prior, direction, lookback)
            if level is not None and any(direction * (row.close - level) < 0 for row in candles[-lag:]):
                continue
            if matches(children[1], candles, direction, lookback, level):
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
    required = 50 if feature == "trend" else max(16, lookback + 1)
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
    previous, current = rsi14(rows[:-1]), rsi14(rows)
    return previous <= 30 < current if direction == 1 else previous >= 70 > current


def describe_rule(rule):
    if "feature" in rule:
        labels = {"trend": "Cùng xu hướng EMA50", "breakout": "Đóng phá vùng N nến",
                  "volume": "Volume ≥ 2× trung bình N nến", "compression": "Vùng N nến ≤ 3 ATR14",
                  "sweep": "Quét râu và đóng lại trong vùng N nến", "rsi": "RSI14 thoát quá mua/bán",
                  "retest": "Retest giữ vùng breakout, nến xác nhận cùng chiều"}
        return f'{labels[rule["feature"]]} ({rule["frame"]})'
    descriptions = [describe_rule(child) for child in rule["children"]]
    if rule["op"] == "then":
        return f'({descriptions[0]}) rồi trong {rule["within"]} nến M15 ({descriptions[1]})'
    return "(" + (" VÀ " if rule["op"] == "all" else " HOẶC ").join(descriptions) + ")"


RULES = [
    {"op": "all", "children": [{"feature": "trend", "frame": "H4"},
                                {"feature": "breakout", "frame": "M15"}]},
    {"op": "all", "children": [{"feature": "compression", "frame": "H1"},
                                {"op": "any", "children": [{"feature": "sweep", "frame": "M15"},
                                                            {"feature": "rsi", "frame": "M15"}]}]},
    {"op": "then", "within": 3,
     "children": [{"op": "all", "children": [{"feature": "breakout", "frame": "M15"},
                                               {"feature": "volume", "frame": "M15"}]},
                  {"feature": "retest", "frame": "M15"}]},
    {"feature": "breakout", "frame": "M15"},
    {"op": "all", "children": [{"feature": "compression", "frame": "M15"},
                                {"feature": "breakout", "frame": "M15"}]},
    {"op": "then", "within": 5,
     "children": [{"feature": "breakout", "frame": "M15"},
                  {"feature": "retest", "frame": "M15"}]},
]
