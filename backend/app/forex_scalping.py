"""M1 historical replay and prospective shadow; never sends broker orders."""

import fcntl
import hashlib
import json
import math
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, stdev

from sqlalchemy import select

from .db import ForexInstrumentState, Session, WorkspaceCandle, WorkspaceFeedState
from .exness_feed import WORKSPACE
from .forex_strategies import ema

NAME = "scalp_m1_breakout"
STEP = 60
TARGET_R = 1.5
HORIZON = 30
SPREAD_MULT = 4
VARIANTS = (
    {"id": NAME, "label": "Gốc · D20 / TP 1.5R", "lookback": 20, "target_r": 1.5, "hours": None},
    {"id": "scalp_m1_tp1", "label": "TP ngắn · D20 / TP 1R", "lookback": 20, "target_r": 1.0, "hours": None},
    {"id": "scalp_m1_tp2", "label": "TP dài · D20 / TP 2R", "lookback": 20, "target_r": 2.0, "hours": None},
    {"id": "scalp_m1_d30", "label": "Breakout chậm · D30 / TP 1.5R", "lookback": 30, "target_r": 1.5, "hours": None},
    {"id": "scalp_m1_utc_7_21", "label": "Khung 07–21 UTC · D20 / TP 1.5R", "lookback": 20, "target_r": 1.5, "hours": (7, 21)},
)

def scalp_max_positions():
    try:
        return min(20, max(1, int(os.getenv("EXNESS_SCALP_MAX_POSITIONS", "1"))))
    except ValueError:
        return 1

VERSION = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()[:16]

def scalp_signal(candle, history, ask, variant=None):
    """M1 close breaks the 20-bar range in EMA20/50 direction; stop = last 5 bars; cost-aware."""
    variant = variant or VARIANTS[0]
    hours = variant["hours"]
    if hours and not hours[0] <= datetime.fromtimestamp(candle.time + STEP, timezone.utc).hour < hours[1]:
        return None
    history = [row for row in history if row.time < candle.time][-60:]
    if len(history) < 50 or ask is None or ask.time != candle.time:
        return None
    if any(after.time - before.time != STEP for before, after in zip(history, history[1:] + [candle])):
        return None
    closes = [row.close for row in history]
    fast, slow = ema(closes, 20), ema(closes, 50)
    recent = history[-variant["lookback"]:]
    spread = max(0.0, ask.close - candle.close)
    near = history[-4:] + [candle]
    if candle.close > max(row.high for row in recent) and fast > slow and candle.close > candle.open:
        stop = min(row.low for row in near)
        risk = ask.close - stop
        side = "long"
    elif candle.close < min(row.low for row in recent) and fast < slow and candle.close < candle.open:
        stop = max(row.high for row in near)
        risk = stop - candle.close
        side = "short"
    else:
        return None
    if risk <= 0 or risk < SPREAD_MULT * spread:
        return None
    return {"strategy": variant["id"], "side": side, "stop": stop, "spread": spread,
            "target_r": variant["target_r"], "horizon": HORIZON,
            "reason": f"M1 Donchian{variant['lookback']} {'breakout' if side == 'long' else 'breakdown'}; EMA20/50 cùng chiều; risk ≥ {SPREAD_MULT}× spread"}

def _new_sample(signal, candle, costs):
    return {"strategy": signal["strategy"], "version": VERSION, "side": signal["side"], "stop": signal["stop"], "status": "pending",
            "confirm_time": candle.time, "last_candle": candle.time, "signal": signal, "costs": costs}

def _step(sample, bid, ask):
    from scripts.exness_mtf_replay import advance
    advance(sample, bid, ask, slippage_bps=sample["costs"]["slippage_bps"],
            commission_per_unit=sample["costs"]["commission_per_unit"] or 0,
            horizon_bars=sample["signal"].get("horizon", HORIZON), step=STEP,
            target_r=sample["signal"].get("target_r", TARGET_R))

def _lower(values):
    return mean(values) - 1.96 * stdev(values) / math.sqrt(len(values)) if len(values) > 1 else None

def stats(samples):
    values = [row["net_r_ex_swap"] for row in samples if row["status"] == "resolved"]
    return {"signals": len(samples), "resolved": len(values),
            "excluded": sum(row["status"] in ("excluded", "invalid", "skipped") for row in samples),
            "mean_r": mean(values) if values else None, "lower_r": _lower(values),
            "win_rate": sum(value > 0 for value in values) / len(values) if values else None}

def replay(bids, asks, costs, variant=None):
    """Non-overlapping replay over stored M1 history; train/validation split by time (70/30)."""
    samples, active = [], None
    for index, candle in enumerate(bids):
        ask = asks.get(candle.time)
        if ask is None:
            continue
        if active:
            _step(active, candle, ask)
            if active["status"] not in ("pending", "open"):
                samples.append(active)
                active = None
        if active is None and index >= 60:
            signal = scalp_signal(candle, bids[index - 60:index], ask, variant)
            if signal:
                active = _new_sample(signal, candle, costs)
    resolved = [row for row in samples if row["status"] == "resolved"]
    cut = bids[int(len(bids) * 0.7)].time if bids else 0
    train = [row for row in samples if row["confirm_time"] < cut
             and row.get("label_available_at", row["last_candle"] + STEP) < cut]
    validation = [row for row in samples if row["confirm_time"] >= cut]
    return {"candles": len(bids), "from": bids[0].time if bids else None, "to": bids[-1].time if bids else None,
            "all": stats(samples), "train": stats(train), "validation": stats(validation),
            "boundary_excluded": sum(row["confirm_time"] < cut <= row.get("label_available_at", row["last_candle"] + STEP)
                                     for row in samples),
            "ambiguous_stop_first": sum(bool(row.get("ambiguous_bar")) for row in resolved)}

def train_candidate(results):
    eligible = [row for row in results if row["train"]["resolved"] >= 30
                and row["train"]["lower_r"] is not None and row["train"]["lower_r"] > 0]
    return max(eligible, key=lambda row: row["train"]["lower_r"])["id"] if eligible else None

def gate(summary, costs_verified):
    reasons = []
    for name in ("train", "validation"):
        part = summary[name]
        if part["resolved"] < 30:
            reasons.append(f"Thiếu mẫu {name} {part['resolved']}/30")
        elif part["lower_r"] is None or part["lower_r"] <= 0:
            reasons.append(f"Lower-bound R {name} ≤ 0")
    if not costs_verified:
        reasons.append("Chưa xác minh commission/slippage từ sàn")
    return reasons

def _path(account):
    base = Path(__file__).resolve().parents[2] / "research-output"
    return base / f"exness-scalp-{hashlib.sha256(account.encode()).hexdigest()[:16]}.json"

def _costs(session):
    from .forex_learner import slippage_estimate
    specs = session.get(ForexInstrumentState, WORKSPACE)
    commission = specs.snapshot.get("commission_per_unit") if specs and specs.snapshot else None
    slippage = slippage_estimate()
    if commission is not None and (not isinstance(commission, (int, float)) or isinstance(commission, bool)
                                   or not math.isfinite(commission) or commission < 0):
        raise ValueError("Invalid scalping commission")
    return {"slippage_bps": slippage["bps"], "commission_per_unit": commission,
            "slippage_basis": "demo_fill_estimate_not_m1_calibrated", "verified": False}

def _load(session, limit=None, start=None):
    query = select(WorkspaceCandle).where(
        WorkspaceCandle.workspace_id == WORKSPACE, WorkspaceCandle.symbol == "XAUUSD",
        WorkspaceCandle.timeframe == "1m")
    if start is not None:
        query = query.where(WorkspaceCandle.time >= start)
    rows = session.scalars(query.order_by(WorkspaceCandle.time.desc(), WorkspaceCandle.price_type)
                           .limit(limit) if limit else query.order_by(WorkspaceCandle.time)).all()
    rows = sorted(rows, key=lambda row: row.time)
    return ([row for row in rows if row.price_type == "bid"],
            {row.time: row for row in rows if row.price_type == "ask"})

def scalp_tick(now=None):
    now = int(time.time()) if now is None else now
    account = os.getenv("EXNESS_ACCOUNT_ID", "").strip()
    if not account:
        return "unconfigured"
    path = _path(account)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix(".lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return "busy"
        ledger = json.loads(path.read_text()) if path.exists() else {
            "account": account, "since": now, "last_time": None, "samples": [], "replay": None}
        with Session() as session:
            feed = session.get(WorkspaceFeedState, (WORKSPACE, "1m"))
            costs = _costs(session)
            if (not ledger["replay"] or ledger["replay"].get("version") != VERSION
                    or (feed and ledger["replay"].get("history_start") != feed.started_at)
                    or now - ledger["replay"]["computed_at"] > 3600):
                bids, asks = _load(session)
                bids = [row for row in bids if row.time + STEP <= now]
                if bids:
                    results = [{"id": variant["id"], "label": variant["label"],
                                **replay(bids, asks, costs, variant)} for variant in VARIANTS]
                    ledger["replay"] = {**results[0], "version": VERSION, "computed_at": now, "costs": costs,
                                        "history_start": feed.started_at if feed else None,
                                        "variants": results, "train_candidate": train_candidate(results)}
            if not feed or feed.error or feed.checked_until is None or feed.checked_until < now // 60 * 60:
                ledger["status"] = "waiting_m1_feed"
                _save(path, ledger)
                return "waiting_m1_feed"
            bids, asks = _load(session, limit=400)
        bids = [row for row in bids if row.time + STEP <= now]
        fresh = [row for row in bids if ledger["last_time"] is None and row.time + STEP >= ledger["since"]
                 or ledger["last_time"] is not None and row.time > ledger["last_time"]]
        for index, candle in enumerate(bids):
            if candle not in fresh or candle.time not in asks:
                continue
            for sample in ledger["samples"]:
                if sample["status"] in ("pending", "open"):
                    _step(sample, candle, asks[candle.time])
            timely = candle.time + STEP >= ledger["since"] and 0 <= now - (candle.time + STEP) <= 30
            if timely:
                for variant in VARIANTS:
                    busy = any(sample["status"] in ("pending", "open") and sample["strategy"] == variant["id"]
                               for sample in ledger["samples"])
                    signal = None if busy else scalp_signal(candle, bids[max(0, index - 60):index], asks[candle.time], variant)
                    if signal:
                        ledger["samples"].append({**_new_sample(signal, candle, costs), "recorded_at": now,
                                                  "provenance": "live_forward_shadow"})
        if fresh:
            ledger["last_time"] = fresh[-1].time
        ledger.update(status="running", updated_at=now, version=VERSION)
        _save(path, ledger)
        return "running"

def _save(path, ledger):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(ledger, allow_nan=False))
    temporary.replace(path)

def scalp_summary(account):
    path = _path(account)
    if not path.exists():
        return {"status": "no_data"}
    ledger = json.loads(path.read_text())
    replay_part = ledger.get("replay")
    if replay_part and replay_part.get("version") != VERSION:
        replay_part = None
    verified = bool(replay_part and replay_part["costs"]["verified"])
    current = [sample for sample in ledger["samples"] if sample.get("version") == VERSION]
    forward = stats([sample for sample in current if sample["strategy"] == NAME])
    variants = []
    for row in replay_part.get("variants", []) if replay_part else []:
        live = stats([sample for sample in current if sample["strategy"] == row["id"]])
        blockers = gate(row, verified)
        if live["resolved"] < 30:
            blockers.append(f"Thiếu mẫu forward {live['resolved']}/30")
        elif live["lower_r"] is None or live["lower_r"] <= 0:
            blockers.append("Lower-bound R forward ≤ 0")
        variants.append({**row, "forward": live, "gate": blockers})
    reasons = gate(replay_part, verified) if replay_part else ["Chưa có replay"]
    if forward["resolved"] < 30:
        reasons.append(f"Thiếu mẫu forward {forward['resolved']}/30")
    elif forward["lower_r"] is None or forward["lower_r"] <= 0:
        reasons.append("Lower-bound R forward ≤ 0")
    return {"status": ledger.get("status"), "since": ledger["since"], "updated_at": ledger.get("updated_at"),
            "strategy": NAME, "version": VERSION, "rule": f"M1 Donchian20 + EMA20/50, SL 5 nến, TP {TARGET_R}R, tối đa {HORIZON} phút",
            "replay": replay_part, "forward": forward,
            "variants": variants,
            "train_candidate": replay_part.get("train_candidate") if replay_part else None,
            "recent": list(reversed(ledger["samples"][-15:])),
            "gate": reasons, "executor": "shadow_only", "max_positions": scalp_max_positions(),
            "assumptions": "Bid/ask, next M1 open, stop-first, no swap; slippage proxy, not tick/latency simulation"}
