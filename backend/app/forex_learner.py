"""Closed-candle contextual learner; simulated labels are never broker fills."""

import fcntl
import json
import math
import random
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, stdev

from fastapi import APIRouter
from sqlalchemy import select

from .db import ExnessDeal, ExnessExecutionTrade, ForexInstrumentState, Session
from .exness_feed import WORKSPACE
from .exness_observation import audit_frames, load_frames, observe_forex
from .forex_strategies import RULES, _closed_hours, ema, strategy_snapshot

router = APIRouter(prefix="/api/workspaces/exness_forex/learning")
REPORT = Path(__file__).resolve().parents[2] / "research-output/exness-learning.json"
POLICY = REPORT.with_name("exness-learning-policy.json")
VERSION = "contextual_r_v2"
def slippage_estimate():
    path = REPORT.with_name("exness-slippage-report.json")
    if not path.exists():
        return {"bps": 0.0, "measured": False, "fills": 0}
    audit = json.loads(path.read_text())
    value = audit.get("per_side_mean_bps")
    if not audit.get("demo_only") or audit.get("fills", 0) < 2 or isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("Invalid slippage measurement")
    return {"bps": min(100, max(0, value)), "measured": True,
            "fills": audit["fills"], "measured_at": audit["as_of"]}
SEEDS = [{"mode": mode, "lookback": n, "stop_atr": 1.5, "trend": trend}
         for mode in ("breakout", "fade") for n in (20, 40) for trend in (True, False)]

def genome_name(g):
    return f"gen_{g['mode']}_n{g['lookback']}_k{g['stop_atr']}_{'trend' if g['trend'] else 'free'}"

def genome_signal(g, candle, hours, minutes):
    """Parametric channel rule: breakout or fade of the last N M15 bars, ATR stop."""
    history = [row for row in minutes if row.time < candle.time][-g["lookback"]:]
    closed = _closed_hours(candle, hours)
    if len(history) < g["lookback"] or len(closed) < 50 or history[-1].time != candle.time - 900:
        return None
    high, low = max(row.high for row in history), min(row.low for row in history)
    atr = sum(row.high - row.low for row in history[-14:]) / min(14, len(history))
    if atr <= 0:
        return None
    up = ema([row.close for row in closed], 20) > ema([row.close for row in closed], 50)
    side = None
    if g["mode"] == "breakout":
        side = "long" if candle.close > high else "short" if candle.close < low else None
    elif candle.low < low < candle.close:
        side = "long"
    elif candle.high > high > candle.close:
        side = "short"
    if side is None or (g["trend"] and (side == "long") != up):
        return None
    stop = candle.close - g["stop_atr"] * atr if side == "long" else candle.close + g["stop_atr"] * atr
    return {"strategy": genome_name(g), "status": "confirmed", "side": side, "stop": stop,
            "candle_time": candle.time, "available_at": candle.time + 900, "generated": True,
            "reason": f"Chiến lược tự sinh {genome_name(g)}"}

def mutate(g, rng):
    child = dict(g)
    child["lookback"] = min(80, max(8, g["lookback"] + rng.choice((-10, -5, 5, 10))))
    child["stop_atr"] = min(3.0, max(0.5, round(g["stop_atr"] + rng.choice((-0.5, 0, 0.5)), 1)))
    if rng.random() < 0.2:
        child["trend"] = not g["trend"]
    if rng.random() < 0.15:
        child["mode"] = "fade" if g["mode"] == "breakout" else "breakout"
    return child

def evolve(samples, population, cutoff, seed):
    """Keep best genomes by pre-cutoff mean R, refill with mutations plus two fresh seeds."""
    rng = random.Random(seed)
    scores = {}
    for sample in samples:
        if sample["status"] == "resolved" and sample["label_available_at"] < cutoff:
            scores.setdefault(sample["strategy"], []).append(sample["net_r_ex_swap"])
    ranked = sorted((g for g in population if len(scores.get(genome_name(g), ())) >= 20),
                    key=lambda g: mean(scores[genome_name(g)]), reverse=True)
    survivors = ranked[:6] or population[:6]
    nxt = list(survivors)
    for g in survivors * 3:
        if len(nxt) >= 10:
            break
        child = mutate(g, rng)
        if genome_name(child) not in {genome_name(x) for x in nxt}:
            nxt.append(child)
    for g in rng.sample(SEEDS, len(SEEDS)):
        if len(nxt) >= 12:
            break
        if genome_name(g) not in {genome_name(x) for x in nxt}:
            nxt.append(g)
    return nxt

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
                  "sd": stdev(values) if len(values) > 1 else 0,
                  "lower_r": mean(values) - 1.96 * stdev(values) / math.sqrt(len(values))
                  if len(values) > 1 else -1}
            for key, values in groups.items()}

def choose(candidates, features, model, explore=False):
    """Exploit buckets with positive lower bound; optionally explore by UCB on demo."""
    scored = []
    for signal in candidates:
        stats = model.get(bucket(signal, features), {"count": 0, "mean_r": 0, "sd": 0, "lower_r": -1})
        n = stats["count"]
        ucb = 99 if n < 5 else stats["mean_r"] + stats.get("sd", 0) / math.sqrt(n)
        scored.append({"strategy": signal["strategy"], "side": signal["side"], "ucb": ucb, **stats})
    eligible = [row for row in scored if row["count"] >= 30 and row["lower_r"] > 0]
    best = max(eligible, key=lambda row: row["lower_r"], default=None)
    mode = "exploit"
    if best is None and explore and scored:
        best, mode = max(scored, key=lambda row: row["ucb"]), "explore"
    signal = next((row for row in candidates if best and row["strategy"] == best["strategy"]
                   and row["side"] == best["side"]), None)
    return signal, mode if signal else "abstain", scored

def gather(candle, hours, history, breakout, population):
    found = [strategy_snapshot(name, candle, hours, history) for name in RULES]
    found.append({**breakout, "strategy": "breakout_retest"})
    found += [genome_signal(g, candle, hours, history) or {"status": "none"} for g in population]
    return [row for row in found if row["status"] == "confirmed"]

def live_signal(candle, hours, minutes, ask, breakout):
    """Decision for the executor at candle close. Policy comes from the last training pass."""
    if not POLICY.exists():
        return None
    policy = json.loads(POLICY.read_text())
    if policy.get("version") != VERSION:
        return None
    candidates = gather(candle, hours, minutes[-61:-1] if minutes and minutes[-1].time == candle.time else minutes[-60:],
                        breakout or {"status": "none"}, policy["population"])
    signal, mode, scored = choose(candidates, context(candle, hours, ask), policy["model"], explore=True)
    if not signal:
        return None
    return {**signal, "status": "confirmed", "candle_time": candle.time, "available_at": candle.time + 900,
            "policy_mode": mode, "policy_updated_at": policy["updated_at"],
            "policy_features": context(candle, hours, ask),
            "reason": f"{mode}: {signal['strategy']} · {signal['reason']}"}

def train(frames, now, commission_per_unit=None, existing=None, population=None, slippage=None):
    from scripts.exness_mtf_replay import advance
    slippage = slippage or slippage_estimate()

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
    population = existing["population"] if existing else (population or SEEDS)
    for index, candle in enumerate(minutes):
        if existing and candle.time <= existing["latest"]["time"]:
            continue
        for sample in active:
            advance(sample, candle, asks[candle.time], slippage_bps=slippage["bps"],
                    commission_per_unit=commission_per_unit or 0)
        active = [sample for sample in active if sample["status"] in ("open", "pending")]
        breakout = observe_forex(candle, bids["1h"], bids["4h"], previous)
        previous = breakout
        history = minutes[max(0, index - 60):index]
        baseline = strategy_snapshot("regime_router", candle, bids["1h"], history,
                                     higher=bids["4h"], breakout=breakout)
        candidates = gather(candle, bids["1h"], history, breakout, population)
        features = context(candle, bids["1h"], asks[candle.time])
        if model is None and candle.time >= cutoff:
            model = fit(samples, cutoff)
        selected, _, scored = choose(candidates, features, model or {})
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
    selected, live_mode, scored = choose(latest_candidates, context(latest, bids["1h"], asks[latest.time]), model or {}, explore=True)
    return {"version": VERSION, "updated_at": now, "fitted_at": existing["fitted_at"] if existing else now,
            "processed_bars": len(minutes), "train_before": cutoff, "breakout_state": previous,
            "mode": "live_demo", "execution_enabled": True, "validation": validation,
            "reason": "Bộ học điều khiển lệnh demo: khai thác bucket đã có bằng chứng, thăm dò khi chưa đủ dữ liệu; số liệu replay là mô phỏng",
            "assumptions": {"slippage_bps_each_side": slippage["bps"], "slippage_measurement": slippage, "commission_per_unit": commission_per_unit,
                            "commission_known": commission_per_unit is not None, "swap_included": False,
                            "overlapping_samples": True, "entry": "next M15 executable open", "stop_first": True},
            "model": model or {}, "population": population,
            "next_population": evolve(samples, population, cutoff, now // 21600) if not existing else existing.get("next_population", population),
            "strategy_summary": {
                name: {"signals": sum(row["strategy"] == name for row in samples),
                       "closed": sum(row["strategy"] == name and row["status"] == "resolved" for row in samples),
                       "mean_r": mean([row["net_r_ex_swap"] for row in samples if row["strategy"] == name and row["status"] == "resolved"])
                       if any(row["strategy"] == name and row["status"] == "resolved" for row in samples) else None,
                       "excluded": sum(row["strategy"] == name and row["status"] == "excluded" for row in samples)}
                for name in ["breakout_retest", *RULES, "regime_router", *(genome_name(g) for g in population)]},
            "latest": {"time": latest.time, "selected": selected["strategy"] if selected else None, "mode": live_mode, "scores": scored},
            "samples": samples, "decisions": decisions}

def broker_labels(session, account):
    """Actual demo profit/initial price risk, separate provenance from simulated labels."""
    trades = session.scalars(select(ExnessExecutionTrade).where(ExnessExecutionTrade.account == account)).all()
    deals = session.scalars(select(ExnessDeal).where(ExnessDeal.account == account)).all()
    labels = []
    for row in trades:
        data = row.snapshot
        signal = data.get("signal", {})
        if data.get("status") != "closed" or not signal.get("policy_features"):
            continue
        fills = [deal.snapshot for deal in deals if deal.position_id == data.get("position_id")]
        opens = [deal for deal in fills if deal.get("type") == "open"]
        exits = [deal for deal in fills if deal.get("type") == "close"]
        if not opens or not exits or any("profit" not in deal for deal in exits):
            continue
        volume = sum(float(deal["volume"]) for deal in opens)
        if volume <= 0 or sum(float(deal["volume"]) for deal in exits) + 1e-9 < volume:
            continue
        entry = sum(float(deal["price"]) * float(deal["volume"]) for deal in opens) / volume
        risk = abs(entry - float(data["body"]["stop_loss_price"])) * volume * 100
        if risk <= 0:
            continue
        labels.append({"status": "resolved", "bucket": bucket(signal, signal["policy_features"]),
                       "net_r_ex_swap": sum(float(deal["profit"]["amount"]) for deal in exits) / risk,
                       "label_available_at": max(deal["event_time"] for deal in exits),
                       "provenance": "broker_demo_profit_ex_fees", "position_id": data["position_id"]})
    return labels

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
            import os
            actual = broker_labels(session, os.getenv("EXNESS_ACCOUNT_ID", ""))
        slippage = slippage_estimate()
        previous = existing
        if force or (existing and (now - existing["fitted_at"] >= 21600 or existing["assumptions"]["commission_per_unit"] != commission
                                  or existing["assumptions"]["slippage_bps_each_side"] != slippage["bps"])):
            existing = None
        report = train(frames, now, commission, existing,
                       population=(previous or {}).get("next_population"), slippage=slippage)
        report["broker_learning"] = {"closed_labels": len(actual), "fees_included": False,
                                      "note": "Broker profit labels; commission/swap not included"}
        policy = {"version": VERSION, "updated_at": now, "model": fit(report["samples"] + actual, now),
                  "population": report["population"]}
        policy_tmp = POLICY.with_suffix(".tmp")
        policy_tmp.write_text(json.dumps(policy, allow_nan=False))
        policy_tmp.replace(POLICY)
        temporary = REPORT.with_suffix(".tmp")
        temporary.write_text(json.dumps(report, ensure_ascii=False, allow_nan=False))
        temporary.replace(REPORT)
        return "trained"
