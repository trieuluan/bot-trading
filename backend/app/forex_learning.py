"""Read-only V2 research and chart API; never trains legacy policies."""

import os
import time
from decimal import Decimal

from fastapi import APIRouter

from .db import Session
from .exness import ExnessError
from .exness_observation import load_frames
from .exness_targets import target_context
from .forex_htf_v2 import (
    MAX_RISK_ATR,
    MIN_RISK_ATR,
    PROVENANCE,
    SCHEMA,
    _closed_rows,
    _h4_context,
    describe,
    detect_setup,
    diagnose,
    execution_candidates,
    execution_enabled,
    htf_ast3_summary,
    qualified_trees,
    read_ledger,
    reference_market,
    tree_id,
    tree_label,
)
from .forex_indicators import ema
from .forex_overlays import chart_overlays, tree_overlays
from .forex_samples import context
from .forex_symbols import configured_symbols, request_symbol

router = APIRouter(prefix="/api/workspaces/exness_forex/learning")

def h1_setup_zone(tree, candle, hours, higher, symbol="XAUUSD"):
    """Read-only setup preview using the evaluator's closed-bar guards and detectors."""
    children = tree["root"]["children"]
    side = _h4_context(candle, higher, symbol)
    if side is None:
        return None
    closed = _closed_rows(candle, hours, 3600, 50, symbol)
    setup = detect_setup(children[1], closed, side, symbol) if closed else None
    if not setup or candle.time - setup["setup_time"] > 14400:
        return None
    direction = Decimal(1) if side == "long" else Decimal(-1)
    atr = Decimal(str(setup["atr"]))
    stop = (Decimal(str(setup["invalidation"])) - direction * Decimal("0.25") * atr).quantize(Decimal("0.001"))
    risk = direction * (Decimal(str(candle.close)) - stop)
    if not MIN_RISK_ATR * atr <= risk <= MAX_RISK_ATR * atr or risk <= 0:
        return None
    return {"side": side, "family": children[1]["family"], "level": setup["level"],
            "invalidation": setup["invalidation"], "stop": float(stop), "setup_time": setup["setup_time"],
            "from": setup["confirmed_h1"] + 3600, "to": setup["setup_time"] + 14400 + 900,
            "source": "preview"}

def signal_chart_zone(tree, signal, source, as_of):
    structure = signal["htf_structure"]
    if signal["available_at"] > as_of:
        return None
    return {"side": signal["side"], "family": tree["root"]["children"][1]["family"],
            "level": structure["level"], "invalidation": structure["invalidation"],
            "stop": signal["stop"], "target": signal["target"], "target_reason": structure["target_reason"],
            "entry": signal.get("ref_price"), "setup_time": structure["setup_time"],
            "from": structure["confirmed_h1"] + 3600, "to": structure["setup_time"] + 14400 + 900,
            "trigger_at": signal["available_at"], "source": source}


def execution_status(account, now, symbol="XAUUSD"):
    from .db import ExnessExecutionState
    v2 = execution_enabled()
    exness = os.getenv("EXNESS_DEMO_EXECUTION_ENABLED", "false").lower() == "true"
    with Session() as session:
        state = session.get(ExnessExecutionState, account) if account else None
        control = bool(state and state.enabled)
    reason = "v2_execution_disabled" if not v2 else "exness_execution_disabled" if not exness else "paused" if not control else None
    try:
        ledger = read_ledger(account, symbol)
        # Vào lệnh không có cổng fitness (ungated): mọi cây trong quần thể đều được phép vào lệnh.
        # Đủ chuẩn tiến hoá (làm bố mẹ) vẫn cần >=10 mẫu forward và mean R > 0.
        allowed = qualified_trees(ledger, now)
        candidates = execution_candidates(account, now, symbol) if not reason else []
        capture = ledger.get("last_capture") or {}
        stamp = now // 900 * 900 - 900
        count = capture.get("confirmed", 0)
        fresh = (capture.get("status") == "captured" and capture.get("time") == stamp
                 and 0 <= now - stamp - 900 <= 120 and isinstance(count, int) and count > 0)
        return {"enabled": v2 and exness and control, "v2_enabled": v2, "exness_enabled": exness,
                "account_enabled": control, "blocked_reason": reason or ("no_eligible_tree" if not allowed else None),
                "entry_allowed_count": len(allowed), "entry_allowed_ids": [identifier for identifier, _ in allowed],
                "evolution_ready_count": sum(1 for _, stats in allowed if stats["eligible"]),
                "selected": candidates[0][0]["strategy"] if candidates else None,
                "signal_count": count if fresh else 0,
                "signal_available_at": stamp + 900 if fresh else None,
                "signal_expires_at": stamp + 1020 if fresh else None}
    except (OSError, ValueError, KeyError, TypeError):
        return {"enabled": False, "v2_enabled": v2, "exness_enabled": exness, "account_enabled": control,
                "blocked_reason": reason or "v2_ledger_unavailable", "entry_allowed_count": 0, "entry_allowed_ids": [],
                "evolution_ready_count": 0, "selected": None,
                "signal_count": 0, "signal_available_at": None, "signal_expires_at": None}


@router.get("/symbols")
def symbols_endpoint():
    return {"symbols": list(configured_symbols())}

@router.get("")
def summary(symbol: str = "XAUUSD"):
    request_symbol(symbol)
    account = os.getenv("EXNESS_ACCOUNT_ID", "").strip()
    return {"symbol": symbol, "engine": SCHEMA, "mode": "v3_shadow", "execution_v2": execution_status(account, int(time.time()), symbol),
            "htf_ast3_shadow": htf_ast3_summary(account, symbol)}


def current_analysis(now=None, symbol="XAUUSD"):
    now = int(time.time()) if now is None else now
    account = os.getenv("EXNESS_ACCOUNT_ID", "").strip()
    with Session() as session:
        frames = load_frames(session, now, symbol)
        market = reference_market(session, now, symbol)
    durations = {"15m": 900, "1h": 3600, "4h": 14400}
    frames = {frame: [row for row in rows if row.time + durations[frame] <= now] for frame, rows in frames.items()}
    bids = {frame: sorted((row for row in rows if row.price_type == "bid"), key=lambda row: row.time)
            for frame, rows in frames.items()}
    minutes, hours, higher = bids["15m"], bids["1h"], bids["4h"]
    if not minutes:
        return {"status": "unavailable", "reason": "Chưa có nến M15 đã đóng"}
    candle = minutes[-1]
    ask = next((row for row in frames["15m"] if row.price_type == "ask" and row.time == candle.time), None)
    if not ask:
        return {"status": "unavailable", "reason": "Thiếu nến ask"}
    try:
        ledger = read_ledger(account, symbol)
    except (OSError, ValueError, KeyError, TypeError):
        return {"status": "unavailable", "reason": "Chờ worker chuyển sang G0 V3 mới"}
    as_of = candle.time + 900
    captured = {}
    for sample in ledger.get("samples", []):
        if (sample.get("provenance") == PROVENANCE and sample.get("recorded_at", now + 1) <= now
                and sample["confirm_time"] + 900 <= as_of
                and sample["confirm_time"] > captured.get(sample["strategy"], {}).get("confirm_time", -1)):
            captured[sample["strategy"]] = sample
    shared_overlays = _safe_overlays(candle, minutes, hours, higher, symbol) or {}
    overlay_cache = {}
    strategies = {}
    for tree in ledger["population"]:
        signal, reason = diagnose(tree, candle, hours, minutes, higher, ask, symbol=symbol,
                                  market={**market, "asks": {row.time: row for row in frames["15m"] if row.price_type == "ask"}})
        try:
            zone = h1_setup_zone(tree, candle, hours, higher, symbol)
        except (KeyError, TypeError, ValueError):
            zone = None
        sample = captured.get(tree_id(tree))
        saved_zone = signal_chart_zone(tree, sample["signal"], "forward", as_of) if sample else None
        if saved_zone:
            saved_zone["sample_status"] = sample["status"]
        if signal:
            zone = signal_chart_zone(tree, signal, "analysis", as_of)
        if zone and saved_zone and zone["setup_time"] == saved_zone["setup_time"]:
            zone = None
        try:
            owned_overlays = tree_overlays(tree, candle, minutes, hours, higher, symbol, shared_overlays, overlay_cache)
        except (KeyError, TypeError, ValueError, ZeroDivisionError):
            owned_overlays = None
        strategies[tree_id(tree)] = {
            "zone": zone,
            "captured_zone": saved_zone,
            "description": describe(tree),
            "generation": ledger.get("members", {}).get(tree_id(tree), {}).get("generation", 0),
            "label": tree_label(tree, ledger.get("members", {}).get(tree_id(tree), {}).get("generation", 0)),
            "status": "confirmed" if signal else "rejected", "reason": reason or describe(tree),
            "side": signal["side"] if signal else None, "stop": signal["stop"] if signal else None,
            "level": signal["htf_structure"]["level"] if signal else None,
            "chart": {"levels": [{"label": label, "price": signal[field], "from": candle.time, "to": candle.time + 900}
                                  for label, field in (("SL H1", "stop"), ("TP H1/H4", "target"))] if signal else [],
                      "oscillator": None, "note": describe(tree), "parameters": tree,
                      "as_of": candle.time + 900, "price_type": "bid",
                      "overlays": owned_overlays},
        }
    closed_hours = [row for row in hours if row.time + 3600 <= candle.time + 900]
    closed_higher = _closed_rows(candle, higher, 14400, 50, symbol)
    try:
        barriers = target_context(candle, hours, higher)["barriers"] if closed_higher and _closed_rows(candle, hours, 3600, 50, symbol) else []
    except ExnessError:
        barriers = []
    candidates = execution_candidates(account, now, symbol) if execution_enabled() else []
    selected = candidates[0][0] if candidates else None
    return {"candle_time": candle.time, "available_at": candle.time + 900,
            "features": context(candle, hours, ask), "status": "ready" if now - candle.time - 900 <= 120 else "stale",
            "source": SCHEMA, "issues": [], "scores": [], "policy_mode": "v2_shadow" if not execution_enabled() else "v2_qualified",
            "policy_updated_at": ledger.get("updated_at"), "selected_signal": selected,
            "strategies": strategies,
            "levels": {"m15_donchian": None, "m15_ema20": ema([row.close for row in minutes[-60:]], 20),
                       "h1_ema20": ema([row.close for row in closed_hours[-60:]], 20) if len(closed_hours) >= 20 else None,
                       "h1_ema50": ema([row.close for row in closed_hours[-60:]], 50) if len(closed_hours) >= 50 else None,
                       "h4_ema50": ema([row.close for row in closed_higher[-60:]], 50) if closed_higher else None,
                       "barriers": barriers,
                       "overlays": shared_overlays or None}}


def _safe_overlays(candle, minutes, hours, higher, symbol):
    try:
        return chart_overlays(candle, minutes, hours, higher, symbol)
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        return None

@router.get("/analysis")
def analysis_endpoint(symbol: str = "XAUUSD"):
    request_symbol(symbol)
    try:
        result = current_analysis(symbol=symbol)
    except Exception:
        result = {"status": "error", "reason": "Phân tích V2 chưa sẵn sàng"}
    return {**result, **summary(symbol)}
