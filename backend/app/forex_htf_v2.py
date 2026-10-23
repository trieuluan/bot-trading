"""Forex HTF AST v2: H4 context -> H1 setup -> M15 trigger -> Exit Policy.

Decoupled family detectors; genetic crossover and mutation; shadow only; no live orders.
"""

import copy
import fcntl
import hashlib
import json
import logging
import math
import os
import random
import time
from collections import defaultdict
from decimal import Decimal
from pathlib import Path
from statistics import mean, stdev

from .exness import ExnessError
from .exness_sessions import regular_session_gap
from .exness_targets import structural_target, target_context
from .forex_detectors import H1_DETECTORS, M15_TRIGGERS, family_context
from .forex_exits import BOUNDS as EXIT_BOUNDS
from .forex_exits import DEFAULTS as EXIT_DEFAULTS
from .forex_exits import barrier_runner, parameters
from .forex_features import FILTER_BOUNDS, FILTER_DEFAULTS, NEW_H1_DETECTORS, filter_result

SCHEMA = "forex_htf_ast_v3"
RUNTIME = "htf_v3_only_1"
PROVENANCE = "live_forward_htf_ast3_shadow"
SETUP_MAX_AGE = 14400  # 4h; replay studies override this
SETUP_PERSIST_H1 = 3  # tìm lại setup tối đa 3 nến H1 trước, còn sống nếu chưa bị đóng xuyên mức vô hiệu
MIN_RISK_ATR = Decimal("0.5")  # ponytail: 0.5 ATR H1 keeps tight H1 setups; raise if live stopouts spike
MAX_RISK_ATR = Decimal("3.0")
LEDGER = Path(__file__).resolve().parents[2] / "research-output/exness-htf-ast3-shadow.json"

H4_FEATURES = ("trend_ema50",)
H1_FAMILIES = ("channel_breakout", "squeeze", "retest", "sweep", "pullback", "fvg", "mss", *NEW_H1_DETECTORS)
M15_FAMILIES = (
    "channel", "rsi", "fvg", "session", "squeeze", "sweep",
    "pullback", "range", "retest", "router", "orb", "vsa", "mss", "vwap", "setup_reclaim", "engulfing"
)
EXIT_POLICIES = ("partial_be_h1", "partial_be_m15", "fixed_structural", *EXIT_DEFAULTS)
# policy -> (management frame, legacy split TP1 flag)
EXIT_TRAITS = {
    "partial_be_h1": ("H1", True), "partial_be_m15": ("M15", True),
    "fixed_structural": ("none", False),
    "evidence_h1": ("H1", False), "evidence_m15": ("M15", False),
    "barrier_partial_h1": ("H1", True),
    "runner_h1": ("H1", False),
    "trail_swing_h1": ("H1", False),
    "ladder_h1": ("H1", True),
}

# G0 initial mapping from M15 trigger family to H1 setup
TRIGGER_TO_H1_SETUP = {
    "channel": "channel_breakout",
    "session": "channel_breakout",
    "orb": "channel_breakout",
    "rsi": "sweep",
    "sweep": "sweep",
    "range": "sweep",
    "vwap": "sweep",
    "fvg": "fvg",
    "squeeze": "squeeze",
    "pullback": "pullback",
    "router": "pullback",
    "retest": "retest",
    "vsa": "mss",
    "mss": "mss",
}

# (key, step, low, high) per family
ROUND_STEPS = {"XAUUSD": 50, "JPY": 0.5, "FX": 0.005}

H1_BOUNDS = {
    "order_block": (("lookback", 5, 20, 40), ("impulse_mult", 0.25, 1.5, 2.5)),
    "pdh_pdl_sweep": (), "asia_sweep": (), "inside_bar": (),
    "round_level": (),
    "channel_breakout": (("lookback", 5, 8, 60),),
    "squeeze": (("lookback", 10, 20, 60), ("bars", 2, 5, 15), ("ratio", 0.1, 1.0, 5.0)),
    "retest": (("lookback", 5, 10, 40), ("wait_hours", 1, 2, 12)),
    "sweep": (("lookback", 5, 10, 40),),
    "pullback": (("entry_ema", 5, 10, 40), ("fast_ema", 5, 10, 30)),
    "fvg": (("min_gap", 0.1, 0.0, 1.0),),
    "mss": (("lookback", 2, 5, 15), ("wait_bars", 1, 2, 10), ("retrace", 0.05, 0.3, 0.7)),
}

M15_BOUNDS = {
    "setup_reclaim": (), "engulfing": (),
    "channel": (("lookback", 5, 8, 60),),
    "rsi": (("period", 1, 7, 21), ("oversold", 5, 20, 35), ("overbought", 5, 65, 80)),
    "fvg": (("min_gap", 0.1, 0.0, 1.0),),
    "session": (("start_hour", 1, 6, 9), ("end_hour", 1, 11, 15)),
    "squeeze": (("lookback", 10, 20, 60), ("bars", 2, 5, 15), ("ratio", 0.1, 1.0, 5.0)),
    "sweep": (("lookback", 5, 10, 40),),
    "pullback": (("entry_ema", 5, 10, 40), ("fast_ema", 5, 10, 30)),
    "range": (("lookback", 5, 10, 40), ("edge", 0.05, 0.05, 0.25), ("efficiency", 0.1, 0.2, 0.6)),
    "retest": (("lookback", 5, 10, 40), ("wait_hours", 2, 2, 12)),
    "router": (("separation", 0.1, 0.2, 1.0),),
    "orb": (("range_bars", 1, 1, 4), ("trade_bars", 2, 4, 12)),
    "vsa": (("lookback", 5, 10, 30), ("vol_mult", 0.25, 1.25, 3.0), ("wick_ratio", 0.05, 0.5, 0.8)),
    "mss": (("lookback", 2, 5, 15), ("wait_bars", 1, 2, 10), ("retrace", 0.05, 0.3, 0.7)),
    "vwap": (("lookback", 5, 20, 50), ("dev_mult", 0.25, 1.5, 3.5)),
}


def validate_params(bounds_dict, family, params):
    if not isinstance(params, dict):
        raise ValueError(f"Params must be dict for family {family}")
    bounds = bounds_dict.get(family, ())
    allowed = {key for key, *_ in bounds}
    if bounds_dict is M15_BOUNDS:
        allowed |= ({"trend"} if family not in ("setup_reclaim", "engulfing") else set()) | ({"mode"} if family == "channel" else set()) | ({"session"} if family == "orb" else set())
        if family not in ("setup_reclaim", "engulfing") and not isinstance(params.get("trend"), bool):
            raise ValueError("M15 params require boolean trend")
        if family == "channel" and params.get("mode") not in ("breakout", "fade"):
            raise ValueError("Invalid channel mode")
        if family == "orb" and params.get("session") not in ("london", "new_york"):
            raise ValueError("Invalid orb session")
    if bounds_dict is H1_BOUNDS and family == "inside_bar":
        allowed.add("nr7")
        if type(params.get("nr7")) is not bool:
            raise ValueError("Invalid NR7 mode")
    if bounds_dict is H1_BOUNDS and family == "round_level":
        allowed.add("step")
        if params.get("step") not in ROUND_STEPS.values():
            raise ValueError("Invalid round step")
    if set(params) - allowed:
        raise ValueError(f"Unused params for {family}: {sorted(set(params) - allowed)}")
    for key, step, low, high in bounds:
        if key not in params:
            raise ValueError(f"Missing param {key} for {family}")
        val = params[key]
        if type(val) not in (int, float) or not math.isfinite(val) or not low <= val <= high:
            raise ValueError(f"Param {key}={val} out of bounds [{low}, {high}] for {family}")
        if isinstance(step, int) and type(val) is not int:
            raise ValueError(f"Param {key} must be integer")
    if bounds_dict is M15_BOUNDS and family == "squeeze" and params["bars"] >= params["lookback"]:
        raise ValueError("Invalid compression window")
    if bounds_dict is M15_BOUNDS and family == "session" and params["start_hour"] >= params["end_hour"]:
        raise ValueError("Invalid session window")
    if bounds_dict is H1_BOUNDS and family == "squeeze" and params["bars"] >= params["lookback"]:
        raise ValueError("Invalid compression window")


def validate_htf2_tree(tree):
    if isinstance(tree, dict) and tree.get("schema") == "forex_htf_ast_v4":
        from .forex_entry_policy import validate_tree
        return validate_tree(tree)
    if not isinstance(tree, dict) or set(tree) != {"schema", "root"} or tree["schema"] != SCHEMA:
        raise ValueError("Invalid HTF v2 schema")
    root = tree["root"]
    if (not isinstance(root, dict) or set(root) != {"op", "children"}
            or root.get("op") != "all" or not isinstance(root.get("children"), list)):
        raise ValueError("Invalid HTF v2 root")
    children = root["children"]
    if not 4 <= len(children) <= 7:
        raise ValueError("HTF v2 tree requires H4 context, H1 setup, M15 trigger, exit policy, and optional filter")

    h4, h1, m15, exit_node = children[:4]
    if (not isinstance(h4, dict) or set(h4) != {"frame", "feature"}
            or h4["frame"] != "H4" or h4["feature"] not in H4_FEATURES):
        raise ValueError("Invalid H4 context node")

    if (not isinstance(h1, dict) or set(h1) != {"frame", "family", "params"}
            or h1["frame"] != "H1" or h1["family"] not in H1_FAMILIES):
        raise ValueError("Invalid H1 setup node")
    validate_params(H1_BOUNDS, h1["family"], h1["params"])

    if (not isinstance(m15, dict) or set(m15) != {"frame", "family", "params"}
            or m15["frame"] != "M15" or m15["family"] not in M15_FAMILIES):
        raise ValueError("Invalid M15 trigger node")
    validate_params(M15_BOUNDS, m15["family"], m15["params"])

    if (not isinstance(exit_node, dict) or set(exit_node) not in ({"frame", "policy"}, {"frame", "policy", "params"})
            or exit_node["frame"] != "exit" or exit_node["policy"] not in EXIT_POLICIES):
        raise ValueError("Invalid exit policy node")
    if exit_node["policy"] in EXIT_DEFAULTS:
        try:
            parameters(exit_node["policy"], exit_node.get("params"))
        except ExnessError as exc:
            raise ValueError(str(exc)) from exc
    elif "params" in exit_node:
        raise ValueError("Unused exit params")

    seen = set()
    for flt in children[4:]:
        if not isinstance(flt, dict) or flt.get("frame") != "filter" or not isinstance(flt.get("name"), str):
            raise ValueError("Invalid filter")
        name = flt["name"]
        if name in seen:
            raise ValueError("Duplicate filter")
        seen.add(name)
        if name == "session":
            if (set(flt) != {"frame", "name", "buckets"} or not isinstance(flt["buckets"], list)
                    or not flt["buckets"] or not all(type(bucket) is int and 0 <= bucket <= 3 for bucket in flt["buckets"])
                    or len(set(flt["buckets"])) != len(flt["buckets"])):
                raise ValueError("Invalid session filter")
        elif name in FILTER_DEFAULTS:
            if set(flt) != {"frame", "name", "params"} or not isinstance(flt["params"], dict) or set(flt["params"]) != set(FILTER_DEFAULTS[name]):
                raise ValueError("Invalid filter params")
            if any(type(value) not in (int, float) or value not in FILTER_BOUNDS[name][key] for key, value in flt["params"].items()):
                raise ValueError("Filter param out of bounds")
        else:
            raise ValueError("Unknown filter")
    return len(children)


def tree_id(tree):
    if isinstance(tree, dict) and tree.get("schema") == "forex_htf_ast_v4":
        from .forex_entry_policy import tree_id as entry_tree_id
        return entry_tree_id(tree)
    validate_htf2_tree(tree)
    nodes = tree["root"]["children"]
    body = json.dumps({"schema": tree["schema"], "children": nodes[:4] + sorted(nodes[4:], key=lambda node: node["name"])}, sort_keys=True)
    return "hast3_" + hashlib.sha256(body.encode()).hexdigest()[:12]


def describe(tree):
    if tree.get("schema") == "forex_htf_ast_v4":
        from .forex_entry_policy import control_tree
        return describe(control_tree(tree)) + " + " + tree["root"]["children"][3]["policy"]
    c = tree["root"]["children"]
    side = "Hai chiều · thuận H4"
    h1_name = c[1]["family"]
    m15_name = c[2]["family"]
    exit_policy = c[3]["policy"]
    exit_map = {
        "partial_be_h1": "Quản lý H1 (TP1 +1R, BE)",
        "partial_be_m15": "Quản lý M15 (TP1 +1R, BE)",
        "fixed_structural": "Cấu trúc thuần (không BE)",
        "evidence_h1": "Chốt khi nến H1 yếu, giữ runner tới cản",
        "evidence_m15": "Chốt khi nến M15 yếu, giữ runner tới cản",
        "barrier_partial_h1": "Chốt tại cản H1, runner tới cản xa hơn",
        "runner_h1": "Giữ 100%, trail ATR H1",
        "ladder_h1": "Chốt từng nấc R, trail H1",
    }
    return f"H4 EMA50 ({side}) + H1 {h1_name} + M15 {m15_name} + {exit_map.get(exit_policy, exit_policy)}"


SETUP_NAMES = {"channel_breakout": "Phá kênh", "squeeze": "Nén biên độ", "retest": "Retest", "sweep": "Quét thanh khoản",
               "pullback": "Pullback EMA", "fvg": "FVG", "mss": "Đổi cấu trúc (MSS)", "order_block": "Order Block",
               "pdh_pdl_sweep": "Quét đỉnh/đáy ngày trước", "asia_sweep": "Quét hộp Á UTC",
               "inside_bar": "Inside Bar / NR7", "round_level": "Từ chối mốc tròn"}
TRIGGER_NAMES = {"channel": "Phá kênh", "rsi": "RSI", "fvg": "FVG", "session": "Phiên", "squeeze": "Nén", "sweep": "Quét",
                 "pullback": "Pullback", "range": "Biên", "retest": "Retest", "router": "Router", "orb": "ORB",
                 "vsa": "VSA (tick volume)", "mss": "MSS", "vwap": "VWAP (tick volume)",
                 "setup_reclaim": "Lấy lại level", "engulfing": "Nến nhấn chìm"}
POLICY_NAMES = {"partial_be_h1": "Chốt 1 phần · BE H1", "partial_be_m15": "Chốt 1 phần · BE M15",
                "fixed_structural": "Cấu trúc thuần", "evidence_h1": "Thoát theo nến H1", "evidence_m15": "Thoát theo nến M15",
                "barrier_partial_h1": "Chốt tại cản H1", "runner_h1": "Giữ runner H1", "ladder_h1": "Chốt từng nấc",
                "trail_swing_h1": "Gồng 100% · trailing swing H1"}

def tree_label(tree, generation=0):
    """Tên đọc được cho UI; id chỉ là hậu tố tra cứu. Tham số khác mặc định được nêu để cây con không trùng tên."""
    c = tree["root"]["children"]
    v4 = tree["schema"] == "forex_htf_ast_v4"
    h1 = c[1]["family"]
    m15 = c[2]["family"]
    policy = c[4]["policy"] if v4 else c[3]["policy"]
    changed = [f"{k} {v}" for k, v in c[1].get("params", {}).items() if v != default_h1_params(h1).get(k)]
    changed += [f"{k} {v}" for k, v in c[2].get("params", {}).items() if v != M15_DEFAULTS[m15].get(k)]
    changed += [f"{k} {v}" for k, v in c[4 if v4 else 3].get("params", {}).items() if v != EXIT_DEFAULTS.get(policy, {}).get(k)]
    entry_str = f" · {c[3]['policy']}" if v4 else ""
    title = f"Hai chiều · thuận H4 · H1 {SETUP_NAMES[h1]} → M15 {TRIGGER_NAMES[m15]}{entry_str}"
    policy_name = POLICY_NAMES.get(policy, policy)
    prefix = "hast4_" if v4 else "hast3_"
    return {"title": title, "policy": policy_name, "params": ", ".join(changed), "generation": generation,
            "short": tree_id(tree).removeprefix(prefix)[:4],
            "full": f"{title} · {policy_name}" + (f" · {', '.join(changed)}" if changed else "") + f" · G{generation} #{tree_id(tree)[6:10]}"}

def default_h1_params(family):
    if family in NEW_H1_DETECTORS:
        return {"order_block": {"lookback": 20, "impulse_mult": 1.5}, "pdh_pdl_sweep": {},
                "asia_sweep": {}, "inside_bar": {"nr7": False}, "round_level": {"step": 50}}[family]
    return {"channel_breakout": {"lookback": 20}, "squeeze": {"lookback": 40, "bars": 10, "ratio": 3.0},
            "fvg": {"min_gap": 0.1}, "pullback": {"entry_ema": 20, "fast_ema": 20},
            "retest": {"lookback": 20, "wait_hours": 6},
            "mss": {"lookback": 10, "wait_bars": 6, "retrace": 0.5}, "sweep": {"lookback": 20}}[family]


M15_DEFAULTS = {
    "setup_reclaim": {}, "engulfing": {},
    "channel": {"mode": "breakout", "lookback": 20, "trend": True},
    "rsi": {"period": 14, "oversold": 30, "overbought": 70, "trend": False},
    "fvg": {"min_gap": 0.0, "trend": True},
    "session": {"start_hour": 7, "end_hour": 12, "trend": True},
    "squeeze": {"lookback": 40, "bars": 10, "ratio": 3.0, "trend": True},
    "sweep": {"lookback": 20, "trend": False},
    "pullback": {"entry_ema": 20, "fast_ema": 20, "trend": True},
    "range": {"lookback": 20, "edge": 0.1, "efficiency": 0.5, "trend": False},
    "retest": {"lookback": 20, "wait_hours": 12, "trend": True},
    "router": {"separation": 0.5, "trend": False},
    "orb": {"session": "london", "range_bars": 2, "trade_bars": 8, "trend": True},
    "vsa": {"lookback": 20, "vol_mult": 1.5, "wick_ratio": 0.6, "trend": False},
    "mss": {"lookback": 10, "wait_bars": 6, "retrace": 0.5, "trend": False},
    "vwap": {"lookback": 30, "dev_mult": 2.5, "trend": False},
}

def default_m15_params(family):
    return copy.deepcopy(M15_DEFAULTS[family])

def round_step(symbol):
    """Stored in the tree; fixed per instrument class, never inferred from digits."""
    return ROUND_STEPS["XAUUSD"] if symbol == "XAUUSD" else ROUND_STEPS["JPY"] if symbol.endswith("JPY") else ROUND_STEPS["FX"]

def _tree(h1_family, h1_params, m15_family, m15_params, policy, filters=()):
    tree = {"schema": SCHEMA, "root": {"op": "all", "children": [
        {"frame": "H4", "feature": "trend_ema50"},
        {"frame": "H1", "family": h1_family, "params": copy.deepcopy(h1_params)},
        {"frame": "M15", "family": m15_family, "params": copy.deepcopy(m15_params)},
        {"frame": "exit", "policy": policy},
        *copy.deepcopy(list(filters))]}}
    validate_htf2_tree(tree)
    return tree

def _filter(name):
    return {"frame": "filter", "name": name, "params": dict(FILTER_DEFAULTS[name])}

G0_EXITS = ("partial_be_h1", "partial_be_m15", "fixed_structural", "evidence_h1", "evidence_m15",
            "barrier_partial_h1", "runner_h1", "trail_swing_h1", "ladder_h1")

def g0_trees(symbol="XAUUSD"):
    """56 bidirectional trees: 28 base + 20 new setups + 8 filter trees; exits rotate over all 9 policies."""
    def h1_params(setup):
        return {"step": round_step(symbol)} if setup == "round_level" else default_h1_params(setup)
    trees = []
    for trigger in M15_FAMILIES[:14]:
        setup = TRIGGER_TO_H1_SETUP[trigger]
        for _ in range(2):
            policy = G0_EXITS[len(trees) % len(G0_EXITS)]
            trees.append(_tree(setup, h1_params(setup), trigger, default_m15_params(trigger), policy))
    for setup in NEW_H1_DETECTORS:
        for trigger in ("setup_reclaim", "engulfing"):
            for _ in range(2):
                policy = G0_EXITS[len(trees) % len(G0_EXITS)]
                trees.append(_tree(setup, h1_params(setup), trigger, {}, policy))
    filter_sets = (("adx_regime",), ("price_zone",), ("adr_headroom",), ("spread_guard",), ("smt_divergence",),
                   ("liquid_session",), ("adx_regime", "price_zone"), ("spread_guard", "adr_headroom"))
    setups = (("order_block", "setup_reclaim"), ("pdh_pdl_sweep", "engulfing"), ("asia_sweep", "setup_reclaim"),
              ("inside_bar", "engulfing"), ("round_level", "setup_reclaim"), ("fvg", "setup_reclaim"),
              ("sweep", "engulfing"), ("mss", "setup_reclaim"))
    for names, (setup, trigger) in zip(filter_sets, setups):
        m15 = default_m15_params(trigger) if trigger in M15_DEFAULTS else {}
        trees.append(_tree(setup, h1_params(setup), trigger, m15, G0_EXITS[len(trees) % len(G0_EXITS)],
                           [_filter(n) for n in names]))
    assert len(trees) == 56 and len({tree_id(tree) for tree in trees}) == 56
    return trees

# ---------------------------------------------------------------------------
# Data handling & Evaluation
# ---------------------------------------------------------------------------

def _closed_rows(candle, rows, duration, count, symbol="XAUUSD"):
    closed = sorted((row for row in rows if row.time + duration <= candle.time + 900), key=lambda row: row.time)
    window = closed[-count:]
    if len(window) < count or window[-1].time + 2 * duration <= candle.time + 900:
        return []
    if any(after.time - before.time != duration and not regular_session_gap(before.time, after.time, duration, symbol)
           for before, after in zip(window, window[1:])):
        return []
    if any(not all(math.isfinite(value) and value > 0 for value in (row.open, row.high, row.low, row.close))
           or row.low > min(row.open, row.close) or row.high < max(row.open, row.close)
           or row.time % duration != 0 for row in window):
        return []
    return closed


def _h4_context(candle, higher, symbol="XAUUSD"):
    closed = _closed_rows(candle, higher, 14400, 50, symbol)
    if not closed:
        return None
    ema_val = closed[0].close
    for row in closed[1:]:
        ema_val += 2 / 51 * (row.close - ema_val)
    last = closed[-1].close
    if last == ema_val:
        return None
    return "long" if last > ema_val else "short"


def detect_setup(node, closed, side, symbol):
    """Shared by evaluator and chart preview; PDH/PDL needs full-day history, others use 60 H1.

    Persist: nếu nến vừa đóng không phải setup, lùi tối đa SETUP_PERSIST_H1 nến; setup cũ chỉ sống khi
    các nến H1 sau đó chưa đóng xuyên mức vô hiệu. Hết hạn vẫn do SETUP_MAX_AGE quyết định.
    """
    detectors = {**H1_DETECTORS, **NEW_H1_DETECTORS}
    direction = 1 if side == "long" else -1
    for back in range(SETUP_PERSIST_H1 + 1):
        sub = closed[:len(closed) - back] if back else closed
        if not sub:
            return None
        if node["family"] == "pdh_pdl_sweep":
            setup = NEW_H1_DETECTORS["pdh_pdl_sweep"](sub, side, symbol=symbol)
        else:
            setup = detectors[node["family"]](sub[-60:], side, **node["params"])
        if not setup:
            continue
        if any(direction * (row.close - setup["invalidation"]) < 0 for row in closed[len(sub):]):
            return None
        return setup
    return None

def diagnose(tree, candle, hours, minutes, higher=(), ask=None, slippage_bps=0, commission=0, symbol="XAUUSD",
             market=None):
    """Diagnose bidirectional V3 tree on closed bars. Returns (signal, None) or (None, reason)."""
    validate_htf2_tree(tree)
    if tree["schema"] == "forex_htf_ast_v4":
        from .forex_entry_policy import control_tree, levels
        signal, reason = diagnose(control_tree(tree), candle, hours, minutes, higher, ask, slippage_bps,
                                  commission, symbol, market)
        if signal is None:
            return None, reason
        try:
            virtual = levels(signal, candle, tree["root"]["children"][3])
        except ValueError as exc:
            return None, str(exc)
        return {**signal, "strategy": tree_id(tree), "htf_ast_v4": True, "entry_policy": virtual}, None
    if (candle.time % 900 or not all(math.isfinite(value) and value > 0 for value in
                                   (candle.open, candle.high, candle.low, candle.close))
            or candle.low > min(candle.open, candle.close) or candle.high < max(candle.open, candle.close)):
        return None, "invalid_m15"

    c = tree["root"]["children"]
    _, h1_node, m15_node, exit_node = c[:4]

    # 1. H4 Context
    side = _h4_context(candle, higher, symbol)
    if not side:
        return None, "h4_stale"

    # 2. H1 Setup
    closed_all = _closed_rows(candle, hours, 3600, 50, symbol)
    closed_h1 = closed_all[-60:]
    if not closed_h1:
        return None, "h1_not_closed"
    setup = detect_setup(h1_node, closed_all, side, symbol)
    if not setup:
        return None, "no_h1_setup"

    # Setup expiration check (4 hours = 14400s max age)
    if candle.time - setup["setup_time"] > SETUP_MAX_AGE:
        return None, "h1_setup_expired"
    if candle.time < setup["confirmed_h1"] + 3600:
        return None, "h1_not_closed"

    direction = Decimal(1) if side == "long" else Decimal(-1)
    entry = Decimal(str(candle.close))
    atr = Decimal(str(setup["atr"]))
    invalidation = Decimal(str(setup["invalidation"]))
    tick = Decimal("0.001") if symbol == "XAUUSD" or symbol.endswith("JPY") else Decimal("0.00001")
    stop = (invalidation - direction * Decimal("0.25") * atr).quantize(tick)
    risk = direction * (entry - stop)
    if risk <= 0:
        return None, "no_h1_invalidation"
    if risk < MIN_RISK_ATR * atr:
        return None, "sl_too_tight"
    if risk > MAX_RISK_ATR * atr:
        return None, "sl_too_wide"

    # 3. M15 Trigger
    history = [r for r in minutes if r.time < candle.time][-96:]
    if not history or history[-1].time != candle.time - 900:
        return None, "m15_gap"
    m15_func = M15_TRIGGERS.get(m15_node["family"])
    if not m15_func:
        return None, "unknown_m15_detector"
    h1_context = family_context(m15_node["family"], m15_node["params"], candle, hours, higher)
    if h1_context is None:
        return None, "h1_context_missing"
    triggered = m15_func(candle, history, side, setup, m15_node["params"], h1_context)
    if not triggered:
        return None, "no_m15_trigger"

    # 4. Filters: fail closed on missing data.
    market = {"minutes": [*history, candle], "asks": {} if ask is None else {ask.time: ask}, **(market or {})}
    closed_higher = [row for row in higher if row.time + 14400 <= candle.time + 900]
    filters = []
    # Cổng ADX H4 mặc định cho mọi cây (replay: lọc thị trường đi ngang); cây đã có adx_regime dùng ngưỡng riêng.
    gates = [] if any(f["name"] == "adx_regime" for f in c[4:]) else [_filter("adx_regime")]
    for flt in [*gates, *c[4:]]:
        reason, audit = filter_result(flt, candle, closed_all, closed_higher, side, symbol, market)
        filters.append(audit)
        if reason:
            return None, reason

    # 5. Structural TP & R/R
    try:
        ctx = target_context(candle, hours, higher)
        runner = barrier_runner(side, entry, stop, ctx) if exit_node["policy"] == "barrier_partial_h1" else None
        if runner:
            target, label = Decimal(str(runner[0])).quantize(tick), runner[1]
        else:
            target, label = structural_target(side, entry, stop, ctx, tick)
    except ExnessError as exc:
        text = str(exc)
        return None, "poor_rr" if "poor_rr" in text else "no_target" if "Need" not in text else "h1_not_closed"

    target_r = float(direction * (target - entry) / risk)
    policy = exit_node["policy"]
    management_frame, split = EXIT_TRAITS[policy]

    signal = {
        "strategy": tree_id(tree),
        "status": "confirmed",
        "side": side,
        "stop": float(stop),
        "target": float(target),
        "target_r": target_r,
        "exit_policy": policy,
        "partial_be": policy != "fixed_structural",
        "split": split,
        "management_frame": management_frame,
        "target_context": ctx,
        "candle_time": candle.time,
        "available_at": candle.time + 900,
        "generated": True,
        "shadow_only": True,
        "htf_ast_v2": True,
        "htf_ast_v3": True,
        "filters": filters,
        "htf_structure": {
            "atr": setup["atr"],
            "level": setup["level"],
            "invalidation": setup["invalidation"],
            "audit": {key: value for key, value in setup.items()
                      if key not in ("atr", "level", "invalidation", "setup_time", "confirmed_h1")},
            "target_reason": label,
            "setup_time": setup["setup_time"],
            "confirmed_h1": setup["confirmed_h1"],
            "confirmed_h4": max(row.time for row in higher if row.time + 14400 <= candle.time + 900),
        },
        "reason": f"HTF AST v3 {describe(tree)} (SL: H1, TP: {label})",
    }

    # Verify executable price and spread
    from .forex_buffer_study import htf_exit_target
    slip = Decimal(str(slippage_bps)) / 10000
    if not slip.is_finite() or not 0 <= slip < 1:
        raise ExnessError("invalid_slippage")
    if ask is not None and (ask.time != candle.time or not math.isfinite(ask.close) or ask.close < candle.close):
        return None, "invalid_spread"
    executable = (ask.close if ask is not None and side == "long" else candle.close)
    executable = Decimal(str(executable)) * (1 + direction * slip)
    try:
        htf_exit_target(signal, executable, stop, tick, slip, commission)
    except ExnessError as exc:
        return None, str(exc)

    if policy in EXIT_DEFAULTS:
        signal["exit_params"] = parameters(policy, exit_node.get("params"))
    return signal, None


def evaluate(tree, candle, hours, minutes, higher=()):
    return diagnose(tree, candle, hours, minutes, higher)[0]


# ---------------------------------------------------------------------------
# Genetic Evolution Engine (Crossover & Mutation)
# ---------------------------------------------------------------------------

def crossover(parent_a, parent_b, rng=None):
    rng = random if rng is None else rng
    validate_htf2_tree(parent_a)
    validate_htf2_tree(parent_b)
    if "forex_htf_ast_v4" in (parent_a["schema"], parent_b["schema"]):
        from .forex_entry_policy import control_tree, entry_node, with_entry
        first = control_tree(parent_a) if parent_a["schema"] == "forex_htf_ast_v4" else parent_a
        second = control_tree(parent_b) if parent_b["schema"] == "forex_htf_ast_v4" else parent_b
        child = crossover(first, second, rng)
        source = rng.choice((parent_a, parent_b))
        node = source["root"]["children"][3] if source["schema"] == "forex_htf_ast_v4" else entry_node("market")
        return with_entry(child, node)
    child = copy.deepcopy(parent_a)
    nodes = child["root"]["children"]
    index = rng.randrange(1, 5)
    if index == 4:  # filters travel together; same role only
        nodes[4:] = copy.deepcopy(parent_b["root"]["children"][4:])
    else:
        nodes[index] = copy.deepcopy(parent_b["root"]["children"][index])
    try:
        validate_htf2_tree(child)
    except ValueError:
        return copy.deepcopy(parent_a)
    return child

def _random_filter(rng, taken):
    names = [name for name in (*FILTER_DEFAULTS, "session") if name not in taken]
    name = rng.choice(names)
    return {"frame": "filter", "name": name, "buckets": [rng.randrange(4)]} if name == "session" else _filter(name)

def mutate(tree, rng=None, symbol="XAUUSD"):
    rng = random if rng is None else rng
    from .forex_entry_intents import pilot_enabled
    if tree["schema"] == SCHEMA and pilot_enabled() and rng.randrange(5) == 0:
        from .forex_entry_policy import POLICIES, entry_node, with_entry
        return with_entry(tree, entry_node(rng.choice(POLICIES)))
    if tree["schema"] == "forex_htf_ast_v4":
        from .forex_entry_policy import POLICIES, control_tree, entry_node, with_entry
        if rng.randrange(5) == 0:
            node = copy.deepcopy(tree["root"]["children"][3])
            if node["params"] and rng.random() < 0.5:
                key = rng.choice(list(node["params"]))
                values = {"wait_bars": (1, 2, 3, 4), "limit_ratio": (0.25, 0.5, 0.75, 1),
                          "stop_buffer_atr": (0, 0.025, 0.05, 0.1, 0.25)}[key]
                node["params"][key] = rng.choice([value for value in values if value != node["params"][key]])
            else:
                node = entry_node(rng.choice([policy for policy in POLICIES if policy != node["policy"]]))
            return with_entry(tree, node)
        return with_entry(mutate(control_tree(tree), rng, symbol), tree["root"]["children"][3])
    child = copy.deepcopy(tree)
    nodes = child["root"]["children"]
    index = rng.randrange(1, 5)  # H4 node has nothing to mutate; direction is never stored
    if index == 3:
        node = nodes[3]
        if node["policy"] in EXIT_DEFAULTS and rng.random() < 0.5:
            params = parameters(node["policy"], node.get("params"))
            name = rng.choice(list(params))
            choices = [value for value in EXIT_BOUNDS[name] if value != params[name]
                       and not (node["policy"] == "ladder_h1" and name == "fraction" and value > 0.4)]
            params[name] = rng.choice(choices)
            node["params"] = params
        else:
            node["policy"] = rng.choice([policy for policy in EXIT_POLICIES if policy != node["policy"]])
            node.pop("params", None)
            if node["policy"] in EXIT_DEFAULTS:
                node["params"] = parameters(node["policy"])
    elif index == 4:
        filters = nodes[4:]
        action = rng.choice(("add", "remove", "tune"))
        tunable = [node for node in filters if node["name"] in FILTER_DEFAULTS and FILTER_BOUNDS[node["name"]]]
        if action == "tune" and tunable:
            node = rng.choice(tunable)
            key = rng.choice(list(node["params"]))
            node["params"][key] = rng.choice([v for v in FILTER_BOUNDS[node["name"]][key] if v != node["params"][key]])
        elif action == "remove" and filters:
            nodes.remove(rng.choice(filters))
        elif len(filters) < 3:
            nodes.append(_random_filter(rng, {node["name"] for node in filters}))
    else:
        node = nodes[index]
        registry = H1_BOUNDS if index == 1 else M15_BOUNDS
        bounds = registry[node["family"]]
        if rng.random() < 0.5 or not bounds:
            node["family"] = rng.choice([family for family in registry if family != node["family"]])
            node["params"] = (default_h1_params if index == 1 else default_m15_params)(node["family"])
            if node["family"] == "round_level":
                node["params"] = {"step": round_step(symbol)}
        else:
            key, step, low, high = rng.choice(bounds)
            candidates = [round(node["params"][key] + delta, 6) for delta in (-step, step)]
            candidates = [value for value in candidates if low <= value <= high]
            if candidates:
                node["params"][key] = int(rng.choice(candidates)) if isinstance(step, int) else rng.choice(candidates)
    try:
        validate_htf2_tree(child)
    except ValueError:
        return copy.deepcopy(tree)
    return child

def logic_version():
    names = ("forex_htf_v2.py", "forex_detectors.py", "forex_buffer_study.py",
             "forex_features.py", "forex_samples.py", "forex_indicators.py", "exness_targets.py", "exness_management.py", "exness_sessions.py", "forex_exits.py",
             "forex_entry_policy.py", "forex_entry_replay.py")
    return hashlib.sha256(b"".join(Path(__file__).with_name(name).read_bytes() for name in names)).hexdigest()[:16]


def parent_stats(samples, now, version, born_at=0):
    resolved = sorted((sample for sample in samples
                       if sample["status"] == "resolved" and sample.get("provenance") == PROVENANCE
                       and sample.get("costs", {}).get("verified") is True
                       and sample.get("strategy_version") == version
                       and sample["confirm_time"] + 900 >= born_at
                       and sample["confirm_time"] + 900 <= sample.get("label_available_at", 0)
                       and sample.get("label_available_at", now) < now),
                      key=lambda sample: (sample["confirm_time"], sample.get("recorded_at", 0)))
    days = len({sample["confirm_time"] // 86400 for sample in resolved})
    result = {"count": len(resolved), "days": days, "ready": len(resolved) >= 10,
              "eligible": False, "train_lower_r": None, "val_mean_r": None}
    if not result["ready"]:
        return result
    values = [sample["net_r_ex_swap"] for sample in resolved]
    if not all(type(value) in (int, float) and math.isfinite(value) for value in values):
        raise ValueError("Invalid fitness return")
    average = mean(values)
    result.update(mean_r=average, eligible=average > 0)
    return result


def evaluate_parent_fitness(samples, now=None, version=None):
    now = int(time.time()) if now is None else now
    stats = parent_stats(samples, now, logic_version() if version is None else version)
    return stats if stats["eligible"] else None


def evolve_population(ledger, now, random_seed=None, version=None):
    version = logic_version() if version is None else version
    last = ledger.get("last_evolution") or {}
    earliest = last.get("at", ledger.get("since", now)) + 3600
    if now < earliest:
        return False
    groups = defaultdict(list)
    for sample in ledger["samples"]:
        groups[sample["strategy"]].append(sample)
    members = ledger.setdefault("members", {})
    population = ledger["population"]
    scores = {tree_id(tree): parent_stats(groups[tree_id(tree)], now, version,
                                         max(ledger.get("since", 0), members.get(tree_id(tree), {}).get("born_at", 0))) for tree in population}
    parents = sorted((tree for tree in population if scores[tree_id(tree)]["eligible"]),
                     key=lambda tree: (-scores[tree_id(tree)]["mean_r"], tree_id(tree)))[:4]
    if not parents:
        return False
    replacements = sorted((tree for tree in population if scores[tree_id(tree)]["ready"]
                            and tree not in parents
                            and scores[tree_id(tree)]["mean_r"] < scores[tree_id(parents[0])]["mean_r"]
                            and not any(sample["strategy"] == tree_id(tree) and sample["status"] in ("waiting", "pending", "open")
                                        for sample in ledger["samples"])),
                           key=lambda tree: (scores[tree_id(tree)]["mean_r"], tree_id(tree)))[:1]
    if not replacements:
        return False
    seed = random_seed if random_seed is not None else int(hashlib.sha256(
        f"{ledger.get('account')}:{ledger.get('generation', 0)}:{version}:{now // 900}".encode()).hexdigest()[:16], 16)
    rng = random.Random(seed)
    known = set(members) | {tree_id(tree) for tree in population} | set(groups)
    children, lineage = [], {}
    for _ in range(1000):
        if len(children) == len(replacements):
            break
        selected = rng.sample(parents, 2) if len(parents) > 1 else [parents[0]]
        base = crossover(*selected, rng) if len(selected) == 2 else selected[0]
        child = mutate(base, rng, ledger.get("symbol", "XAUUSD"))
        identifier = tree_id(child)
        if identifier in known:
            continue
        known.add(identifier)
        children.append(child)
        lineage[identifier] = [tree_id(parent) for parent in selected]
    if not children:
        return False
    retired = [tree_id(tree) for tree in replacements[:len(children)]]
    generation = ledger.get("generation", 0) + 1
    for identifier in retired:
        members.setdefault(identifier, {})["retired_at"] = now
    for tree in children:
        identifier = tree_id(tree)
        members[identifier] = {"tree": tree, "generation": generation, "parents": lineage[identifier],
                               "born_at": now, "version": version}
    ledger.update(population=[tree for tree in population if tree_id(tree) not in retired] + children,
                  generation=generation, last_evolution={"at": now, "generation": generation})
    ledger.setdefault("evolution_history", []).append({
        "at": now, "generation": generation, "seed": seed, "version": version,
        "parents": [tree_id(tree) for tree in parents[:5]], "fitness": scores,
        "retired": retired, "added": [tree_id(tree) for tree in children], "lineage": lineage})
    if len(ledger["population"]) != 56:
        raise ValueError("Evolution changed population size")
    return True


# ---------------------------------------------------------------------------
# Ledger & Tick
# ---------------------------------------------------------------------------

def legacy_ledger_path(account):
    return LEDGER.with_name(LEDGER.stem + "-" + hashlib.sha256(account.encode()).hexdigest()[:16] + ".json")

def ledger_path(account, symbol="XAUUSD"):
    from .forex_symbols import checked_symbol
    checked_symbol(symbol)
    legacy = legacy_ledger_path(account)
    return legacy.with_name(legacy.stem + "-" + symbol + ".json")


def reference_market(session, now, symbol):
    """SMT reference H1 loaded once per tick; never one broker call per tree."""
    from .exness_observation import TICK_WINDOW, load_frames
    from .forex_features import SMT_REFERENCES
    from .forex_symbols import configured_symbols
    reference = SMT_REFERENCES.get(symbol)
    if reference is None or reference not in configured_symbols():
        return {}
    rows = load_frames(session, now, reference, window=TICK_WINDOW)["1h"]
    return {"reference_hours": sorted((row for row in rows if row.price_type == "bid"), key=lambda row: row.time)}

def htf_ast3_tick(now=None, symbol="XAUUSD"):
    from .db import ForexInstrumentState, Session, WorkspaceFeedState
    from .exness_feed import FRAMES, WORKSPACE
    from .exness_observation import TICK_WINDOW, audit_frames, load_frames
    from .forex_migration import migrate_symbol_ledger
    from .forex_samples import advance_learning_sample, attach_execution, context, slippage_estimate

    now = int(time.time()) if now is None else now
    account = os.getenv("EXNESS_ACCOUNT_ID", "").strip()
    if not account:
        return "unconfigured"
    path = ledger_path(account, symbol)
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.with_suffix(".lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return "busy"

        stamp = now // 900 * 900 - 900
        ledger = migrate_symbol_ledger(path, legacy_ledger_path(account), account, symbol, now)
        if ledger.get("schema") != SCHEMA or ledger.get("account") != account:
            raise ValueError("Invalid HTF AST v2 ledger identity")
        population = ledger.get("population")
        if not isinstance(population, list) or len(population) != 56:
            raise ValueError("Invalid HTF AST v2 population")
        for tree in population:
            validate_htf2_tree(tree)
        if len({tree_id(tree) for tree in population}) != 56:
            raise ValueError("Duplicate HTF AST v2 population")

        previous = ledger.get("last_capture") or {}
        if previous.get("time") == stamp and previous.get("status") in ("captured", "missed_window"):
            return "waiting_next_candle"

        with Session() as session:
            frames = load_frames(session, now, symbol, window=TICK_WINDOW)
            market = reference_market(session, now, symbol)
            feeds = [session.get(WorkspaceFeedState, (WORKSPACE, symbol, frame)) for frame in FRAMES]
            feed_ready = all(state and not state.error and state.checked_until is not None
                             and state.checked_until >= now // FRAMES[frame][1] * FRAMES[frame][1]
                             for frame, state in zip(FRAMES, feeds))
            specs = session.get(ForexInstrumentState, (WORKSPACE, symbol))
            condition = dict(specs.snapshot) if specs and specs.snapshot else None

        if condition:
            from .forex_conversion import cost_context
            from .forex_costs import SPEC_TTL, account_tag
            try:
                if (condition.get("account_tag") != account_tag(account)
                        or not 0 <= now - condition["observed_at"] < SPEC_TTL):
                    raise ValueError("Unverified instrument specs")
                condition = cost_context(condition, stamp + 900, account=account, symbol=symbol)
            except ValueError:
                condition = None
        commission = (condition or {}).get("commission_per_unit")
        bids = {frame: [row for row in rows if row.price_type == "bid"] for frame, rows in frames.items()}
        asks = {row.time: row for row in frames["15m"] if row.price_type == "ask"}
        valid = audit_frames(frames, symbol)["status"] != "blocked"

        if valid:
            from .forex_entry_replay import advance_forward
            advance_forward([sample for sample in ledger["samples"]
                             if sample["tree"]["schema"] == "forex_htf_ast_v4"], bids, asks, now)
            for sample in ledger["samples"]:
                if sample["tree"]["schema"] == "forex_htf_ast_v4":
                    continue
                if sample["status"] not in ("pending", "open"):
                    continue
                for index, candle in enumerate(bids["15m"]):
                    if candle.time <= sample["last_candle"]:
                        continue
                    if candle.time not in asks:
                        sample.update(status="excluded", reason="ask_candle_missing")
                        break
                    advance_learning_sample(sample, candle, asks[candle.time], bids["15m"][max(0, index - 59):index + 1],
                                            sample["costs"]["slippage_bps"], sample["costs"]["commission_per_unit"] or 0,
                                            hours=bids["1h"])
                    if sample["status"] not in ("pending", "open"):
                        break

        capture = {"time": stamp, "at": now, "status": "waiting_data"}
        if now - (stamp + 900) > 120:
            capture["status"] = "missed_window"
        elif valid and feed_ready and bids["15m"] and bids["15m"][-1].time == stamp and stamp in asks:
            candle, history = bids["15m"][-1], [row for row in bids["15m"] if row.time < stamp][-96:]
            slippage = slippage_estimate(symbol)
            costs = {"slippage_bps": slippage["bps"], "slippage_source": slippage["source"],
                     "commission_per_unit": commission, "conversion": (condition or {}).get("conversion"),
                     "verified": commission is not None}
            hits, rejected = 0, defaultdict(int)
            rejected_total = ledger.setdefault("rejections", {})

            used = {(x["strategy"], x["side"], x["signal"]["htf_structure"]["setup_time"])
                    for x in ledger["samples"]}
            for tree in ledger["population"]:
                signal, reason = diagnose(tree, candle, bids["1h"], history, higher=bids["4h"],
                                          ask=asks[stamp], slippage_bps=costs["slippage_bps"],
                                          commission=costs["commission_per_unit"] or 0, symbol=symbol,
                                          market={**market, "asks": asks})
                if signal is None:
                    rejected[reason] += 1
                    rejected_total[reason] = rejected_total.get(reason, 0) + 1
                    continue
                key = (signal["strategy"], signal["side"], signal["htf_structure"]["setup_time"])
                if key in used:
                    rejected["setup_reused"] += 1
                    rejected_total["setup_reused"] = rejected_total.get("setup_reused", 0) + 1
                    continue
                used.add(key)
                hits += 1
                signal["symbol"] = symbol
                signal["ref_price"] = asks[stamp].close if signal["side"] == "long" else candle.close
                sample = {
                    "symbol": symbol, "strategy": signal["strategy"], "tree": tree, "signal": signal, "side": signal["side"],
                    "strategy_version": logic_version(),
                    "stop": signal["stop"], "confirm_time": stamp, "recorded_at": now, "last_candle": stamp,
                    "status": "pending", "costs": costs, "provenance": PROVENANCE
                }
                if tree["schema"] == "forex_htf_ast_v4":
                    signal["setup_id"] = setup_identity(signal, tree)
                    sample.update(status="waiting", condition=condition)
                else:
                    attach_execution(sample, bids["15m"][-60:], bids["1h"], bids["4h"],
                                     context(candle, bids["1h"], asks[stamp]), candle, condition)
                ledger["samples"].append(sample)

            capture.update(status="captured", confirmed=hits, rejected=dict(rejected))

            # Attempt generational evolution
            evolve_population(ledger, now)

        ledger.update(last_capture=capture, updated_at=now)
        from .forex_migration import atomic_json
        atomic_json(path, ledger)
        return capture["status"]


def _htf_ast3_summary(account, symbol="XAUUSD"):
    trees = g0_trees(symbol)
    base = {
        "status": "shadow_only", "generation": 0, "tree_count": len(trees), "since": None,
        "last_capture": None, "last_evolution": None, "pending": 0, "resolved": 0, "excluded": 0,
        "mean_r": None,
        "note": "AST HTF v2: H4 xu hướng, H1 setup + SL, M15 trigger; so sánh quản lý H1 vs M15; R sau chi phí, chưa swap"
    }
    path = ledger_path(account, symbol) if account else None
    ledger = json.loads(path.read_text()) if path and path.exists() else {
        "samples": [], "population": trees, "generation": 0
    }
    if ledger.get("runtime") not in (None, RUNTIME):
        raise ValueError("Unsupported V3 runtime")
    members, now = ledger.get("members", {}), int(time.time())
    version = logic_version()
    pop = ledger.get("population", trees)
    groups = defaultdict(list)
    for sample in ledger.get("samples", []):
        groups[sample["strategy"]].append(sample)

    rows = []
    for tree in pop:
        tid = tree_id(tree)
        samples = groups.get(tid, [])
        values = [s["net_r_ex_swap"] for s in samples if s["status"] == "resolved"]
        last = samples[-1] if samples else None
        c = tree["root"]["children"]
        exit_node = c[4 if tree["schema"] == "forex_htf_ast_v4" else 3]
        stats = parent_stats(samples, now, version, max(ledger.get("since", 0), members.get(tid, {}).get("born_at", 0)))
        rows.append({
            "id": tid, "description": describe(tree), "label": tree_label(tree, members.get(tid, {}).get("generation", 0)), "signals": len(samples),
            "generation": members.get(tid, {}).get("generation", 0), "parents": members.get(tid, {}).get("parents", []),
            "fitness": stats, "eligible": stats["eligible"],
            "h1_setup": c[1]["family"], "m15_trigger": c[2]["family"], "exit_policy": c[4 if tree["schema"] == "forex_htf_ast_v4" else 3]["policy"],
            "entry_policy": c[3]["policy"] if tree["schema"] == "forex_htf_ast_v4" else "market",
            "exit_params": parameters(exit_node["policy"], exit_node.get("params")) if exit_node["policy"] in EXIT_DEFAULTS else {},
            "last_levels": {"stop": last["signal"]["stop"], "target": last["signal"]["target"],
                            "reason": last["signal"]["reason"], "time": last["confirm_time"]} if last else None,
            "costs_verified": bool(samples) and all(sample["costs"]["verified"] for sample in samples),
            "pending": sum(s["status"] in ("waiting", "pending", "open") for s in samples),
            "resolved": len(values),
            "excluded": sum(s["status"] in ("excluded", "invalid", "skipped") for s in samples),
            "mean_r": mean(values) if values else None,
            "sides": {side: {"resolved": len(side_values), "mean_r": mean(side_values) if side_values else None}
                      for side in ("long", "short")
                      for side_values in [[s["net_r_ex_swap"] for s in samples
                                           if s["status"] == "resolved" and s["side"] == side]]},
            "lower_r": mean(values) - 1.96 * stdev(values) / len(values) ** 0.5 if len(values) > 1 else None
        })

    # Paired comparison between H1 management and M15 management
    paired_h1, paired_m15 = [], []
    by_time = defaultdict(lambda: {"h1": None, "m15": None})
    for s in ledger.get("samples", []):
        if s["status"] == "resolved":
            pol = s["signal"].get("exit_policy")
            if pol == "partial_be_h1":
                by_time[(s["confirm_time"], s["side"])]["h1"] = s["net_r_ex_swap"]
            elif pol == "partial_be_m15":
                by_time[(s["confirm_time"], s["side"])]["m15"] = s["net_r_ex_swap"]
    for pair in by_time.values():
        if pair["h1"] is not None and pair["m15"] is not None:
            paired_h1.append(pair["h1"])
            paired_m15.append(pair["m15"])

    comparison = {
        "paired_count": len(paired_h1),
        "h1_mean_r": mean(paired_h1) if paired_h1 else None,
        "m15_mean_r": mean(paired_m15) if paired_m15 else None,
    }

    resolved = [s["net_r_ex_swap"] for s in ledger.get("samples", []) if s["status"] == "resolved"]
    reasons = defaultdict(int)
    for s in ledger.get("samples", []):
        if s["status"] in ("excluded", "invalid", "skipped"):
            reasons[str(s.get("reason") or s.get("execution_result", {}).get("reason", "unknown"))] += 1

    err_path = path.with_suffix(".error.json") if path else None
    err = json.loads(err_path.read_text()) if err_path and err_path.exists() else None

    return {
        **base,
        "status": "error" if err else (ledger.get("last_capture") or {}).get("status", "waiting_data"),
        "generation": ledger.get("generation", 0),
        "engine": SCHEMA, "migration": ledger.get("migration"),
        "last_evolution": ledger.get("last_evolution"),
        "next_evolution_at": (ledger.get("last_evolution") or {}).get("at", ledger.get("since", now)) + 3600,
        "error": err,
        "since": ledger.get("since"),
        "last_capture": ledger.get("last_capture"),
        "pending": sum(r["pending"] for r in rows),
        "resolved": len(resolved),
        "excluded": sum(r["excluded"] for r in rows),
        "mean_r": mean(resolved) if resolved else None,
        "rejections": ledger.get("rejections", {}),
        "excluded_reasons": dict(reasons),
        "management_comparison": comparison,
        "trees": rows
    }


def htf_ast3_summary(account, symbol="XAUUSD"):
    try:
        return _htf_ast3_summary(account, symbol)
    except Exception:
        logging.exception("HTF AST v2 shadow summary unavailable")
        return {
            "status": "error", "generation": 0, "tree_count": 0, "since": None, "last_capture": None,
            "pending": 0, "resolved": 0, "excluded": 0, "mean_r": None, "trees": [],
            "note": "HTF AST v2 shadow unavailable"
        }


def safe_htf_ast3_tick(now=None, symbol="XAUUSD"):
    try:
        status = htf_ast3_tick(now, symbol)
        account = os.getenv("EXNESS_ACCOUNT_ID", "").strip()
        if account and status not in ("busy", "waiting_next_candle"):
            ledger_path(account, symbol).with_suffix(".error.json").unlink(missing_ok=True)
        return status
    except Exception:
        logging.exception("HTF AST v2 shadow unavailable")
        try:
            account = os.getenv("EXNESS_ACCOUNT_ID", "").strip()
            if account:
                path = ledger_path(account, symbol).with_suffix(".error.json")
                path.parent.mkdir(parents=True, exist_ok=True)
                temporary = path.with_suffix(".tmp")
                temporary.write_text(json.dumps({"status": "error", "at": int(time.time()) if now is None else now}))
                temporary.replace(path)
        except OSError:
            logging.exception("Unable to record HTF AST v2 shadow error")


def execution_enabled():
    return os.getenv("FOREX_HTF_V2_EXECUTION_ENABLED", "false").lower() == "true"


def read_ledger(account, symbol="XAUUSD"):
    path = ledger_path(account, symbol)
    ledger = json.loads(path.read_text())
    if (ledger.get("runtime") != RUNTIME or ledger.get("schema") != SCHEMA or ledger.get("account") != account
            or ledger.get("symbol") != symbol):
        raise ValueError("V3 migration pending or ledger identity invalid")
    population = ledger.get("population")
    if not isinstance(population, list) or len(population) != 56:
        raise ValueError("Invalid V3 population")
    identifiers = [tree_id(tree) for tree in population]
    if len(set(identifiers)) != 56:
        raise ValueError("Duplicate V3 population")
    for tree in population:
        validate_htf2_tree(tree)
    return ledger


def qualified_trees(ledger, now):
    groups = defaultdict(list)
    for sample in ledger["samples"]:
        groups[sample["strategy"]].append(sample)
    version = logic_version()
    ranked = []
    for tree in ledger["population"]:
        identifier = tree_id(tree)
        born = max(ledger["since"], ledger.get("members", {}).get(identifier, {}).get("born_at", 0))
        stats = parent_stats(groups[identifier], now, version, born)
        # ponytail: entry has no fitness gate; restore stats["eligible"] gating if forward qualification is required.
        ranked.append((identifier, stats))
    def key(item):
        stats = item[1]
        return (not stats["eligible"], -stats.get("mean_r", 0), item[0])
    return sorted(ranked, key=key)


def setup_identity(signal, tree):
    setup = tree["root"]["children"][1]
    raw = {"setup": setup, "side": signal["side"], "time": signal["htf_structure"]["setup_time"],
           "level": signal["htf_structure"]["level"], "invalidation": signal["htf_structure"]["invalidation"]}
    return hashlib.sha256(json.dumps(raw, sort_keys=True).encode()).hexdigest()[:24]


def execution_signal(sample):
    return {**copy.deepcopy(sample["signal"]), "shadow_only": False,
            "execution_source": PROVENANCE, "strategy_version": sample["strategy_version"],
            "tree": copy.deepcopy(sample["tree"]), "setup_id": setup_identity(sample["signal"], sample["tree"])}


def execution_candidates(account, now, symbol="XAUUSD"):
    ledger = read_ledger(account, symbol)
    stamp = now // 900 * 900 - 900
    if not 0 <= now - stamp - 900 <= 120:
        return []
    if (ledger.get("last_capture") or {}).get("status") != "captured" or ledger["last_capture"]["time"] != stamp:
        return []
    ranked = qualified_trees(ledger, now)
    version = logic_version()
    result = []
    for identifier, stats in ranked:
        sample = next((sample for sample in ledger["samples"] if sample["strategy"] == identifier
                       and sample["strategy_version"] == version and sample["confirm_time"] == stamp
                       and sample.get("provenance") == PROVENANCE and sample.get("costs", {}).get("verified") is True
                       and sample["status"] in ("pending", "open")), None)
        if sample and sample["tree"] in ledger["population"] and sample["tree"]["schema"] == SCHEMA:
            result.append((execution_signal(sample), stats))
    from .forex_entry_intents import pilot_enabled
    if pilot_enabled():
        from .forex_entry_pilot import read_ledger as read_pilot
        pilot = read_pilot(account, symbol)
        controls = {member["control_tree_id"] for member in pilot["members"].values()}
        paired = {sample["setup_id"] for sample in pilot["samples"]}
        result = [(signal, stats) for signal, stats in result
                  if signal["strategy"] not in controls and signal["setup_id"] not in paired]
    return result

def virtual_candidates(account, symbol, now):
    ledger = read_ledger(account, symbol)
    version = logic_version()
    return [sample for sample in ledger["samples"]
            if sample["tree"]["schema"] == "forex_htf_ast_v4" and sample["tree"] in ledger["population"]
            and sample["status"] in ("waiting", "pending") and sample.get("strategy_version") == version
            and sample.get("provenance") == PROVENANCE and sample.get("costs", {}).get("verified") is True
            and sample["signal"]["available_at"] <= now < sample["signal"]["entry_policy"]["expires_at"]]


def validate_execution_signal(signal, account, now=None):
    now = time.time() if now is None else now
    if not execution_enabled() or os.getenv("EXNESS_DEMO_EXECUTION_ENABLED", "false").lower() != "true":
        raise ExnessError("v2_execution_disabled")
    if str(signal.get("strategy", "")).startswith("hast4_"):
        from .forex_entry_intents import validate_execution_signal as validate_entry
        return validate_entry(signal, account, now)
    if signal.get("shadow_only") is not False or not str(signal.get("strategy", "")).startswith("hast3_"):
        raise ExnessError("shadow_only_signal")
    try:
        candidates = execution_candidates(account, now, signal.get("symbol", "XAUUSD"))
        for expected, _ in candidates:
            supplied = dict(signal)
            if supplied.get("tp_reason") == expected["htf_structure"]["target_reason"]:
                supplied.pop("tp_reason")
            if supplied == expected:
                return expected
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ExnessError("v2_execution_source_unavailable") from exc
    raise ExnessError("unverified_v2_signal")
