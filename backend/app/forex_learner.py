"""Closed-candle contextual learner; simulated labels are never broker fills."""

import fcntl
import json
import math
import random
import time
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from pathlib import Path
from statistics import mean, stdev

from fastapi import APIRouter
from sqlalchemy import select

from .db import ExnessDeal, ExnessExecutionTrade, ForexInstrumentState, ForexObservation, Session, WorkspaceCandle
from .exness_feed import FRAMES, WORKSPACE
from .forex_buffer_study import advance_variant, build_buffer_model, exit_target, new_variants, select_buffer, slot_key
from .exness import ExnessError
from .exness_targets import target_context
from .exness_management import structure_stop_buffer
from .exness_observation import audit_frames, load_frames, observe_forex
from .forex_strategies import RULES, _closed_hours, ema, rsi, strategy_snapshot

router = APIRouter(prefix="/api/workspaces/exness_forex/learning")
REPORT = Path(__file__).resolve().parents[2] / "research-output/exness-learning.json"
POLICY = REPORT.with_name("exness-learning-policy.json")
VERSION = "contextual_r_v5"
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
FAMILIES = ("channel", "rsi", "fvg", "session", "squeeze", "sweep", "pullback", "range", "retest", "router")
CAP = 40
SEEDS = [
    {"family": "channel", "mode": mode, "lookback": n, "stop_atr": 1.5, "trend": trend}
    for mode in ("breakout", "fade") for n in (20, 40) for trend in (True, False)
] + [
    {"family": "rsi", "period": 14, "oversold": 30, "overbought": 70, "stop_atr": 1.0, "trend": False},
    {"family": "rsi", "period": 9, "oversold": 25, "overbought": 75, "stop_atr": 1.5, "trend": True},
    {"family": "fvg", "min_gap": 0.0, "stop_atr": 1.0, "trend": True},
    {"family": "fvg", "min_gap": 0.3, "stop_atr": 1.5, "trend": False},
    {"family": "session", "start_hour": 7, "end_hour": 12, "stop_atr": 1.5, "trend": True},
    {"family": "session", "start_hour": 8, "end_hour": 12, "stop_atr": 1.0, "trend": False},
    {"family": "squeeze", "lookback": 40, "bars": 10, "ratio": 3.0, "stop_atr": 1.2, "trend": True},
    {"family": "sweep", "lookback": 20, "stop_atr": 1.0, "trend": False},
    {"family": "pullback", "entry_ema": 20, "fast_ema": 20, "stop_atr": 1.5, "trend": True},
    {"family": "range", "lookback": 20, "edge": 0.1, "efficiency": 0.5, "stop_atr": 1.5, "trend": False},
    {"family": "retest", "lookback": 20, "wait_hours": 12, "stop_atr": 1.5, "trend": True},
    {"family": "router", "separation": 0.5, "stop_atr": 1.5, "trend": False},
]

def genome_name(g):
    tag = "trend" if g["trend"] else "free"
    family = g.get("family", "channel")
    if family == "channel":
        return f"gen_{g['mode']}_n{g['lookback']}_k{g['stop_atr']}_{tag}"
    params = "_".join(f"{key}{g[key]}" for key in sorted(g) if key not in ("family", "stop_atr", "trend", "generation", "parents"))
    return f"gen_{family}_{params}_k{g['stop_atr']}_{tag}"

def genome_signal(g, candle, hours, minutes, higher=()):
    """Parametric closed-bar rule per family; ATR stop; trend=True requires H1 EMA20/50 agreement."""
    family = g.get("family", "channel")
    validate_genome(g)
    history = [row for row in minutes if row.time < candle.time][-96:]
    closed = _closed_hours(candle, hours)
    if (len(history) < 20 or len(closed) < 50 or history[-1].time != candle.time - 900
            or closed[-1].time + 7200 <= candle.time + 900):
        return None
    needed = g.get("lookback", 20) if family in ("channel", "sweep", "squeeze") else 20
    if any(after.time - before.time != 900 for before, after in zip(history[-needed:], history[-needed + 1:])):
        return None
    atr = sum(row.high - row.low for row in history[-14:]) / 14
    if atr <= 0:
        return None
    side = None
    if family in ("channel", "sweep"):
        window = history[-g["lookback"]:]
        if len(window) < g["lookback"]:
            return None
        high, low = max(row.high for row in window), min(row.low for row in window)
        if family == "channel" and g["mode"] == "breakout":
            side = "long" if candle.close > high else "short" if candle.close < low else None
        elif candle.low < low < candle.close:
            side = "long"
        elif candle.high > high > candle.close:
            side = "short"
        if family == "sweep" and (
                side == "long" and (candle.close <= candle.open or candle.high > high)
                or side == "short" and (candle.close >= candle.open or candle.low < low)):
            side = None
    elif family == "rsi":
        value = rsi([row.close for row in history], g["period"])
        if value < g["oversold"] and candle.close > candle.open and candle.close > history[-1].close:
            side = "long"
        elif value > g["overbought"] and candle.close < candle.open and candle.close < history[-1].close:
            side = "short"
    elif family == "fvg":
        first, middle, last = history[-3:]
        if (last.low - first.high >= g["min_gap"] * atr and last.low > first.high and middle.close > middle.open
                and candle.low <= last.low < candle.close and candle.close > candle.open):
            side = "long"
        elif (first.low - last.high >= g["min_gap"] * atr and last.high < first.low and middle.close < middle.open
                and candle.high >= last.high > candle.close and candle.close < candle.open):
            side = "short"
    elif family == "session":
        stamp = datetime.fromtimestamp(candle.time, timezone.utc)
        if not g["start_hour"] <= stamp.hour < g["end_hour"]:
            return None
        day = int(datetime(stamp.year, stamp.month, stamp.day, tzinfo=timezone.utc).timestamp())
        asian = [row for row in history if day <= row.time < day + g["start_hour"] * 3600]
        if [row.time for row in asian] != list(range(day, day + g["start_hour"] * 3600, 900)):
            return None
        high, low = max(row.high for row in asian), min(row.low for row in asian)
        if any(row.close > high or row.close < low for row in history if row.time >= day + g["start_hour"] * 3600):
            return None
        side = "long" if candle.close > high and candle.close > candle.open else \
               "short" if candle.close < low and candle.close < candle.open else None
    elif family == "squeeze":
        window = history[-g["lookback"]:]
        if len(window) < g["lookback"]:
            return None
        recent, prior = window[-g["bars"]:], window[:-g["bars"]]
        typical = sum(row.high - row.low for row in prior) / len(prior)
        high, low = max(row.high for row in recent), min(row.low for row in recent)
        if typical > 0 and high - low <= g["ratio"] * typical:
            side = "long" if candle.close > high else "short" if candle.close < low else None
    elif family == "pullback":
        fast, slow = ema([row.close for row in closed], g["fast_ema"]), ema([row.close for row in closed], 50)
        level = ema([row.close for row in history[-60:]], g["entry_ema"])
        if fast > slow and closed[-1].close > fast and candle.low <= level < candle.close and candle.close > candle.open:
            side = "long"
        elif fast < slow and closed[-1].close < fast and candle.high >= level > candle.close and candle.close < candle.open:
            side = "short"
    elif family == "range":
        window = [row for row in closed if row.time + 3600 <= candle.time][-g["lookback"]:]
        if len(window) < g["lookback"]:
            return None
        high, low = max(row.high for row in window), min(row.low for row in window)
        width = high - low
        if width <= 0 or abs(window[-1].close - window[0].close) > g["efficiency"] * width:
            return None
        if candle.high >= high - g["edge"] * width > candle.close and candle.close < candle.open:
            side = "short"
        elif candle.low <= low + g["edge"] * width < candle.close and candle.close > candle.open:
            side = "long"
    elif family == "retest":
        closed_h4 = [row for row in higher if row.time + 14400 <= candle.time + 900]
        if len(closed_h4) < 50 or closed_h4[-1].time + 28800 <= candle.time + 900:
            return None
        h4_ema = ema([row.close for row in closed_h4], 50)
        direction = 1 if closed_h4[-1].close > h4_ema else -1 if closed_h4[-1].close < h4_ema else 0
        for index in range(len(closed) - 1, g["lookback"] - 1, -1):
            breakout = closed[index]
            available = breakout.time + 3600
            if candle.time < available or candle.time + 900 - available > g["wait_hours"] * 3600:
                continue
            window = closed[index - g["lookback"]:index]
            level = max(row.high for row in window) if direction == 1 else min(row.low for row in window)
            if not direction or direction * (breakout.close - level) <= 0:
                continue
            since = [row for row in history if row.time >= available]
            if candle.time != available and (not since or since[0].time != available):
                return None
            if any(direction * (row.close - level) < 0 for row in since):
                return None
            if any(row.low <= level < row.close if direction == 1 else row.high >= level > row.close for row in since):
                return None
            if candle.low <= level < candle.close and direction == 1 and candle.close > candle.open:
                side = "long"
            elif candle.high >= level > candle.close and direction == -1 and candle.close < candle.open:
                side = "short"
            break
    elif family == "router":
        closes = [row.close for row in closed]
        fast, slow = ema(closes, 20), ema(closes, 50)
        h1_range = sum(row.high - row.low for row in closed[-14:]) / 14
        rule = "trend_pullback" if abs(fast - slow) > g["separation"] * h1_range else "range_rejection"
        signal = RULES[rule](candle, closed, history)
        side = signal["side"] if signal else None
    fast, slow = ema([row.close for row in closed], 20), ema([row.close for row in closed], 50)
    if side is None or (g["trend"] and (fast == slow or (side == "long") != (fast > slow))):
        return None
    stop = candle.close - g["stop_atr"] * atr if side == "long" else candle.close + g["stop_atr"] * atr
    return {"strategy": genome_name(g), "status": "confirmed", "side": side, "stop": stop,
            "candle_time": candle.time, "available_at": candle.time + 900, "generated": True,
            "family": family, "generation": g.get("generation", 0), "parents": g.get("parents", []),
            "reason": f"Chiến lược tự sinh {genome_name(g)}"}

# (key, step, low, high) per family; every child stays inside these bounds.
BOUNDS = {
    "channel": (("lookback", 5, 8, 60),),
    "sweep": (("lookback", 5, 10, 40),),
    "squeeze": (("lookback", 10, 20, 60), ("bars", 2, 5, 15), ("ratio", 0.1, 1.0, 5.0)),
    "rsi": (("period", 1, 7, 21), ("oversold", 5, 20, 35), ("overbought", 5, 65, 80)),
    "fvg": (("min_gap", 0.1, 0.0, 1.0),),
    "session": (("start_hour", 1, 6, 9), ("end_hour", 1, 11, 15)),
    "pullback": (("entry_ema", 5, 10, 40), ("fast_ema", 5, 10, 30)),
    "range": (("lookback", 5, 10, 40), ("edge", 0.05, 0.05, 0.25), ("efficiency", 0.1, 0.2, 0.6)),
    "retest": (("lookback", 5, 10, 40), ("wait_hours", 2, 2, 12)),
    "router": (("separation", 0.1, 0.2, 1.0),),
}

def validate_genome(g):
    family = g.get("family", "channel")
    if family not in BOUNDS or type(g.get("trend")) is not bool:
        raise ValueError("Invalid genome family/filter")
    bounds = (*BOUNDS[family], ("stop_atr", 0.1, 0.5, 3.0))
    for key, step, low, high in bounds:
        value = g.get(key)
        if (type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high
                or isinstance(step, int) and type(value) is not int):
            raise ValueError(f"Invalid genome parameter: {key}")
    if family == "channel" and g.get("mode") not in ("breakout", "fade"):
        raise ValueError("Invalid genome mode")
    if family == "squeeze" and g["bars"] >= g["lookback"]:
        raise ValueError("Invalid compression window")
    if family == "session" and g["start_hour"] >= g["end_hour"]:
        raise ValueError("Invalid session window")

def mutate(g, rng):
    validate_genome(g)
    child = dict(g)
    family = g.get("family", "channel")
    child["family"] = family
    for key, step, low, high in BOUNDS[family]:
        value = g[key] + rng.choice((-2, -1, 0, 1, 2)) * step
        child[key] = round(min(high, max(low, value)), 1) if isinstance(step, float) else int(min(high, max(low, value)))
    child["stop_atr"] = min(3.0, max(0.5, round(g["stop_atr"] + rng.choice((-0.5, 0, 0.5)), 1)))
    if rng.random() < 0.2:
        child["trend"] = not g["trend"]
    if family == "channel" and rng.random() < 0.15:
        child["mode"] = "fade" if g["mode"] == "breakout" else "breakout"
    child["generation"] = g.get("generation", 0) + 1
    child["parents"] = [genome_name(g)]
    validate_genome(child)
    return child

def crossover(a, b):
    """Signal parameters from a; trend filter and stop width from b."""
    validate_genome(a)
    validate_genome(b)
    return {**a, "family": a.get("family", "channel"), "trend": b["trend"], "stop_atr": b["stop_atr"],
            "generation": max(a.get("generation", 0), b.get("generation", 0)) + 1,
            "parents": [genome_name(a), genome_name(b)]}

def evolve(samples, population, cutoff, seed, retired=()):
    """Use training labels only; reserve two slots per family and cap each at four."""
    rng = random.Random(seed)
    scores = {}
    for sample in samples:
        if (sample["status"] == "resolved" and sample["label_available_at"] < cutoff
                and math.isfinite(sample["net_r_ex_swap"])):
            scores.setdefault(sample["strategy"], []).append(sample["net_r_ex_swap"])
    def value(g):
        return mean(scores[genome_name(g)]) if genome_name(g) in scores else -math.inf
    retired = set(retired) | {name for name, values in scores.items() if len(values) >= 40 and mean(values) < 0}
    alive = []
    for g in population:
        validate_genome(g)
        if genome_name(g) not in retired:
            alive.append(g)
    nxt, names = [], set()
    for family in FAMILIES:
        members = [g for g in alive if g.get("family", "channel") == family]
        ranked = sorted(members, key=value, reverse=True)
        seeds = [g for g in SEEDS if g["family"] == family]
        group = []
        for g in ranked[:2] + seeds:
            name = genome_name(g)
            if name not in names and name not in retired and len(group) < 2:
                group.append(g)
                names.add(name)
        for _ in range(128):
            if len(group) >= 4 or len(nxt) + len(group) >= CAP:
                break
            parent = rng.choice(group or seeds)
            mate = rng.choice(alive or SEEDS)
            crossed = rng.random() < 0.3
            child = mutate(crossover(parent, mate) if crossed else parent, rng)
            if crossed:
                child["parents"] = [genome_name(parent), genome_name(mate)]
            name = genome_name(child)
            if name not in names and name not in retired:
                group.append(child)
                names.add(name)
        nxt.extend(group)
    return nxt

def promoted_names(samples, cutoff, now):
    """Generated strategies may trade only with positive lower-bound R on train AND validation (30+ each)."""
    groups = defaultdict(lambda: ([], []))
    for sample in samples:
        if (sample["status"] != "resolved" or not sample["strategy"].startswith("gen_")
                or sample["label_available_at"] >= now or not math.isfinite(sample["net_r_ex_swap"])):
            continue
        if sample["confirm_time"] + 900 < cutoff and sample["label_available_at"] < cutoff:
            groups[sample["strategy"]][0].append(sample["net_r_ex_swap"])
        elif sample["confirm_time"] + 900 >= cutoff:
            groups[sample["strategy"]][1].append(sample["net_r_ex_swap"])
    def lower(values):
        return mean(values) - 1.96 * stdev(values) / math.sqrt(len(values)) if len(values) >= 30 else -1
    return sorted(name for name, (train, val) in groups.items() if lower(train) > 0 and lower(val) > 0)

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

def choose(candidates, features, model, explore=False, promoted=()):
    """Unapproved genomes stay shadow-only, including when demo exploration is enabled."""
    scored = []
    for signal in candidates:
        stats = model.get(bucket(signal, features), {"count": 0, "mean_r": 0, "sd": 0, "lower_r": -1})
        n = stats["count"]
        ucb = 99 if n < 5 else stats["mean_r"] + stats.get("sd", 0) / math.sqrt(n)
        shadow = (signal.get("generated") or signal["strategy"].startswith("gen_")) and signal["strategy"] not in promoted
        scored.append({"strategy": signal["strategy"], "side": signal["side"], "ucb": ucb, **stats,
                       "shadow_only": bool(shadow)})
    eligible = [row for row in scored if not row["shadow_only"] and row["count"] >= 30 and row["lower_r"] > 0]
    best = max(eligible, key=lambda row: row["lower_r"], default=None)
    mode = "exploit"
    exploratory = [row for row in scored if not row["shadow_only"]]
    if best is None and explore and exploratory:
        best, mode = max(exploratory, key=lambda row: row["ucb"]), "explore"
    signal = next((row for row in candidates if best and row["strategy"] == best["strategy"]
                   and row["side"] == best["side"]), None)
    return signal, mode if signal else "abstain", scored

def gather(candle, hours, history, breakout, population, higher=(), include_waiting=False):
    hours = _closed_hours(candle, hours)
    higher = [row for row in higher if row.time + 14400 <= candle.time + 900][-60:]
    found = [strategy_snapshot(name, candle, hours, history) for name in RULES]
    found.append({**breakout, "strategy": "breakout_retest"})
    found += [genome_signal(g, candle, hours, history, higher=higher) or {
        "strategy": genome_name(g), "status": "waiting_regime_setup",
        "reason": f"Chưa có tín hiệu {genome_name(g)}",
    } for g in population]
    return found if include_waiting else [row for row in found if row["status"] == "confirmed"]

def learner_decision(candle, hours, minutes, ask, breakout, policy, higher=()):
    if len(policy["population"]) > CAP:
        raise ValueError("Genome population exceeds limit")
    features = context(candle, hours, ask)
    observations = gather(candle, hours,
                          [row for row in minutes if row.time < candle.time][-96:],
                          breakout or {"status": "none", "reason": "Chờ quan sát breakout đúng nến"},
                          policy["population"], higher=higher, include_waiting=True)
    candidates = [row for row in observations if row["status"] == "confirmed"]
    signal, mode, scored = choose(candidates, features, policy["model"], explore=True,
                                  promoted=set(policy.get("promoted", ())))
    if signal:
        signal = {**signal, **select_buffer(signal, features, policy.get("exit_model", {})),
                  "status": "confirmed", "candle_time": candle.time, "available_at": candle.time + 900,
                  "policy_mode": mode, "policy_updated_at": policy["updated_at"], "policy_features": features,
                  "reason": f"{mode}: {signal['strategy']} · {signal['reason']}"}
    return signal, mode, scored, observations

def live_signal(candle, hours, minutes, ask, breakout, higher=()):
    """Decision for the executor at candle close. Policy comes from the last training pass."""
    if not POLICY.exists():
        return None
    policy = json.loads(POLICY.read_text())
    if policy.get("version") != VERSION:
        return None
    return learner_decision(candle, hours, minutes, ask, breakout, policy, higher=higher)[0]

def train(frames, now, commission_per_unit=None, existing=None, population=None, slippage=None, condition=None, retired=()):
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
    if len(population) > CAP:
        raise ValueError("Genome population exceeds limit")
    for genome in population:
        validate_genome(genome)
    exit_samples = existing.get("exit_study_samples", []) if existing else []
    exit_active = [row for row in exit_samples if row["status"] in ("pending", "open")]
    for index, candle in enumerate(minutes):
        if existing and candle.time <= existing["latest"]["time"]:
            continue
        for sample in active:
            advance(sample, candle, asks[candle.time], slippage_bps=slippage["bps"],
                    commission_per_unit=commission_per_unit or 0)
        active = [sample for sample in active if sample["status"] in ("open", "pending")]
        for row in exit_active:
            advance_variant(row, candle, asks[candle.time], minutes[max(0, index - 59):index + 1], slippage["bps"] / 10000, commission_per_unit or 0)
        exit_active = [row for row in exit_active if row["status"] in ("open", "pending")]
        breakout = observe_forex(candle, bids["1h"], bids["4h"], previous)
        previous = breakout
        history = minutes[max(0, index - 96):index]
        baseline = strategy_snapshot("regime_router", candle, bids["1h"], history,
                                     higher=bids["4h"], breakout=breakout)
        candidates = gather(candle, bids["1h"], history, breakout, population, higher=bids["4h"])
        features = context(candle, bids["1h"], asks[candle.time])
        if model is None and candle.time >= cutoff:
            model = fit(samples, cutoff)
        selected, _, scored = choose(candidates, features, model or {})
        indices = {}
        if baseline["status"] == "confirmed":
            candidates.append(baseline)
        occupied = {row.get("slot", row["context"]) for row in exit_active}
        for signal in candidates:
            if condition and signal["strategy"] != "regime_router" and slot_key(signal, features) not in occupied:
                try:
                    rows = new_variants(signal, history + [candle], bids["1h"], bids["4h"], features, candle, condition)
                except ExnessError:
                    rows = []
                exit_samples.extend(rows)
                exit_active.extend(rows)
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
    training_results = defaultdict(list)
    for sample in samples:
        if (sample["status"] == "resolved" and sample["strategy"].startswith("gen_")
                and sample["label_available_at"] < cutoff and math.isfinite(sample["net_r_ex_swap"])):
            training_results[sample["strategy"]].append(sample["net_r_ex_swap"])
    retired = sorted(set(retired) | set((existing or {}).get("retired_genomes", ())) | {
        name for name, values in training_results.items() if len(values) >= 40 and mean(values) < 0})
    costs_verified = commission_per_unit is not None and slippage.get("measured", False)
    promoted = [name for name in promoted_names(samples, cutoff, now) if name not in retired] if costs_verified else []
    latest_candidates = [row["signal"] for row in samples if row["confirm_time"] == latest.time and row["strategy"] != "regime_router"]
    selected, live_mode, scored = choose(latest_candidates, context(latest, bids["1h"], asks[latest.time]), model or {}, explore=True, promoted=set(promoted))
    for row in exit_samples:
        if row["status"] not in ("pending", "open"):
            for key in ("signal", "condition", "atr", "legs"):
                row.pop(key, None)
    return {"version": VERSION, "updated_at": now, "fitted_at": existing["fitted_at"] if existing else now,
            "processed_bars": len(minutes), "train_before": cutoff, "breakout_state": previous,
            "mode": "live_demo", "execution_enabled": True, "validation": validation,
            "reason": "Chiến lược gốc có thể thăm dò demo; gen mới chạy shadow đến khi đủ 30 mẫu train và 30 validation, lower-bound R dương cả hai; replay là mô phỏng",
            "assumptions": {"slippage_bps_each_side": slippage["bps"], "slippage_measurement": slippage, "commission_per_unit": commission_per_unit,
                            "commission_known": commission_per_unit is not None, "swap_included": False,
                            "overlapping_samples": True, "entry": "next M15 executable open", "stop_first": True},
            "model": model or {}, "population": population,
            "promoted": promoted, "retired_genomes": retired,
            "evolution": {"cap": CAP, "families": {family: sum(g.get("family", "channel") == family for g in population) for family in FAMILIES},
                          "shadow": sum(genome_name(g) not in promoted for g in population),
                          "costs_verified": costs_verified,
                          "gate": "30 train + 30 validation, positive lower R in both, measured costs; boundary-crossing trades excluded; simulation only"},
            "next_population": evolve(samples, population, cutoff, now // 21600, retired=retired) if not existing else existing.get("next_population", population),
            "strategy_summary": {
                name: {"signals": sum(row["strategy"] == name for row in samples),
                       "closed": sum(row["strategy"] == name and row["status"] == "resolved" for row in samples),
                       "mean_r": mean([row["net_r_ex_swap"] for row in samples if row["strategy"] == name and row["status"] == "resolved"])
                       if any(row["strategy"] == name and row["status"] == "resolved" for row in samples) else None,
                       "excluded": sum(row["strategy"] == name and row["status"] == "excluded" for row in samples)}
                for name in ["breakout_retest", *RULES, "regime_router", *(genome_name(g) for g in population)]},
            "latest": {"time": latest.time, "selected": selected["strategy"] if selected else None, "mode": live_mode, "scores": scored},
            "samples": samples, "decisions": decisions, "exit_study_samples": exit_samples}

def broker_labels(session, account):
    """Actual demo profit/initial price risk, separate provenance from simulated labels."""
    trades = session.scalars(select(ExnessExecutionTrade).where(ExnessExecutionTrade.account == account)).all()
    deals = session.scalars(select(ExnessDeal).where(ExnessDeal.account == account)).all()
    labels = []
    for row in trades:
        data = row.snapshot
        signal = data.get("signal", {})
        if not signal.get("policy_features"):
            continue
        legs = data.get("legs") or [data]
        if any(leg.get("status") in ("planned", "submitting", "submitted", "uncertain", "open", "closing") for leg in legs):
            continue
        total_risk, total_profit, available = 0.0, 0.0, 0
        valid = True
        for leg in legs:
            pid = leg.get("position_id")
            if leg.get("status") != "closed" or not pid:
                continue
            fills = [deal.snapshot for deal in deals if deal.position_id == pid]
            opens = [deal for deal in fills if deal.get("type") == "open"]
            exits = [deal for deal in fills if deal.get("type") == "close"]
            if not opens or not exits or any("profit" not in deal for deal in exits):
                valid = False
                break
            volume = sum(float(deal["volume"]) for deal in opens)
            if volume <= 0 or sum(float(deal["volume"]) for deal in exits) + 1e-9 < volume:
                valid = False
                break
            entry = sum(float(deal["price"]) * float(deal["volume"]) for deal in opens) / volume
            stop = leg.get("body", {}).get("stop_loss_price")
            if not stop:
                valid = False
                break
            total_risk += abs(entry - float(stop)) * volume * float(leg.get("contract_size", 100))
            total_profit += sum(float(deal["profit"]["amount"]) for deal in exits)
            available = max(available, max(deal["event_time"] for deal in exits))
        if valid and total_risk > 0:
            policy_bucket = bucket(signal, signal["policy_features"])
            if signal.get("exit_variant"):
                policy_bucket += ":exit:" + signal["exit_variant"]
            if data.get("exit_policy") == "structural_tp_v1":
                policy_bucket += ":structural_tp_v1"
            labels.append({"status": "resolved", "bucket": policy_bucket,
                           "net_r_ex_swap": total_profit / total_risk, "label_available_at": available,
                           "provenance": "broker_demo_profit_ex_fees", "position_id":
                           next((leg["position_id"] for leg in legs if leg.get("position_id")), None),
                           "exit_policy": data.get("exit_policy", "fixed_2r")})
    return labels

@router.get("")
def summary():
    if not REPORT.exists():
        return {"mode": "warming_up", "reason": "Waiting for first learning pass"}
    report = json.loads(REPORT.read_text())
    return {key: value for key, value in report.items() if key not in ("samples", "model", "decisions", "breakout_state", "exit_study_samples")}

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
            condition = dict(specs.snapshot) if specs and specs.snapshot else None
            commission = (condition or {}).get("commission_per_unit")
            if condition and any(condition.get(key) is None for key in ("volume_min", "volume_max", "volume_step", "contract_size", "point_digits")):
                condition = None
            import os
            actual = broker_labels(session, os.getenv("EXNESS_ACCOUNT_ID", ""))
        slippage = slippage_estimate()
        previous = existing
        if force or (existing and (now - existing["fitted_at"] >= 21600 or existing["assumptions"]["commission_per_unit"] != commission
                                  or existing["assumptions"]["slippage_bps_each_side"] != slippage["bps"])):
            existing = None
        report = train(frames, now, commission, existing,
                       population=(previous or {}).get("next_population"), slippage=slippage, condition=condition,
                       retired=(previous or {}).get("retired_genomes", ()))
        report["broker_learning"] = {"closed_labels": len(actual), "fees_included": False,
                                      "note": "Broker profit labels; commission/swap not included"}
        costs_verified = commission is not None and slippage["measured"]
        exit_model = build_buffer_model(report.get("exit_study_samples", []), report["train_before"], now, costs_verified)
        report["exit_learning"] = {"default_variant": "k0.25_structural", "costs_verified": costs_verified,
                                   "sample_count": len(report.get("exit_study_samples", [])), "contexts": exit_model,
                                   "assumptions": "Closed M15 simulation; stop-first; partial TP, BE, trailing, early exit; no swap; overlapping contexts"}
        policy = {"version": VERSION, "updated_at": now, "model": fit(report["samples"] + actual, now),
                  "exit_model": exit_model, "population": report["population"],
                  "promoted": report["promoted"]}
        policy_tmp = POLICY.with_suffix(".tmp")
        policy_tmp.write_text(json.dumps(policy, allow_nan=False))
        policy_tmp.replace(POLICY)
        temporary = REPORT.with_suffix(".tmp")
        temporary.write_text(json.dumps(report, ensure_ascii=False, allow_nan=False))
        temporary.replace(REPORT)
        return "trained"


def current_analysis(now=None):
    """Read-only snapshot of what the learner sees at the latest closed M15 bar."""
    now = int(time.time()) if now is None else now
    with Session() as session:
        frames = {
            frame: list(reversed(session.scalars(select(WorkspaceCandle).where(
                WorkspaceCandle.workspace_id == WORKSPACE,
                WorkspaceCandle.symbol == "XAUUSD", WorkspaceCandle.timeframe == frame,
                WorkspaceCandle.time + step <= now,
            ).order_by(WorkspaceCandle.time.desc(), WorkspaceCandle.price_type).limit(162)).all()))
            for frame, (_, step) in FRAMES.items()
        }
        specs = session.get(ForexInstrumentState, WORKSPACE)
        condition = dict(specs.snapshot) if specs and specs.snapshot else None
        obs_row = None
        minutes_bid = [row for row in frames["15m"] if row.price_type == "bid"]
        if minutes_bid:
            obs_row = session.get(ForexObservation, (WORKSPACE, minutes_bid[-1].time))
    bids = {frame: [row for row in frames[frame] if row.price_type == "bid"] for frame in frames}
    minutes = bids["15m"]
    if not minutes:
        return None
    candle = minutes[-1]
    asks = {row.time: row for row in frames["15m"] if row.price_type == "ask"}
    ask = asks.get(candle.time)
    if ask is None:
        return None
    hours, higher = bids["1h"], bids["4h"]
    features = context(candle, hours, ask)
    breakout_snap = obs_row.snapshot if obs_row else None

    history = [row for row in minutes if row.time < candle.time][-20:]
    m15_donchian = {"high": max(row.high for row in history), "low": min(row.low for row in history)} if len(history) >= 20 else None
    ema_history = [row for row in minutes if row.time < candle.time][-60:]
    m15_ema20 = ema([row.close for row in ema_history], 20) if len(ema_history) >= 20 else None
    closed_hours = [row for row in hours if row.time + 3600 <= candle.time + 900][-60:]
    h1_ema20 = h1_ema50 = None
    if len(closed_hours) >= 50:
        closes = [row.close for row in closed_hours]
        h1_ema20, h1_ema50 = ema(closes, 20), ema(closes, 50)
    barriers = []
    tc = None
    issues = []
    try:
        tc = target_context(candle, hours, higher)
        barriers = tc.get("barriers", [])
    except ExnessError as exc:
        issues.append(str(exc))

    strategies = {}
    for name in RULES:
        snap = strategy_snapshot(name, candle, hours, minutes)
        strategies[name] = {"status": snap["status"], "side": snap.get("side"),
                            "reason": snap.get("reason"),
                            "stop": snap.get("stop") if snap.get("status") == "confirmed" else None,
                            "level": snap.get("level")}
    strategies["breakout_retest"] = {
        "status": breakout_snap.get("status", "not_started") if breakout_snap else "not_started",
        "side": breakout_snap.get("side") if breakout_snap else None,
        "reason": breakout_snap.get("reason") if breakout_snap else None,
        "stop": breakout_snap.get("stop") if breakout_snap and breakout_snap.get("status") == "confirmed" else None,
        "level": breakout_snap.get("level") if breakout_snap else None,
    }

    selected_signal = None
    policy_mode = "warming_up"
    policy_updated_at = None
    scored = []
    if POLICY.exists():
        try:
            policy = json.loads(POLICY.read_text())
            if policy.get("version") == VERSION:
                policy_updated_at = policy["updated_at"]
                signal, mode, scored, observations = learner_decision(
                    candle, hours, minutes, ask, breakout_snap, policy, higher=higher)
                policy_mode = mode
                for row in observations:
                    name = row.get("strategy", "")
                    strategies[name] = {**row, "selected": bool(signal and name == signal["strategy"])}
                if signal:
                    selected_signal = {**signal, "projected_entry": None, "projected_tp": None,
                                       "projected_stop": None, "projection_error": None}
                    direction = 1 if signal["side"] == "long" else -1
                    entry_price = ask.close if direction == 1 else candle.close
                    stop_raw = signal["stop"]
                    if not condition or condition.get("point_digits") is None:
                        raise ExnessError("Chưa có thông số sàn để tính SL/TP dự kiến")
                    k = signal.get("sl_buffer_k", 0.25)
                    pad = structure_stop_buffer(minutes, multiplier=Decimal(str(k))) if not signal.get("generated") else 0
                    pad = max(Decimal(str(pad)), Decimal(str(ask.close - candle.close))) if not signal.get("generated") else Decimal(0)
                    tick = Decimal(1).scaleb(-condition["point_digits"])
                    if direction * (entry_price - stop_raw) <= 0:
                        raise ExnessError("Invalid structure stop")
                    buffered_stop = Decimal(str(stop_raw)) + (-pad if direction == 1 else pad)
                    buffered_stop = buffered_stop.quantize(tick, rounding=ROUND_FLOOR if direction == 1 else ROUND_CEILING)
                    if buffered_stop <= 0 or (direction == 1 and buffered_stop >= Decimal(str(candle.close))) or (
                            direction == -1 and buffered_stop <= Decimal(str(ask.close))):
                        raise ExnessError("stop_inside_spread")
                    tp, reason = exit_target({**signal, "target_context": tc},
                                             Decimal(str(entry_price)), buffered_stop, tick)
                    selected_signal.update(projected_stop=float(buffered_stop), projected_entry=entry_price,
                                           projected_tp=float(tp), tp_reason=reason)
            else:
                issues.append("Phiên bản policy chưa tương thích")
        except (ExnessError, ValueError, KeyError, OSError) as exc:
            issues.append(str(exc))
            if selected_signal:
                selected_signal["projection_error"] = str(exc)

    return {
        "candle_time": candle.time, "available_at": candle.time + 900, "features": features,
        "status": "stale" if now - (candle.time + 900) >= 900 else "ready",
        "source": "closed_candle_policy_preview", "policy_updated_at": policy_updated_at,
        "issues": issues, "scores": scored,
        "levels": {"m15_donchian": m15_donchian, "m15_ema20": m15_ema20,
                   "h1_ema20": h1_ema20, "h1_ema50": h1_ema50,
                   "barriers": [{"price": b["price"], "side": b["side"], "reason": b["reason"]} for b in barriers]},
        "strategies": strategies, "selected_signal": selected_signal, "policy_mode": policy_mode,
    }


@router.get("/analysis")
def analysis_endpoint():
    result = current_analysis()
    if result is None:
        return {"status": "unavailable", "reason": "Chưa đủ dữ liệu nến M15"}
    return result
