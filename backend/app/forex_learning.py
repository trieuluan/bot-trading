"""Read-only V2 research and chart API; never trains legacy policies."""

import os
import time

from fastapi import APIRouter

from .db import Session
from .exness_observation import load_frames
from .forex_htf_v2 import (
    SCHEMA, describe, diagnose, execution_candidates, execution_enabled, htf_ast2_summary,
    qualified_trees, read_ledger, tree_id,
)
from .forex_indicators import ema
from .forex_samples import context

router = APIRouter(prefix="/api/workspaces/exness_forex/learning")


def execution_status(account, now):
    from .db import ExnessExecutionState
    v2 = execution_enabled()
    exness = os.getenv("EXNESS_DEMO_EXECUTION_ENABLED", "false").lower() == "true"
    with Session() as session:
        state = session.get(ExnessExecutionState, account) if account else None
        control = bool(state and state.enabled)
    reason = "v2_execution_disabled" if not v2 else "exness_execution_disabled" if not exness else "paused" if not control else None
    try:
        ledger = read_ledger(account)
        eligible = qualified_trees(ledger, now)
        candidates = execution_candidates(account, now) if not reason else []
        return {"enabled": v2 and exness and control, "v2_enabled": v2, "exness_enabled": exness,
                "account_enabled": control, "blocked_reason": reason or ("no_eligible_tree" if not eligible else None),
                "eligible_count": len(eligible), "eligible_ids": [identifier for identifier, _ in eligible],
                "selected": candidates[0][0]["strategy"] if candidates else None}
    except (OSError, ValueError, KeyError, TypeError):
        return {"enabled": False, "v2_enabled": v2, "exness_enabled": exness, "account_enabled": control,
                "blocked_reason": reason or "v2_ledger_unavailable", "eligible_count": 0, "eligible_ids": [], "selected": None}


@router.get("")
def summary():
    account = os.getenv("EXNESS_ACCOUNT_ID", "").strip()
    return {"engine": SCHEMA, "mode": "v2_shadow", "execution_v2": execution_status(account, int(time.time())),
            "htf_ast2_shadow": htf_ast2_summary(account)}


def current_analysis(now=None):
    now = int(time.time()) if now is None else now
    account = os.getenv("EXNESS_ACCOUNT_ID", "").strip()
    with Session() as session:
        frames = load_frames(session, now)
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
        ledger = read_ledger(account)
    except (OSError, ValueError, KeyError, TypeError):
        return {"status": "unavailable", "reason": "Chờ worker chuyển sang G0 V2 mới"}
    strategies = {}
    for tree in ledger["population"]:
        signal, reason = diagnose(tree, candle, hours, minutes, higher, ask)
        strategies[tree_id(tree)] = {
            "status": "confirmed" if signal else "rejected", "reason": reason or describe(tree),
            "side": signal["side"] if signal else None, "stop": signal["stop"] if signal else None,
            "level": signal["htf_structure"]["level"] if signal else None,
            "chart": {"levels": [{"label": label, "price": signal[field], "from": candle.time, "to": candle.time + 900}
                                  for label, field in (("SL H1", "stop"), ("TP H1/H4", "target"))] if signal else [],
                      "oscillator": None, "note": describe(tree), "parameters": tree,
                      "as_of": candle.time + 900, "price_type": "bid"},
        }
    closed_hours = [row for row in hours if row.time + 3600 <= candle.time + 900]
    candidates = execution_candidates(account, now) if execution_enabled() else []
    selected = candidates[0][0] if candidates else None
    return {"candle_time": candle.time, "available_at": candle.time + 900,
            "features": context(candle, hours, ask), "status": "ready" if now - candle.time - 900 <= 120 else "stale",
            "source": SCHEMA, "issues": [], "scores": [], "policy_mode": "v2_shadow" if not execution_enabled() else "v2_qualified",
            "policy_updated_at": ledger.get("updated_at"), "selected_signal": selected,
            "strategies": strategies,
            "levels": {"m15_donchian": None, "m15_ema20": ema([row.close for row in minutes[-60:]], 20),
                       "h1_ema20": ema([row.close for row in closed_hours[-60:]], 20) if len(closed_hours) >= 20 else None,
                       "h1_ema50": ema([row.close for row in closed_hours[-60:]], 50) if len(closed_hours) >= 50 else None,
                       "barriers": []}}


@router.get("/analysis")
def analysis_endpoint():
    try:
        result = current_analysis()
    except Exception:
        result = {"status": "error", "reason": "Phân tích V2 chưa sẵn sàng"}
    return {**result, **summary()}
