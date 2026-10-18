"""Isolated historical teaching; never advances the forward cursor or paper wallet."""

import copy
import hashlib
import json
import logging
import time
from dataclasses import asdict
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from .db import Session
from .futures_context import load_signal_context
from .futures_learner import (FEE, SLIP, WARMUP, advance, evolve, performance, read_state, save_state,
                              stats, sync_seeds)
from .futures_strategies import exit_policy_enabled, regime, strategy_setup, validate_genome
from .futures_symbols import SYMBOLS, current_symbol, symbol_scope
from .futures_rule_tree import nodes
from .macro_calendar import MACRO_TIMESTAMPS
from .mexc import MarketDataError, MexcFuturesAdapter, validate_candles
from .paper import lock_wallet
from .research import check_cooldown, market_failure, research_lock

router = APIRouter(prefix="/api/workspaces/mexc_futures/learning/history", tags=["Futures Learning"])
LEASE_SECONDS = 1800
logger = logging.getLogger(__name__)


def historical_stats(samples):
    resolved = [sample for sample in samples if sample["status"] == "resolved"]
    result = stats([sample["net_r"] for sample in resolved])
    result["signals"] = len(samples)
    result["excluded"] = len(samples) - len(resolved)
    contexts = {}
    for sample in resolved:
        key = f'{sample["side"]}:{sample["regime"]}'
        bucket = contexts.setdefault(key, {"resolved": 0, "total_r": 0.0})
        bucket["resolved"] += 1
        bucket["total_r"] += sample["net_r"]
    result["contexts"] = contexts
    return result


def replay(candidates, candles, fair, funding, start, end, progress=lambda done, total: None,
           btc_candles=None, benchmarks=None, oi_history=None):
    validate_candles(candles)
    validate_candles(fair)
    expected = list(range(start - WARMUP * 900, end, 900))
    if ([bar.time for bar in candles] != expected
            or [bar.time for bar in fair] != expected or end <= start):
        raise ValueError("Incomplete historical trade/fair-price coverage")
    if btc_candles is not None and [bar.time for bar in btc_candles] != expected:
        raise ValueError("BTC reference candles must cover the same bars")
    if btc_candles is not None:
        validate_candles(btc_candles)
    if benchmarks is not None:
        for name, peer in benchmarks.items():
            validate_candles(peer)
            if [bar.time for bar in peer] != expected:
                raise ValueError(f"Benchmark {name} candles must cover the same bars")
    for candidate in candidates.values():
        validate_genome(candidate["genome"])
    total = (end - start) // 900
    split = start + int(total * 0.7) * 900
    rows = {}
    for name, candidate in candidates.items():
        rows[name] = {"strategy": name, "genome": candidate["genome"], "train": [], "validation": []}
    active = {}
    for index in range(WARMUP, len(candles)):
        bar, mark = candles[index], fair[index]
        known_funding = [record for record in funding if record["time"] <= bar.time]
        if bar.time == split:
            for sample in active.values():
                sample.update(status="excluded", exit_reason="split_boundary")
            active.clear()
        history = candles[index - WARMUP + 1:index + 1]
        btc_history = btc_candles[index - WARMUP + 1:index + 1] if btc_candles else None
        peer_history = {name: peer[index - WARMUP + 1:index + 1] for name, peer in benchmarks.items()} if benchmarks else None
        for name, sample in list(active.items()):
            advance(sample, bar, mark, known_funding, history)
            if sample["status"] != "open":
                active.pop(name)
        context = regime(history)
        partition = "train" if bar.time < split else "validation"
        for name, candidate in candidates.items():
            if name in active:
                continue
            kwargs = {k: v for k, v in (("btc_candles", btc_history),
                                       ("benchmarks", peer_history), ("oi_history", oi_history)) if v}
            setup = strategy_setup(candidate["genome"], history, known_funding, **kwargs)
            if setup is None:
                continue
            sample = {"strategy": name, "candle_time": bar.time, "entry_time": bar.time + 900,
                      "side": setup.side, "stop": setup.stop, "target": setup.target,
                      "regime": context, "status": "open", "entry": None, "net_r": None,
                      "label_available_at": None,
                      "exit_policy": candidate["genome"].get("exit_policy", "fixed")}
            rows[name][partition].append(sample)
            active[name] = sample
        completed = index - WARMUP + 1
        if completed % 96 == 0 or completed == total:
            progress(completed, total)
    for sample in active.values():
        sample.update(status="excluded", exit_reason="dataset_boundary")
    for row in rows.values():
        for partition in ("train", "validation"):
            row[partition] = historical_stats(row[partition])
        row["prior_eligible"] = all(
            row[partition]["resolved"] >= 30 and row[partition]["mean_r"] > 0
            and (row[partition]["no_losses"] or (row[partition]["profit_factor"] or 0) > 1.3)
            for partition in ("train", "validation")
        )
    digest = hashlib.sha256(json.dumps({
        "candles": [asdict(bar) for bar in candles], "fair": [asdict(bar) for bar in fair],
        "funding": funding,
        "benchmarks": {name: [asdict(bar) for bar in peer]
                       for name, peer in (benchmarks or {}).items()},
        "btc_candles": [asdict(bar) for bar in btc_candles or []],
        "oi_history": oi_history or [], "macro_release_times": MACRO_TIMESTAMPS,
    }, sort_keys=True, allow_nan=False).encode()).hexdigest()
    return {"source": "historical", "start_time": start, "end_time": end,
            "train_before": split, "bars": total, "warmup_bars": WARMUP,
            "fee_bps": FEE * 10000, "slippage_bps": SLIP * 10000,
            "dataset_sha256": digest, "strategies": list(rows.values()),
            "signal_inputs": {"benchmarks": sorted(benchmarks or {}),
                              "btc_reference": bool(btc_candles),
                              "oi_observations": len(oi_history or []),
                              "macro_calendar": "official_release_snapshot_2025_2026_not_revision_archive"},
            "eligible_priors": sum(row["prior_eligible"] for row in rows.values())}


def update_job(job_id, **changes):
    with Session.begin() as session:
        lock_wallet(session)
        state = read_state(session, int(time.time()))
        job = state.get("historical", {})
        if job.get("id") != job_id or job.get("status") != "running":
            raise ValueError("Historical job superseded")
        job.update(changes, updated_at=int(time.time()))
        state["historical"] = job
        save_state(session, state)


def evolve_from_history(job_id, children=4):
    """Breed from replay results once; children are new frozen genomes and start forward-only."""
    now = int(time.time())
    with Session.begin() as session:
        lock_wallet(session)
        state = read_state(session, now)
        if state.get("historical", {}).get("id") != job_id:
            return 0
        made = evolve(state, now, max_children=children, force=True)
        state["historical"]["children"] = made
        save_state(session, state)
        return made

def load_history(adapter, start, end, now, fair=False):
    candles = []
    for cursor in range(start, end, 10_000 * 900):
        candles.extend(adapter.history(cursor, min(end, cursor + 10_000 * 900), now, fair=fair))
    return candles

def run_history(job_id, days, candidates, symbol="BTC_USDT"):
    with symbol_scope(symbol):
        _run_history(job_id, days, candidates)

def _run_history(job_id, days, candidates):
    adapter = None
    try:
        check_cooldown()
        adapter = MexcFuturesAdapter()
        now = adapter.server_time()
        end = now // 900 * 900
        start = end - days * 86400
        candles = load_history(adapter, start - WARMUP * 900, end, now)
        update_job(job_id, phase="fair_price")
        fair = load_history(adapter, start - WARMUP * 900, end, now, fair=True)
        funding = adapter.funding_history(start - WARMUP * 900 - 2 * 86400, end)
        with Session() as session:
            signal_context = load_signal_context(session, start - WARMUP * 900, end)
        used = {node.get("feature") for candidate in candidates.values()
                for _, node in nodes(candidate["genome"].get("rule", {}))}
        signal_context.pop("benchmarks", None)
        if "relative_strength" in used:
            peers = SYMBOLS[1:] if current_symbol() == "BTC_USDT" else ("BTC_USDT",)
            signal_context["benchmarks"] = {}
            update_job(job_id, phase="benchmarks")
            for peer in peers:
                peer_adapter = MexcFuturesAdapter(symbol=peer)
                try:
                    signal_context["benchmarks"][peer] = load_history(
                        peer_adapter, start - WARMUP * 900, end, now)
                finally:
                    peer_adapter.close()
        update_job(job_id, phase="replay")
        report = replay(candidates, candles, fair, funding, start, end,
                        lambda done, total: update_job(job_id, completed=done, total=total),
                        **signal_context)
        update_job(job_id, status="completed", phase="done", report=report, error=None)
        evolve_from_history(job_id)
    except Exception as exc:
        logger.exception("Historical Futures learning failed")
        if isinstance(exc, MarketDataError):
            market_failure(exc)
        try:
            update_job(job_id, status="failed", error=str(exc))
        except ValueError:
            pass
    finally:
        if adapter:
            adapter.close()
        research_lock.release()


def job_status(job):
    result = copy.deepcopy(job)
    if result and result["status"] == "running" and time.time() - result["updated_at"] > LEASE_SECONDS:
        result.update(status="failed", error="Phiên học bị gián đoạn; có thể chạy lại.")
    return result


class HistoryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    days: int = Field(default=30, ge=7, le=90, strict=True)


@router.get("")
def get_history():
    with Session() as session:
        return job_status(read_state(session, int(time.time())).get("historical"))


@router.post("", status_code=202)
def start_history(payload: HistoryRequest, background: BackgroundTasks):
    if not research_lock.acquire(blocking=False):
        raise HTTPException(409, "Đang tải hoặc nghiên cứu dữ liệu; thử lại sau.")
    try:
        check_cooldown()
        now = int(time.time())
        with Session.begin() as session:
            lock_wallet(session)
            state = read_state(session, now)
            sync_seeds(state, now)
            previous = job_status(state.get("historical"))
            if previous and previous["status"] == "running":
                raise HTTPException(409, "Một phiên học lịch sử đang chạy.")
            job = {"id": str(uuid4()), "status": "running", "phase": "candles",
                   "days": payload.days, "started_at": now, "updated_at": now,
                   "completed": 0, "total": payload.days * 96, "error": None,
                   "report": (previous or {}).get("report")}
            state["historical"] = job
            candidates = copy.deepcopy({name: candidate for name, candidate in state["candidates"].items()
                                        if exit_policy_enabled(candidate["genome"])})
            save_state(session, state)
        background.add_task(run_history, job["id"], payload.days, candidates, current_symbol())
        return job
    except Exception:
        research_lock.release()
        raise
