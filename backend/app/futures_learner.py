"""Bounded genomes and forward-only teaching labels; independent windows gate paper execution."""

import copy
import math
import random
import time
from collections import defaultdict
from dataclasses import asdict
from statistics import mean
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from .backtest import BacktestConfig, simulate
from .db import (AutoPaperDecision, AutoPaperState, FuturesLearningState, MarketState, PaperTrade,
                 Session)
from .futures_symbols import current_symbol, market_symbol, state_id
from .futures_context import load_signal_context
from .feed import load_candles
from .futures_exits import adaptive_target, partial_size, protective_stop, discretionary_exit, has_target
from .futures_rule_tree import describe_rule, is_htf_rule, nodes, validate_rule
from .futures_strategies import (EXIT_SEEDS, RULE_SEEDS, SEEDS, crossover, exit_policy_enabled, genome_name, genome_fingerprint,
                                 is_rule_tree, mutate, regime, shadow_only, strategy_chart,
                                 strategy_setup, to_rule_tree)
from .mexc import SOURCE, MarketDataError, MexcFuturesAdapter, validate_candles
from .paper import lock_wallet
from .strategy import Setup

router = APIRouter(prefix="/api/workspaces/mexc_futures/learning", tags=["Futures Learning"])
FEE, SLIP = 8 / 10000, 2 / 10000
WARMUP, WINDOW_BARS, CAP = 1600, 2880, 96 + len(EXIT_SEEDS) + len(RULE_SEEDS)
WINDOW_SECONDS = WINDOW_BARS * 900
MAX_HOLD = 192 * 900
VERSION = "futures_htf_h4_consensus_v2"


def add_candidate(state, genome, born, parents=()):
    name = genome_name(genome)
    known = [*state["candidates"].values(), *state.get("rule_archive", {}).values()]
    fingerprint = genome_fingerprint(genome)
    if any(genome_fingerprint(candidate["genome"]) == fingerprint for candidate in known):
        return
    generation = max((state["candidates"][parent]["generation"] for parent in parents), default=-1) + 1
    state["candidates"][name] = {
        "genome": copy.deepcopy(genome), "born_at": born, "parents": list(parents),
        "generation": generation, "windows": [], "promoted": False,
    }


def new_state(now):
    state = {
        "version": VERSION, "enabled": True, "auto_mode": "learner", "started_at": now,
        "cursor": None, "candidates": {}, "samples": [], "funding": [],
        "evolved_at": now, "last_decision": None, "error": None, "updated_at": None,
    }
    born = (now // 900 + 1) * 900
    for seed in [*SEEDS, *RULE_SEEDS]:
        add_candidate(state, seed, born)
    for seed in EXIT_SEEDS:
        add_candidate(state, seed, born)
    rng = random.Random(now)
    for seed in [*SEEDS, *RULE_SEEDS]:
        if len(state["candidates"]) >= CAP - 1:
            break
        add_candidate(state, mutate(seed, rng), born, (genome_name(seed),))
    return state


def read_state(session, now, *, copy_payload=True):
    row = session.get(FuturesLearningState, state_id())
    if row is None:
        raise HTTPException(503, "Run migrations before starting Futures learning")
    if row.payload and row.payload.get("version") not in (VERSION, "futures_htf_v1"):
        state = new_state(now)
        row.payload = state
        return state
    state = (copy.deepcopy(row.payload) if copy_payload else row.payload) if row.payload else new_state(now)
    migrate_to_trees(state)
    if state.get("version") == "futures_htf_v1":
        replacement = new_state(now)
        state.setdefault("rule_archive", {}).update(state["candidates"])
        state.update(version=VERSION, candidates=replacement["candidates"],
                     evolved_at=now, last_decision=None, h4_consensus_started_at=now)
    return state


def save_state(session, state):
    migrate_to_trees(state)
    session.get(FuturesLearningState, state_id()).payload = state


def stats(values):
    gains = sum(max(value, 0) for value in values)
    losses = -sum(min(value, 0) for value in values)
    return {"resolved": len(values), "mean_r": mean(values) if values else None,
            "profit_factor": gains / losses if losses else None,
            "no_losses": bool(gains and not losses)}


def qualification(candidate):
    windows = candidate["windows"]
    gains = sum(window["gains"] for window in windows)
    losses = sum(window["losses"] for window in windows)
    count = sum(window["trade_count"] for window in windows)
    positive = sum(window["net_pnl"] > 0 for window in windows) / len(windows) if windows else 0
    worst_dd = max((window["max_drawdown_percent"] for window in windows), default=0)
    aligned = all(
        window["start_time"] == candidate["born_at"] + index * WINDOW_SECONDS
        and window["end_time"] == window["start_time"] + WINDOW_SECONDS
        for index, window in enumerate(windows)
    )
    checks = {
        "exit_policy_enabled": exit_policy_enabled(candidate["genome"]),
        "not_shadow_only": not shadow_only(candidate["genome"]),
        "forward_windows": bool(windows) and aligned,
        "trades_gte_100": count >= 100,
        "pf_gt_1_3": gains > 1.3 * losses and gains > 0,
        "positive_windows_gte_60pct": positive >= 0.6,
        "worst_dd_lt_10pct": worst_dd < 10,
    }
    return {"eligible": all(checks.values()), "checks": checks, "windows": len(windows),
            "trades": count, "profit_factor": gains / losses if losses else None,
            "no_losses": bool(gains and not losses), "positive_windows": positive,
            "worst_dd": worst_dd}


def advance(sample, bar, fair, funding, candles=None):
    """Per-unit teaching labels; next-open entry, stop-first, fees and settled funding."""
    if sample["status"] != "open" or bar.time < sample["entry_time"]:
        return
    direction = 1 if sample["side"] == "long" else -1
    if sample["entry"] is None:
        if bar.time != sample["entry_time"]:
            sample.update(status="excluded", reason="missing_entry_bar")
            return
        entry = bar.open * (1 + direction * SLIP)
        risk = direction * (entry - sample["stop"])
        if risk <= 0 or direction * (sample["target"] - entry) <= 0:
            sample.update(status="excluded", reason="invalid_entry_geometry")
            return
        sample.update(entry=entry, risk=risk, funding_cost=0.0)
    else:
        sample["funding_cost"] += sample.get("quantity", 1.0) * direction * fair.open * sum(
            row["rate"] for row in funding if row["time"] == bar.time
        )
    stop, target = sample["stop"], sample["target"]
    if direction * (bar.open - stop) <= 0:
        price, why = bar.open, "stop_gap"
    elif (bar.low <= stop if direction == 1 else bar.high >= stop):
        price, why = stop, "stop"
    elif has_target(sample) and (bar.high >= target if direction == 1 else bar.low <= target):
        price, why = target, "target"
    elif bar.time - sample["entry_time"] >= MAX_HOLD:
        price, why = bar.close, "timeout"
    else:
        history = candles or [bar]
        why = discretionary_exit(sample, history)
        if why:
            price = bar.close
            return finish(sample, direction, price, why, bar)
        sample.setdefault("original_quantity", sample.get("quantity", 1.0))
        quantity = partial_size(sample, history)
        if quantity:
            fill = bar.close * (1 - direction * SLIP)
            sample["partial_exits"] = [*(sample.get("partial_exits") or []),
                                       dict(time=bar.time + 900, price=fill, quantity=quantity)]
            sample["realized_gross"] = sample.get("realized_gross", 0) + direction * (fill - sample["entry"]) * quantity
            sample["realized_exit_fee"] = sample.get("realized_exit_fee", 0) + fill * quantity * FEE
            sample["quantity"] = sample.get("quantity", 1.0) - quantity
        sample["target"] = adaptive_target(sample, history)
        sample["stop"] = protective_stop(sample, candles or [bar], FEE, SLIP)
        return
    finish(sample, direction, price, why, bar)

def finish(sample, direction, price, why, bar):
    price *= 1 - direction * SLIP
    quantity = sample.get("quantity", 1.0)
    net = (sample.get("realized_gross", 0) + direction * (price - sample["entry"]) * quantity
           - sample["entry"] * FEE - price * quantity * FEE
           - sample.get("realized_exit_fee", 0) - sample["funding_cost"])
    sample.update(status="resolved", exit_reason=why, net_r=net / sample["risk"],
                  label_available_at=bar.time + 900)


def score(samples, name, side, context, cutoff, historical=None):
    closed = [sample for sample in samples if sample["status"] == "resolved"
              and sample["label_available_at"] <= cutoff]
    values = [sample["net_r"] for sample in closed if sample["strategy"] == name
              and sample["side"] == side and sample["regime"] == context]
    count, total = len(values), sum(values)
    prior_count = 0
    report = (historical or {}).get("report")
    if report and report["end_time"] <= cutoff:
        prior = next((row for row in report["strategies"]
                      if row["strategy"] == name and row["prior_eligible"]), None)
        bucket = prior["train"]["contexts"].get(f"{side}:{context}") if prior else None
        if bucket and bucket["resolved"]:
            prior_count = min(bucket["resolved"], 30)
            total += bucket["total_r"] / bucket["resolved"] * prior_count
            count += prior_count
    if not count:
        return math.inf
    return total / count + math.sqrt(2 * math.log(len(closed) + prior_count + 1) / count)


EVOLVE_SECONDS, MIN_PARENT_SAMPLES = 3600, 10

def migrate_to_trees(state):
    """Convert genomes, preserving IDs referenced by trades, samples and replay reports."""
    if state.get("auto_mode") == "legacy":
        state.update(auto_mode="learner", last_decision=None)
    for collection in (state["candidates"], state.get("rule_archive", {})):
        for candidate in collection.values():
            if not is_rule_tree(candidate["genome"]):
                candidate["genome"] = to_rule_tree(candidate["genome"])
    report = (state.get("historical") or {}).get("report")
    for row in (report or {}).get("strategies", []):
        if not is_rule_tree(row["genome"]):
            row["genome"] = to_rule_tree(row["genome"])

def sync_seeds(state, now):
    """Add seed families introduced after this state was created, each with one mutated child."""
    migrate_to_trees(state)
    born, rng = (now // 900 + 1) * 900, random.Random(now)
    existing = {genome_name(candidate["genome"]) for candidate in state["candidates"].values()}
    for seed in EXIT_SEEDS:
        if genome_name(seed) not in existing and len(state["candidates"]) < CAP:
            add_candidate(state, seed, born)
    for seed in [*SEEDS, *RULE_SEEDS]:
        name = genome_name(seed)
        if name not in existing and len(state["candidates"]) < CAP:
            add_candidate(state, seed, born)
            if len(state["candidates"]) < CAP:
                add_candidate(state, mutate(seed, rng), born, (name,))

def performance(state, now):
    """Resolved forward samples plus historical replay (train + validation) per strategy."""
    totals = defaultdict(lambda: [0, 0.0])
    for sample in state["samples"]:
        if sample["status"] == "resolved" and sample["label_available_at"] <= now:
            totals[sample["strategy"]][0] += 1
            totals[sample["strategy"]][1] += sample["net_r"]
    report = (state.get("historical") or {}).get("report")
    if report and report["end_time"] <= now:
        for row in report["strategies"]:
            for part in ("train", "validation"):
                for bucket in row[part]["contexts"].values():
                    totals[row["strategy"]][0] += bucket["resolved"]
                    totals[row["strategy"]][1] += bucket["total_r"]
    return {name: (count, total / count) for name, (count, total) in totals.items() if count}

def complexity(genome):
    return validate_rule(genome["rule"]) if is_rule_tree(genome) else 0

def evolve(state, now, max_children=1, force=False):
    """Frozen children from the best parents; at the cap the weakest unpromoted child is retired."""
    if not force and now - state["evolved_at"] < EVOLVE_SECONDS:
        return 0
    state["evolved_at"] = now
    results = performance(state, now)
    archive = state.get("rule_archive", {})
    ranked = sorted((name for name, (count, r) in results.items()
                     if count >= MIN_PARENT_SAMPLES and r > 0 and name in state["candidates"]
                     and exit_policy_enabled(state["candidates"][name]["genome"])),
                    key=lambda name: results[name][1] - 0.01 * complexity(
                        state["candidates"][name]["genome"]), reverse=True)
    parents = ranked[:4]
    if not parents:
        return 0
    rng, made = random.Random(now // EVOLVE_SECONDS), 0
    for _ in range(max_children):
        if sum(exit_policy_enabled(candidate["genome"])
               for candidate in state["candidates"].values()) >= CAP:
            weak = [name for name, candidate in state["candidates"].items()
                    if candidate["generation"] > 0 and not candidate["promoted"]
                    and exit_policy_enabled(candidate["genome"])
                    and not any(sample["strategy"] == name and sample["status"] == "open"
                                for sample in state["samples"])
                    and name in results and name not in parents
                    and results[name][1] < results[parents[0]][1]]
            if not weak:
                break
            retired = min(weak, key=lambda name: results[name][1])
            archive = state.setdefault("rule_archive", {})
            archive[retired] = state["candidates"].pop(retired)
        for _ in range(40):
            lineage = tuple(rng.sample(parents, 2)) if len(parents) > 1 else (parents[0],)
            base = state["candidates"][lineage[0]]["genome"]
            if len(lineage) == 2:
                base = crossover(base, state["candidates"][lineage[1]]["genome"])
            genome = mutate(base, rng)
            known = {genome_fingerprint(candidate["genome"])
                     for candidate in [*state["candidates"].values(), *archive.values()]}
            if genome_fingerprint(genome) not in known and genome_name(genome) not in results:
                add_candidate(state, genome, (now // 900 + 1) * 900,
                              lineage)
                made += 1
                break
    return made

def step(state, candles, fair, now, btc_candles=None, oi_history=None, benchmarks=None):
    bar = candles[-1]
    if state["cursor"] is not None and bar.time <= state["cursor"]:
        return
    if state["cursor"] is not None and bar.time != state["cursor"] + 900:
        raise ValueError("Futures learning candle gap")
    for sample in state["samples"]:
        advance(sample, bar, fair, state["funding"], candles)
    context, found, observations = regime(candles), [], []
    busy = {sample["strategy"] for sample in state["samples"] if sample["status"] == "open"}
    for name, candidate in state["candidates"].items():
        observation = {"strategy": name, "status": "warming_up", "side": None,
                       "setup": None, "levels": []}
        observations.append(observation)
        if not exit_policy_enabled(candidate["genome"]):
            observation.update(status="paused", note="Đã ngừng thử chính sách thoát này; giữ lịch sử.")
            continue
        if bar.time < candidate["born_at"]:
            continue
        kwargs = {k: v for k, v in (("btc_candles", btc_candles), ("oi_history", oi_history), ("benchmarks", benchmarks)) if v}
        if any(node.get("feature") == "basis_aligned" for _, node in nodes(candidate["genome"]["rule"])):
            kwargs["fair_candles"] = [fair]
        setup = strategy_setup(candidate["genome"], candles, state["funding"], **kwargs)
        observation.update(strategy_chart(candidate["genome"], candles, {
            **kwargs, "funding": state["funding"],
            "funding_threshold": candidate["genome"]["funding_threshold"],
            "max_er": candidate["genome"]["max_er"],
        }))
        observation["status"] = "no_signal"
        if setup is None:
            continue
        ucb = score(state["samples"], name, setup.side, context, bar.time + 900,
                    state.get("historical"))
        if not shadow_only(candidate["genome"]):
            found.append({"name": name, "setup": setup, "ucb": ucb,
                          "promoted": candidate["promoted"] and qualification(candidate)["eligible"]})
        observation.update(status="shadow" if shadow_only(candidate["genome"]) else "signal",
                           side=setup.side, setup=asdict(setup))
        if name not in busy:
            state["samples"].append({
                "strategy": name, "candle_time": bar.time, "entry_time": bar.time + 900,
                "side": setup.side, "stop": setup.stop, "target": setup.target,
                "regime": context, "status": "open", "entry": None, "net_r": None,
                "label_available_at": None,
                "exit_policy": candidate["genome"].get("exit_policy", "fixed"),
                "exit_params": candidate["genome"].get("exit_params"),
                "exit_rule": candidate["genome"].get("rule"),
            })
    eligible = [candidate for candidate in found if candidate["promoted"]]
    pool = found if state["auto_mode"] == "paper_learning" else eligible or found
    best = max((candidate["ucb"] for candidate in pool), default=None)
    tied = sorted((candidate for candidate in pool if candidate["ucb"] == best),
                  key=lambda candidate: candidate["name"])
    pick = random.Random(bar.time).choice(tied) if tied else None
    selection_reason = ("explore_unseen_context" if pick and math.isinf(pick["ucb"])
                        else "contextual_ucb" if pick else "no_signal")
    state["last_decision"] = {
        "time": bar.time, "regime": context, "candidates": len(found),
        "mode": ("paper_learning" if state["auto_mode"] == "paper_learning" and found
                 else "qualified" if eligible else "shadow" if found else "abstain"),
        "auto_mode": state["auto_mode"],
        "selection_reason": selection_reason,
        "selected": pick["name"] if pick else None,
        "setup": asdict(pick["setup"]) if pick else None,
        "strategies": observations,
    }
    state["cursor"], state["updated_at"] = bar.time, now


def validate_window(candidate, candles, fair, funding, contract, signal_context=None):
    start = candidate["born_at"] + len(candidate["windows"]) * WINDOW_SECONDS
    end = start + WINDOW_SECONDS
    if (len(candles) != WARMUP + WINDOW_BARS or candles[WARMUP].time != start
            or candles[-1].time + 900 != end):
        raise ValueError("Incomplete forward validation window")
    validate_candles(candles)
    validate_candles(fair)
    if [bar.time for bar in candles] != [bar.time for bar in fair]:
        raise ValueError("Fair-price coverage mismatch")
    fair_by_time = {bar.time: bar for bar in fair}
    result = simulate(
        candles, BacktestConfig(), funding, fair, contract, warmup=WARMUP, window=WARMUP,
        signal=lambda history, allow_short=False: strategy_setup(
            candidate["genome"], history, funding,
            **({**(signal_context or {}), "fair_candles": [fair_by_time[history[-1].time]]}
               if any(node.get("feature") == "basis_aligned"
                      for _, node in nodes(candidate["genome"]["rule"])) else (signal_context or {}))),
        exit_policy=candidate["genome"].get("exit_policy", "fixed"),
        exit_params=candidate["genome"].get("exit_params"),
        exit_rule=candidate["genome"].get("rule"),
    )
    candidate["windows"].append({
        **{key: result[key] for key in ("start_time", "end_time", "trade_count", "net_pnl", "max_drawdown_percent")},
        "gains": sum(max(trade["net_pnl"], 0) for trade in result["trades"]),
        "losses": -sum(min(trade["net_pnl"], 0) for trade in result["trades"]),
    })
    if not qualification(candidate)["eligible"]:
        candidate["promoted"] = False


def learning_tick(now=None, adapter=None):
    now = int(time.time()) if now is None else int(now)
    end = now // 900 * 900
    with Session() as session:
        state = read_state(session, now)
        market = session.get(MarketState, state_id())
        if not state["enabled"] or not market or market.source != SOURCE:
            return
        cursor = state["cursor"]
        if cursor is not None and cursor >= end - 900:
            return
        candles = load_candles(session, limit=WARMUP + 96, until=end)
        if not candles or candles[-1].time != end - 900:
            return
        signal_context = load_signal_context(session, candles[0].time, end)
        benchmarks = signal_context.get("benchmarks")
        oi_history = signal_context.get("oi_history")
        due = next(((name, candidate) for name, candidate in state["candidates"].items()
                    if exit_policy_enabled(candidate["genome"])
                    and candidate["born_at"] + (len(candidate["windows"]) + 1) * WINDOW_SECONDS <= end), None)
        validation = None
        if due:
            name, candidate = due
            start = candidate["born_at"] + len(candidate["windows"]) * WINDOW_SECONDS
            validation = (name, start, load_candles(session, since=start - WARMUP * 900, until=start + WINDOW_SECONDS))
            validation_context = load_signal_context(session, start - WARMUP * 900,
                                                     start + WINDOW_SECONDS)
    owns_adapter = adapter is None
    adapter = adapter or MexcFuturesAdapter()
    try:
        validate_candles(candles)
        first = cursor + 900 if cursor is not None else end - 900
        if first < candles[0].time:
            raise ValueError("Learning backlog exceeds cached history; restore missing candles")
        bars = [bar for bar in candles if bar.time >= first][:96]
        stop = bars[-1].time + 900
        fair = adapter.history(first, stop, now, fair=True)
        if [bar.time for bar in fair] != [bar.time for bar in bars]:
            raise ValueError("Missing forward fair-price candles")
        funding = adapter.funding_history(first - 2 * 86400, stop)
        if validation:
            name, start, history = validation
            if len(history) != WARMUP + WINDOW_BARS:
                history = adapter.history(start - WARMUP * 900, start + WINDOW_SECONDS, now)
                validation = (name, start, history)
            validation_fair = adapter.history(history[0].time, start + WINDOW_SECONDS, now, fair=True)
            validation_funding = adapter.funding_history(history[0].time, start + WINDOW_SECONDS)
            contract = adapter.contract_info()
        with Session.begin() as session:
            lock_wallet(session)
            current = read_state(session, now)
            if not current["enabled"] or current["cursor"] != cursor:
                return
            current["funding"] = funding
            if current["cursor"] is None:
                current["cursor"] = first - 900
            for bar, mark in zip(bars, fair):
                history = [past for past in candles if past.time <= bar.time][-WARMUP:]
                bench_hist = {name: [p for p in peer if p.time <= bar.time][-WARMUP:] for name, peer in benchmarks.items()} if benchmarks else None
                step(current, history, mark, now, oi_history=oi_history, benchmarks=bench_hist)
            if validation and current["candidates"][name]["windows"] == state["candidates"][name]["windows"]:
                validate_window(current["candidates"][name], validation[2], validation_fair,
                                validation_funding, contract, validation_context)
            sync_seeds(current, now)
            evolve(current, now)
            current["error"] = None
            save_state(session, current)
    except (MarketDataError, ValueError) as exc:
        with Session.begin() as session:
            lock_wallet(session)
            current = read_state(session, now)
            current["error"] = str(exc)
            save_state(session, current)
    finally:
        if owns_adapter:
            adapter.close()


def select_for_auto(session, candle_time, now):
    state = read_state(session, int(now))
    decision = state["last_decision"]
    if not state["enabled"] or state["error"] or not decision or decision["time"] != candle_time:
        raise HTTPException(409, "Đợi learner xử lý nến mới; không dùng breakout dự phòng")
    if not decision["setup"]:
        return True, None, None
    candidate = state["candidates"].get(decision["selected"])
    if candidate and (not exit_policy_enabled(candidate["genome"]) or shadow_only(candidate["genome"])):
        return True, None, None
    if state["auto_mode"] == "paper_learning":
        if decision.get("auto_mode") != "paper_learning" or decision["mode"] != "paper_learning":
            raise HTTPException(409, "Chờ quyết định mới của chế độ học thử Paper")
        if not candidate or candidate["born_at"] > candle_time:
            raise HTTPException(409, "Chiến thuật học thử chưa sẵn sàng")
        return True, Setup(**decision["setup"]), decision["selected"]
    if decision["mode"] != "qualified":
        return True, None, None
    if not candidate or not candidate["promoted"] or not qualification(candidate)["eligible"]:
        raise HTTPException(409, "Chiến thuật shadow chưa đủ điều kiện Auto Paper")
    return True, Setup(**decision["setup"]), decision["selected"]


def paper_by_strategy(session):
    """Auto Paper trades of the current pair, keyed by the strategy that opened them."""
    rows = session.execute(
        select(PaperTrade.id, PaperTrade.side, PaperTrade.entry, PaperTrade.quantity,
               PaperTrade.exit_time, PaperTrade.net_pnl, AutoPaperDecision.parameters)
        .join(AutoPaperDecision, AutoPaperDecision.trade_id == PaperTrade.id)
        .where(PaperTrade.symbol == current_symbol(),
               AutoPaperDecision.symbol == PaperTrade.symbol)
        .order_by(PaperTrade.id)
    ).all()
    result = {}
    for trade_id, side, entry, quantity, exit_time, net_pnl, parameters in rows:
        name = ((parameters or {}).get("audit") or {}).get("strategy")
        if not name:
            continue
        stats_row = result.setdefault(name, {"open": [], "closed": 0, "wins": 0, "net_pnl": 0.0,
                                             "last_trade_id": None})
        stats_row["last_trade_id"] = trade_id
        if exit_time is None:
            stats_row["open"].append(
                {"id": trade_id, "side": side, "entry": entry, "quantity": quantity})
        else:
            stats_row["closed"] += 1
            stats_row["wins"] += (net_pnl or 0) > 0
            stats_row["net_pnl"] += net_pnl or 0
    return result


def genes(genome):
    """Comparable, human-readable genes: HTF slots keep their own frame description."""
    rule = genome.get("rule")
    out = {}
    if rule and is_htf_rule(rule):
        out.update(zip(("H4", "H1", "M15"), map(describe_rule, rule["children"])))
    elif rule:
        out["rule"] = describe_rule(rule)
    for key in ("lookback", "target_r", "stop_buffer", "max_er", "funding_threshold"):
        if key in genome:
            out[key] = genome[key]
    out["exit_policy"] = genome.get("exit_policy", "fixed")
    out["exit_params"] = genome.get("exit_params")
    return out


def lineage(state, candidate):
    """What a child changed: genes taken from the second parent and the single mutation."""
    known = {**state.get("rule_archive", {}), **state["candidates"]}
    parents = [known.get(name) for name in candidate["parents"]]
    if not parents or None in parents:
        return None
    first = parents[0]["genome"]
    base = first
    if len(parents) == 2:
        try:
            base = crossover(first, parents[1]["genome"])
        except ValueError:
            return None
    child, before = genes(candidate["genome"]), genes(base)
    a = genes(first)
    b = genes(parents[1]["genome"]) if len(parents) == 2 else a
    return {
        "kind": "crossover" if len(parents) == 2 else "mutation",
        "from_mate": [{"gene": key, "value": child[key]} for key in child
                      if len(parents) == 2 and a.get(key) != b.get(key)
                      and child[key] == b.get(key) and child[key] == before.get(key)],
        "mutated": [{"gene": key, "before": before.get(key), "after": child[key]}
                    for key in child if child[key] != before.get(key)],
    }


def summary(state, now, paper=None):
    paper = paper or {}
    rows, results = [], performance(state, now)
    by_strategy = defaultdict(list)
    for sample in state["samples"]:
        by_strategy[sample["strategy"]].append(sample)
    report = (state.get("historical") or {}).get("report")
    replay = defaultdict(int)
    if report and report["end_time"] <= now:
        for row in report["strategies"]:
            for part in ("train", "validation"):
                replay[row["strategy"]] += sum(
                    bucket["resolved"] for bucket in row[part]["contexts"].values())
    for name, candidate in state["candidates"].items():
        samples = by_strategy.get(name, [])
        values = [sample["net_r"] for sample in samples if sample["status"] == "resolved"
                  and sample["label_available_at"] <= now]
        count, average = results.get(name, (0, None))
        trades = paper.get(name, {})
        rows.append({"strategy": name, **candidate, **stats(values), "signals": len(samples),
                     "paused": not exit_policy_enabled(candidate["genome"]),
                     "shadow_only": shadow_only(candidate["genome"]),
                     "rule_text": (describe_rule(candidate["genome"]["rule"])
                                   if is_rule_tree(candidate["genome"]) else None),
                     "complexity": complexity(candidate["genome"]),
                     "open_trades": trades.get("open", []),
                     "paper": {key: trades.get(key, default) for key, default in (
                         ("closed", 0), ("wins", 0), ("net_pnl", 0.0), ("last_trade_id", None))},
                     "origin": "seed" if not candidate["parents"] else "child",
                     "lineage": lineage(state, candidate) if candidate["parents"] else None,
                     "learned_samples": count, "learned_mean_r": average,
                     "replay_samples": replay[name],
                     "forward_samples": len(values),
                     "forward_open": sum(sample["status"] == "open" for sample in samples),
                     "qualification": qualification(candidate)})
    archive = state.get("rule_archive", {})
    retired = [{"strategy": name, "genome": archive[name]["genome"] if name in archive else None,
                "closed": trades["closed"], "wins": trades["wins"], "net_pnl": trades["net_pnl"],
                "open": len(trades["open"]), "last_trade_id": trades["last_trade_id"]}
               for name, trades in paper.items() if name not in state["candidates"]]
    return {"enabled": state["enabled"], "auto_mode": state["auto_mode"],
            "cursor": state["cursor"], "updated_at": state["updated_at"],
            "stale": state["cursor"] is None or now - (state["cursor"] + 1800) > 120,
            "error": state["error"], "population": len(rows), "samples": len(state["samples"]),
            "rule_trials": sum(is_rule_tree(candidate["genome"]) for candidate in state["candidates"].values())
                           + len(state.get("rule_archive", {})),
            "promoted": sum(candidate["promoted"] and exit_policy_enabled(candidate["genome"])
                            and not shadow_only(candidate["genome"])
                            for candidate in state["candidates"].values()),
            "last_decision": state["last_decision"], "strategies": rows,
            "retired_paper": retired}


@router.get("")
def get_learning():
    now = int(time.time())
    with Session() as session:
        return summary(read_state(session, now), now, paper_by_strategy(session))


class LearningConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool
    auto_mode: Literal["learner", "paper_learning"]


@router.post("/config")
def configure_learning(payload: LearningConfig):
    now = int(time.time())
    with Session.begin() as session:
        lock_wallet(session)
        if session.get(AutoPaperState, state_id()).enabled:
            raise HTTPException(409, "Dừng Auto Paper trước khi đổi bộ chọn chiến thuật")
        state = read_state(session, now)
        if state["enabled"] != payload.enabled:
            state["cursor"] = None
            for sample in state["samples"]:
                if sample["status"] == "open":
                    sample.update(status="excluded", reason="learning_paused")
        state.update(enabled=payload.enabled, auto_mode=payload.auto_mode, last_decision=None)
        save_state(session, state)
        return summary(state, now, paper_by_strategy(session))


class Promotion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    strategy: str
    promoted: bool


@router.post("/promotion")
def promote(payload: Promotion):
    now = int(time.time())
    with Session.begin() as session:
        lock_wallet(session)
        state = read_state(session, now)
        candidate = state["candidates"].get(payload.strategy)
        if not candidate:
            raise HTTPException(404, "Unknown Futures strategy")
        if payload.promoted and not qualification(candidate)["eligible"]:
            raise HTTPException(409, "Chưa đạt forward: 100 lệnh, PF > 1.3, 60% cửa sổ dương, DD < 10%")
        candidate["promoted"] = payload.promoted
        state["last_decision"] = None
        save_state(session, state)
        return summary(state, now, paper_by_strategy(session))

