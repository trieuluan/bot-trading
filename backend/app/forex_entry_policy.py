"""Virtual entry rules. No broker calls, mutable globals, or price-bar rewriting."""

import copy
import hashlib
import json
import math

SCHEMA = "forex_htf_ast_v4"
QUOTE_GAP = 5  # seconds; entry still requires a fresh (<=2s) quote
POLICIES = ("market", "limit_setup_mid", "stop_trigger_break", "limit_or_stop")
DEFAULTS = {"limit_ratio": 0.5, "stop_buffer_atr": 0.05, "wait_bars": 4}


def entry_node(policy="limit_or_stop", params=None):
    keys = {"market": (), "limit_setup_mid": ("limit_ratio", "wait_bars"),
            "stop_trigger_break": ("stop_buffer_atr", "wait_bars"),
            "limit_or_stop": tuple(DEFAULTS)}
    if not isinstance(policy, str) or policy not in keys:
        raise ValueError("Unknown entry policy")
    return {"frame": "entry", "policy": policy,
            "params": {key: DEFAULTS[key] for key in keys[policy]} if params is None else params}


def validate_entry(node):
    if not isinstance(node, dict) or set(node) != {"frame", "policy", "params"} or node["frame"] != "entry":
        raise ValueError("Invalid entry node")
    expected = entry_node(node["policy"])["params"]
    params = node["params"]
    if not isinstance(params, dict) or set(params) != set(expected):
        raise ValueError("Unused or missing entry params")
    bounds = {"limit_ratio": (0.1, 1), "stop_buffer_atr": (0, 0.25), "wait_bars": (1, 4)}
    for key, value in params.items():
        if type(value) not in (int, float) or not math.isfinite(value) or not bounds[key][0] <= value <= bounds[key][1]:
            raise ValueError("Entry param out of bounds")
        if key == "wait_bars" and type(value) is not int:
            raise ValueError("wait_bars must be integer")


def control_tree(tree):
    from .forex_htf_v2 import SCHEMA as CONTROL_SCHEMA
    result = copy.deepcopy(tree)
    result["schema"] = CONTROL_SCHEMA
    result["root"]["children"].pop(3)
    return result


def validate_tree(tree):
    from .forex_htf_v2 import validate_htf2_tree
    if not isinstance(tree, dict) or set(tree) != {"schema", "root"} or tree["schema"] != SCHEMA:
        raise ValueError("Invalid V4 tree schema")
    root = tree["root"]
    if not isinstance(root, dict) or set(root) != {"op", "children"} or not isinstance(root["children"], list):
        raise ValueError("Invalid V4 root")
    if not 5 <= len(root["children"]) <= 8:
        raise ValueError("V4 requires H4, H1, M15, entry, exit")
    validate_entry(root["children"][3])
    validate_htf2_tree(control_tree(tree))
    return len(root["children"])


def tree_id(tree):
    validate_tree(tree)
    nodes = tree["root"]["children"]
    body = json.dumps({"schema": SCHEMA, "children": nodes[:5] + sorted(nodes[5:], key=lambda node: node["name"])}, sort_keys=True)
    return "hast4_" + hashlib.sha256(body.encode()).hexdigest()[:12]


def with_entry(tree, node=None):
    from .forex_htf_v2 import validate_htf2_tree
    validate_htf2_tree(tree)
    result = copy.deepcopy(tree)
    if result["schema"] == SCHEMA:
        result["root"]["children"][3] = copy.deepcopy(node or entry_node())
    else:
        result["schema"] = SCHEMA
        result["root"]["children"].insert(3, copy.deepcopy(node or entry_node()))
    validate_tree(result)
    return result


def levels(signal, candle, node):
    validate_entry(node)
    if not isinstance(signal, dict) or not isinstance(signal.get("htf_structure"), dict):
        raise ValueError("missing_entry_setup")
    structure = signal["htf_structure"]
    values = (candle.close, candle.high, candle.low, structure.get("level"), structure.get("atr"), signal.get("stop"), signal.get("target"))
    if any(type(value) not in (float, int) or not math.isfinite(value) or value <= 0 for value in values):
        raise ValueError("invalid_entry_levels")
    if signal.get("side") not in ("long", "short"):
        raise ValueError("invalid_entry_side")
    direction = 1 if signal["side"] == "long" else -1
    params, policy = node["params"], node["policy"]
    limit = candle.close + params.get("limit_ratio", 0.5) * (structure["level"] - candle.close)
    stop = (candle.high if direction == 1 else candle.low) + direction * params.get("stop_buffer_atr", 0.05) * structure["atr"]
    if not all(math.isfinite(value) and value > 0 for value in (limit, stop)):
        raise ValueError("invalid_entry_levels")
    if policy in ("limit_setup_mid", "limit_or_stop") and direction * (candle.close - limit) <= 0:
        raise ValueError("limit_wrong_side")
    if policy in ("stop_trigger_break", "limit_or_stop") and direction * (stop - candle.close) <= 0:
        raise ValueError("stop_wrong_side")
    available = signal.get("available_at")
    if type(available) is not int or type(structure.get("setup_time")) is not int or structure["setup_time"] > available:
        raise ValueError("invalid_entry_time")
    expiry = min(available + (120 if policy == "market" else params["wait_bars"] * 900),
                 structure["setup_time"] + 14400 + 900)
    if expiry <= available:
        raise ValueError("setup_expired")
    return {"policy": policy, "limit_price": limit if policy in ("limit_setup_mid", "limit_or_stop") else None,
            "stop_price": stop if policy in ("stop_trigger_break", "limit_or_stop") else None,
            "available_at": available, "expires_at": expiry}


def touched(entry, side, price):
    direction = 1 if side == "long" else -1
    hits = []
    if entry.get("limit_price") is not None and direction * (price - entry["limit_price"]) <= 0:
        hits.append("limit")
    if entry.get("stop_price") is not None and direction * (price - entry["stop_price"]) >= 0:
        hits.append("stop")
    return hits


def observe(payload, quote, now, *, connected=True, allowed=True, invalidated=False):
    result = copy.deepcopy(payload)
    if result["status"] != "waiting":
        return result
    reason = ("control_disabled" if not allowed else "setup_invalidated" if invalidated else
              "stream_disconnected" if not connected else "entry_expired" if now >= result["expires_at"] else None)
    if reason:
        return {**result, "status": "expired" if reason == "entry_expired" else "cancelled", "reason": reason, "ended_at": now}
    valid = (isinstance(quote, dict) and all(type(quote.get(key)) in (int, float) and math.isfinite(quote[key])
             for key in ("time", "received_at", "bid", "ask")))
    if not valid or not (0 < quote["bid"] <= quote["ask"] and 0 <= now - quote["time"] <= 2
                         and 0 <= now - quote["received_at"] <= 2 and quote["time"] >= result["available_at"]):
        return {**result, "status": "cancelled", "reason": "quote_stale", "ended_at": now}
    previous = result.get("last_quote")
    if previous and (now - previous["received_at"] > QUOTE_GAP or quote["time"] - previous["time"] > QUOTE_GAP):
        return {**result, "status": "cancelled", "reason": "quote_gap", "ended_at": now}
    if previous and quote["time"] <= previous["time"]:
        if quote["time"] < previous["time"]:
            return {**result, "status": "cancelled", "reason": "quote_out_of_order", "ended_at": now}
        return result
    if result.get("policy") == "market":
        result.update(status="triggered", winner="market", triggered_at=now,
                      trigger_quote=dict(quote), last_quote=dict(quote))
        return result
    hits = touched(result, result["side"], quote["ask" if result["side"] == "long" else "bid"])
    if not previous and hits:
        return {**result, "status": "cancelled", "reason": "level_already_crossed", "ended_at": now}
    result["last_quote"] = dict(quote)
    if len(hits) > 1:
        return {**result, "status": "cancelled", "reason": "ambiguous_entry", "ended_at": now}
    if hits:
        result.update(status="triggered", winner=hits[0], triggered_at=now, trigger_quote=dict(quote))
    return result


def replay_touch(entry, side, bid, ask):
    """Smallest available bid/ask bar: never invent intrabar event order."""
    if bid.time != ask.time or bid.time < entry["available_at"]:
        return {"status": "excluded", "reason": "invalid_replay_bar"}
    if bid.time >= entry["expires_at"]:
        return {"status": "expired"}
    price_bar = ask if side == "long" else bid
    hits = set(touched(entry, side, price_bar.low) + touched(entry, side, price_bar.high))
    if len(hits) == 2:
        return {"status": "ambiguous", "branches": sorted(hits), "time": bid.time}
    if not hits:
        return {"status": "pending"}
    winner = hits.pop()
    level = entry["limit_price" if winner == "limit" else "stop_price"]
    opening_hits = touched(entry, side, price_bar.open)
    price = price_bar.open if winner in opening_hits else level
    return {"status": "filled", "winner": winner, "entry": price, "time": bid.time,
            "intrabar": winner not in opening_hits}

BASE_14_SETUPS = (
    ("asia_sweep", "engulfing", "evidence_h1"),
    ("order_block", "setup_reclaim", "fixed_structural"),
    ("fvg", "fvg", "barrier_partial_h1"),
    ("channel_breakout", "channel", "partial_be_h1"),
    ("mss", "vsa", "evidence_m15"),
    ("pdh_pdl_sweep", "engulfing", "trail_swing_h1"),
    ("inside_bar", "setup_reclaim", "ladder_h1"),
    ("pullback", "pullback", "runner_h1"),
    ("round_level", "setup_reclaim", "partial_be_m15"),
    ("squeeze", "squeeze", "ladder_h1"),
    ("sweep", "range", "barrier_partial_h1"),
    ("retest", "retest", "trail_swing_h1"),
    ("mss", "mss", "runner_h1"),
    ("order_block", "engulfing", "partial_be_h1"),
)


def make_v4_g0_bundle(symbol="XAUUSD"):
    """14 base setups x 4 entry policies = 56 balanced trees."""
    from .forex_htf_v2 import _tree, default_h1_params, default_m15_params, round_step
    step = round_step(symbol)
    base_trees = []
    for h1_fam, m15_fam, exit_pol in BASE_14_SETUPS:
        h1_p = default_h1_params(h1_fam)
        if h1_fam == "round_level":
            h1_p["step"] = step
        m15_p = default_m15_params(m15_fam)
        base = _tree(h1_fam, h1_p, m15_fam, m15_p, exit_pol)
        base_trees.append(base)
    pop = []
    bundles = {}
    for bundle_idx, base in enumerate(base_trees):
        b_id = f"bundle_{bundle_idx:02d}"
        bundles[b_id] = {"base": base, "tree_ids": {}}
        for pol in POLICIES:
            t = with_entry(base, entry_node(pol))
            tid = tree_id(t)
            pop.append(t)
            bundles[b_id]["tree_ids"][pol] = tid
    return pop, bundles
