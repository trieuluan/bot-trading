"""Closed-candle contextual learner; simulated labels are never broker fills."""

import fcntl
import json
import math
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, stdev

from fastapi import APIRouter

from .db import ForexInstrumentState, Session
from .exness_feed import WORKSPACE
from .exness_observation import audit_frames, load_frames, observe_forex
from .forex_strategies import RULES, ema, strategy_snapshot

router = APIRouter(prefix="/api/workspaces/exness_forex/learning")
REPORT = Path(__file__).resolve().parents[2] / "research-output/exness-learning.json"
VERSION = "contextual_r_v1"

def context(candle, hours, ask):
    closed = [row for row in hours if row.time + 3600 <= candle.time + 900][-60:]
    closes = [row.close for row in closed]
    trend = "unknown"
    if len(closes) >= 50:
        trend = "up" if ema(closes, 20) > ema(closes, 50) else "down"
    spread = max(0, ask.close - candle.close)
    return {"trend": trend, "utc_session": datetime.fromtimestamp(candle.time + 900, timezone.utc).hour // 6,
            "spread": spread, "body": abs(candle.close - candle.open),
            "range": candle.high - candle.low}

def bucket(signal, features):
    return f"{signal['strategy']}:{signal['side']}:{features['trend']}:{features['utc_session']}"

def fit(samples, cutoff):
    groups = defaultdict(list)
    for sample in samples:
        if sample["status"] == "resolved" and sample["label_available_at"] < cutoff:
            groups[sample["bucket"]].append(sample["net_r_ex_swap"])
    return {key: {"count": len(values), "mean_r": mean(values),
                  "lower_r": mean(values) - 1.96 * stdev(values) / math.sqrt(len(values))
                  if len(values) > 1 else -1}
            for key, values in groups.items()}

def choose(candidates, features, model):
    scored = []
    for signal in candidates:
        stats = model.get(bucket(signal, features), {"count": 0, "mean_r": 0, "lower_r": -1})
        scored.append({"strategy": signal["strategy"], "side": signal["side"], **stats})
    eligible = [row for row in scored if row.get("count", 0) >= 30 and row.get("lower_r", -1) > 0]
    best = max(eligible, key=lambda row: row["lower_r"], default=None)
    signal = next((row for row in candidates if best and row["strategy"] == best["strategy"]), None)
    return signal, scored

def train(frames, now, commission_per_unit=None, existing=None):
    from scripts.exness_mtf_replay import advance

    frames = {frame: [row for row in rows if row.time + {"15m": 900, "1h": 3600, "4h": 14400}[frame] <= now]
              for frame, rows in frames.items()}
    if audit_frames(frames)["status"] == "blocked":
        raise ValueError("Invalid bid/ask history; learning paused")
    bids = {frame: sorted((row for row in rows if row.price_type == "bid"), key=lambda row: row.time)
            for frame, rows in frames.items()}
    asks = {row.time: row for row in frames["15m"] if row.price_type == "ask"}
    minutes = bids["15m"]
    if len(minutes) < 300:
        raise ValueError("Need at least 300 closed M15 candles")
    cutoff = existing["train_before"] if existing else minutes[int(len(minutes) * 0.7)].time
    samples = existing["samples"] if existing else []
    active = [row for row in samples if row["status"] in ("pending", "open")]
    decisions = existing["decisions"] if existing else []
    previous = existing["breakout_state"] if existing else None
    model = existing["model"] if existing else None
    for index, candle in enumerate(minutes):
        if existing and candle.time <= existing["latest"]["time"]:
            continue
        for sample in active:
            advance(sample, candle, asks[candle.time], slippage_bps=2,
                    commission_per_unit=commission_per_unit or 0)
        active = [sample for sample in active if sample["status"] in ("open", "pending")]
        breakout = observe_forex(candle, bids["1h"], bids["4h"], previous)
        previous = breakout
        history = minutes[max(0, index - 60):index]
        candidates = [strategy_snapshot(name, candle, bids["1h"], history) for name in RULES]
        candidates.append({**breakout, "strategy": "breakout_retest"})
        baseline = strategy_snapshot("regime_router", candle, bids["1h"], history,
                                     higher=bids["4h"], breakout=breakout)
        candidates = [row for row in candidates if row["status"] == "confirmed"]
        features = context(candle, bids["1h"], asks[candle.time])
        if model is None and candle.time >= cutoff:
            model = fit(samples, cutoff)
        selected, scored = choose(candidates, features, model or {})
        indices = {}
        if baseline["status"] == "confirmed":
            candidates.append(baseline)
        for signal in candidates:
            name = signal["strategy"]
            indices[name] = len(samples)
            sample = {"strategy": name, "status": "pending", "side": signal["side"],
                      "confirm_time": candle.time, "last_candle": candle.time, "stop": signal["stop"],
                      "features": features, "bucket": bucket(signal, features),
                      "provenance": "forward_simulation" if existing else "historical_simulation", "signal": signal}
            samples.append(sample)
            active.append(sample)
        if candle.time >= cutoff:
            decisions.append({"time": candle.time, "selected": indices.get(selected["strategy"]) if selected else None,
                              "baseline": indices.get("regime_router"), "scores": scored})
    paired, selected_returns, baseline_returns = [], [], []
    for decision in decisions:
        learner = samples[decision["selected"]] if decision["selected"] is not None else None
        baseline = samples[decision["baseline"]] if decision["baseline"] is not None else None
        if not learner and not baseline:
            continue
        if any(row and row["status"] != "resolved" for row in (learner, baseline)):
            continue
        learner_r = learner["net_r_ex_swap"] if learner else 0
        baseline_r = baseline["net_r_ex_swap"] if baseline else 0
        paired.append(learner_r - baseline_r)
        if learner:
            selected_returns.append(learner_r)
        if baseline:
            baseline_returns.append(baseline_r)
    edge = mean(paired) - 1.96 * stdev(paired) / math.sqrt(len(paired)) if len(paired) > 1 else -1
    # ponytail: overlapping replay labels rank hypotheses only; broker validation needed for promotion.
    validation = {"selected_closed": len(selected_returns), "baseline_closed": len(baseline_returns),
                  "selected_mean_r": mean(selected_returns) if selected_returns else None,
                  "baseline_mean_r": mean(baseline_returns) if baseline_returns else None,
                  "paired_lower_r": edge, "eligible": len(selected_returns) >= 50 and len(baseline_returns) >= 30 and edge > 0}
    latest = minutes[-1]
    latest_candidates = [row["signal"] for row in samples if row["confirm_time"] == latest.time and row["strategy"] != "regime_router"]
    selected, scored = choose(latest_candidates, context(latest, bids["1h"], asks[latest.time]), model or {})
    return {"version": VERSION, "updated_at": now, "fitted_at": existing["fitted_at"] if existing else now,
            "processed_bars": len(minutes), "train_before": cutoff, "breakout_state": previous,
            "mode": "shadow", "execution_enabled": False, "validation": validation,
            "reason": "Đang học và chọn tín hiệu mô phỏng; cần kiểm chứng forward trước khi thay router đặt lệnh",
            "assumptions": {"slippage_bps_each_side": 2, "commission_per_unit": commission_per_unit,
                            "commission_known": commission_per_unit is not None, "swap_included": False,
                            "overlapping_samples": True, "entry": "next M15 executable open", "stop_first": True},
            "model": model or {}, "strategy_summary": {
                name: {"signals": sum(row["strategy"] == name for row in samples),
                       "closed": sum(row["strategy"] == name and row["status"] == "resolved" for row in samples),
                       "mean_r": mean([row["net_r_ex_swap"] for row in samples if row["strategy"] == name and row["status"] == "resolved"])
                       if any(row["strategy"] == name and row["status"] == "resolved" for row in samples) else None,
                       "excluded": sum(row["strategy"] == name and row["status"] == "excluded" for row in samples)}
                for name in ("breakout_retest", *RULES, "regime_router")},
            "latest": {"time": latest.time, "selected": selected["strategy"] if selected else None, "scores": scored},
            "samples": samples, "decisions": decisions}

@router.get("")
def summary():
    if not REPORT.exists():
        return {"mode": "warming_up", "reason": "Waiting for first learning pass"}
    report = json.loads(REPORT.read_text())
    return {key: value for key, value in report.items() if key not in ("samples", "model", "decisions", "breakout_state")}

def learning_tick(now=None, force=False):
    now = int(time.time()) if now is None else now
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    with REPORT.with_suffix(".lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return "busy"
        existing = json.loads(REPORT.read_text()) if REPORT.exists() else None
        if existing and existing.get("version") != VERSION:
            existing = None
        if not force and existing and now // 900 * 900 <= existing["latest"]["time"] + 900:
            return "waiting_next_candle"
        with Session() as session:
            frames = load_frames(session, now)
            specs = session.get(ForexInstrumentState, WORKSPACE)
            commission = (specs.snapshot or {}).get("commission_per_unit") if specs else None
        if force or (existing and (now - existing["fitted_at"] >= 21600 or existing["assumptions"]["commission_per_unit"] != commission)):
            existing = None
        report = train(frames, now, commission, existing)
        temporary = REPORT.with_suffix(".tmp")
        temporary.write_text(json.dumps(report, ensure_ascii=False, allow_nan=False))
        temporary.replace(REPORT)
        return "trained"
