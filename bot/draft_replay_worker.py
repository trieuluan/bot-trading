"""Dedicated single-job screening worker; never blocks stop execution or REST polling."""

import json
import logging
import multiprocessing
import os
import signal
import time
from threading import Event
from uuid import uuid4

from sqlalchemy import select, text

from backend.app.db import FuturesLearningState, Session
from backend.app.feed import load_candles
from backend.app.futures_draft_replay import (
    MissingInputError,
    ReplayDataError,
    run_screen,
    run_universe,
)
from backend.app.futures_drafts import (
    BACKOFF_SECONDS,
    MAX_ATTEMPTS,
    RETRY_SECONDS,
    TIMEOUT_SECONDS,
    UNIVERSE_DAYS,
    UNIVERSE_SCOPE,
    UNIVERSE_TIMEOUT_SECONDS,
    claim,
    complete,
    ready,
)
from backend.app.futures_learner import (
    active_strategies,
    admit_drafts,
    evolve,
    read_state,
    save_state,
)
from backend.app.futures_native import NATIVE_WARMUP
from backend.app.futures_population_audit import attach, engine_digest, freeze, run_audit
from backend.app.futures_research_lock import acquire, hold_worker
from backend.app.futures_strategies import NATIVE_SEEDS, genome_fingerprint, genome_name
from backend.app.futures_symbols import SYMBOLS, symbol_scope
from backend.app.paper import lock_wallet

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("draft_replay_worker")
stop = Event()


def touch(session, now, symbol=None, draft_id=None, **extra):
    worker = session.scalar(select(FuturesLearningState.payload["draft_worker"]).where(
        FuturesLearningState.id == 1)) or {}
    worker.update(heartbeat_at=now, symbol=symbol, draft_id=draft_id, **extra)
    if session.bind.dialect.name == "postgresql":
        session.execute(text("UPDATE futures_learning_state SET payload = "
                             "jsonb_set(payload::jsonb, '{draft_worker}', CAST(:worker AS jsonb), true)::json "
                             "WHERE id = 1"), {"worker": json.dumps(worker)})
        session.expire_all()
    else:
        lock_wallet(session)
        with symbol_scope(SYMBOLS[0]):
            state = read_state(session, now)
            state["draft_worker"] = worker
            save_state(session, state)


def next_draft(now):
    choices = []
    for symbol in SYMBOLS:
        with symbol_scope(symbol), Session() as session:
            payload = session.execute(select(FuturesLearningState.payload["enabled"],
                FuturesLearningState.payload["drafts"], FuturesLearningState.payload["draft_last_started_at"])
                .where(FuturesLearningState.id == SYMBOLS.index(symbol) + 1)).one()
            enabled, drafts, last_started = payload
            if not enabled:
                continue
            for draft in sorted((drafts or {}).values(), key=lambda r: (r["created_at"], r["id"])):
                if ready(draft, now):
                    choices.append((draft["status"] != "running",
                                    last_started or 0,
                                    draft["created_at"], symbol, draft["id"]))
                    break
    return min(choices)[-2:] if choices else (None, None)


def admit_waiting(now):
    for symbol in SYMBOLS:
        with Session() as session:
            drafts = session.scalar(select(FuturesLearningState.payload["drafts"]).where(
                FuturesLearningState.id == SYMBOLS.index(symbol) + 1)) or {}
            if not any(draft["status"] == "waiting_slot" for draft in drafts.values()):
                continue
        with symbol_scope(symbol), Session.begin() as session:
            lock_wallet(session)
            state = read_state(session, now)
            if state.get("enabled"):
                admit_drafts(state, now, active_strategies(session, state))
                save_state(session, state)


def update_progress(symbol, draft_id, token, **changes):
    """Short updates reload state under the same wallet lock as all learner writers."""
    now = int(time.time())
    with symbol_scope(symbol), Session.begin() as session:
        lock_wallet(session)
        state = read_state(session, now)
        draft = state.get("drafts", {}).get(draft_id)
        if draft and draft["status"] == "running" and draft.get("run_token") == token:
            draft.update(changes, updated_at=now)
            save_state(session, state)
            touch(session, now, symbol, draft_id, phase=draft["phase"])


def retryable(exc):
    """Network/data-coverage faults retry; invalid genomes and absent real inputs do not."""
    if isinstance(exc, MissingInputError):
        return False
    return isinstance(exc, ReplayDataError) or not isinstance(exc, (ValueError, TypeError, KeyError))


def compute(send, symbol, frozen):
    try:
        def emit(**changes):
            send.send(("progress", changes))
        report = (run_audit(symbol, frozen, emit) if frozen.get('purpose') == 'population_audit' else
                  run_universe(frozen["candidates"], frozen["window"], emit)
                  if frozen.get("purpose") == "native_parents" else run_screen(symbol, frozen, emit))
        send.send(("result", report))
    except Exception as exc:
        logger.exception("Replay calculation failed")
        send.send(("error", {"error": f"{type(exc).__name__}: {exc}",
                              "retryable": retryable(exc)}))
    finally:
        send.close()


def supervise(lease, symbol, frozen, emit):
    universe = frozen["window"].get("scope") == UNIVERSE_SCOPE
    timeout = UNIVERSE_TIMEOUT_SECONDS if universe else TIMEOUT_SECONDS
    context = multiprocessing.get_context("spawn")
    receive, send = context.Pipe(duplex=False)
    child = context.Process(target=compute, args=(send, symbol, frozen))
    started = last_heartbeat = time.monotonic()
    latest = {}
    child.start()
    send.close()
    try:
        while True:
            if stop.is_set():
                raise InterruptedError("Replay worker stopping")
            if time.monotonic() - started >= timeout:
                return None, f"Replay exceeded {int(timeout // 60)} minutes", True
            lease.check()
            if receive.poll(1):
                try:
                    kind, payload = receive.recv()
                except EOFError:
                    return None, "Replay process exited without a result", True
                if kind == "result":
                    return payload, None, True
                if kind == "error":
                    return None, payload["error"], payload["retryable"]
                latest = payload
            elif not child.is_alive():
                return None, f"Replay process exited ({child.exitcode})", True
            if time.monotonic() - last_heartbeat >= 15:
                emit(**latest)
                last_heartbeat = time.monotonic()
    finally:
        if child.is_alive():
            child.terminate()
        child.join(timeout=5)
        if child.is_alive():
            child.kill()
            child.join()
        receive.close()

def parent_progress(token, **changes):
    now = int(time.time())
    with symbol_scope(SYMBOLS[0]), Session.begin() as session:
        lock_wallet(session)
        state = read_state(session, now)
        job = state.get("universe_parent_job") or {}
        if job.get("token") == token and job.get("status") == "running":
            job.update(changes, updated_at=now)
            save_state(session, state)
            touch(session, now, phase=f"parents:{job.get('phase', 'data')}")

def refresh_native_parents(lease, now):
    with symbol_scope(SYMBOLS[0]), Session.begin() as session:
        lock_wallet(session)
        state = read_state(session, now)
        job = state.get("universe_parent_job") or {}
        if now < job.get("next_retry_at", 0):
            return False
        enabled = session.scalars(select(FuturesLearningState.payload["enabled"])).all()
        if not any(enabled):
            return False
        previous = "data_start" in job.get("window", {}) and (
            job.get("status") == "running"
            or (job.get("attempts", 0) < MAX_ATTEMPTS and job.get("status") == "failed"))
        end = now // 86400 * 86400
        data_start = end - UNIVERSE_DAYS * 86400
        window = job["window"] if previous else {
            "start": data_start + NATIVE_WARMUP * 900, "end": end,
            "warmup_bars": NATIVE_WARMUP, "scope": UNIVERSE_SCOPE,
            "symbols": list(SYMBOLS), "data_start": data_start}
        token = str(uuid4())
        candidates = (job["candidates"] if previous else
                      {genome_name(genome): {"genome": genome} for genome in NATIVE_SEEDS})
        job = {"token": token, "status": "running", "phase": "data", "window": window,
               "candidates": candidates, "attempts": job["attempts"] + 1 if previous else 1,
               "started_at": now, "updated_at": now, "next_retry_at": 0}
        state["universe_parent_job"] = job
        save_state(session, state)
        touch(session, now, phase="parents:data")
    frozen = {"purpose": "native_parents", "window": window, "candidates": candidates}
    report, error, can_retry = supervise(lease, SYMBOLS[0], frozen,
                                        lambda **changes: parent_progress(token, **changes))
    lease.check()
    finished = int(time.time())
    if error is None and (
        not isinstance(report, dict)
        or report.get("scope") != UNIVERSE_SCOPE
        or report.get("symbols") != list(SYMBOLS)
        or set(report.get("per_symbol", {})) != set(SYMBOLS)
        or report.get("start_time") != window["start"]
        or report.get("end_time") != window["end"]
        or {row["strategy"]: genome_fingerprint(row["genome"])
            for row in report.get("strategies", [])}
        != {name: genome_fingerprint(row["genome"]) for name, row in candidates.items()}
    ):
        error, can_retry = "Invalid frozen native parent universe report", False
    if error is None:
        for symbol in SYMBOLS:
            with symbol_scope(symbol), Session.begin() as session:
                lock_wallet(session)
                state = read_state(session, finished)
                state["breeding_history"] = {"report": report, "updated_at": finished}
                evolve(state, finished, max_children=3, force=True)
                save_state(session, state)
    with symbol_scope(SYMBOLS[0]), Session.begin() as session:
        lock_wallet(session)
        state = read_state(session, finished)
        job = state["universe_parent_job"]
        retry = error is not None and can_retry and job["attempts"] < MAX_ATTEMPTS
        job.update(status="failed" if error else "completed", error=error, phase="done",
                   updated_at=finished, next_retry_at=finished + (BACKOFF_SECONDS if retry else RETRY_SECONDS))
        save_state(session, state)
        touch(session, finished, phase="idle")
    return True


def process(lease, symbol, draft_id):
    now = int(time.time())
    with symbol_scope(symbol), Session.begin() as session:
        lock_wallet(session)
        state = read_state(session, now)
        draft = state.get("drafts", {}).get(draft_id)
        if not state.get("enabled") or not draft or not ready(draft, now):
            return
        token = claim(draft, now)
        if not token:
            save_state(session, state)
            return
        frozen = state["drafts"][draft_id]
        state["draft_last_started_at"] = now
        save_state(session, state)
        touch(session, now, symbol, draft_id, phase=frozen["phase"])
    report, error, retryable = supervise(lease, symbol, frozen,
        lambda **changes: update_progress(symbol, draft_id, token, **changes))
    lease.check()
    now = int(time.time())
    with symbol_scope(symbol), Session.begin() as session:
        lock_wallet(session)
        state = read_state(session, now)
        draft = state.get("drafts", {}).get(draft_id)
        if draft:
            changed = complete(draft, token, now, report=report, error=error, retryable=retryable)
            if changed and draft["status"] == "waiting_slot" and state.get("enabled"):
                admit_drafts(state, now, active_strategies(session, state))
            save_state(session, state)
            touch(session, now, phase="idle")

def audit_population(lease, now):
    digest = engine_digest()
    for symbol in SYMBOLS:
        with symbol_scope(symbol), Session.begin() as session:
            lock_wallet(session)
            state = read_state(session, now)
            if not state.get('enabled'):
                continue
            job = state.get('population_audit_job') or {}
            if job.get('status') == 'failed' and now - job.get('updated_at', 0) < 86400:
                continue
            frozen = freeze(state, now, load_candles(session, limit=100000))
            pending = {name: row for name, row in frozen['candidates'].items()
                       if not (state.get('population_audit', {}).get(name, {}).get('engine') == digest
                               and state['population_audit'][name].get('fingerprint') == row['fingerprint']
                               and state['population_audit'][name].get('born_at') == row['born_at']
                               and now - state['population_audit'][name].get('cutoff', 0) < 86400)}
            if not pending:
                continue
            name = min(pending, key=lambda key: (state.get('population_audit', {}).get(key, {}).get('cutoff', 0), key))
            frozen.update(purpose='population_audit', candidates={name: pending[name]})
            state['population_audit_job'] = {'status': 'running', 'strategy': name,
                                            'snapshot': frozen, 'updated_at': now}
            save_state(session, state)
        report, error, _ = supervise(lease, symbol, frozen, lambda **changes: None)
        lease.check()
        finished = int(time.time())
        with symbol_scope(symbol), Session.begin() as session:
            lock_wallet(session)
            state = read_state(session, finished)
            if error is None:
                attach(state, report)
            before = set(state['candidates'])
            admitted = admit_drafts(state, finished, active_strategies(session, state)) if state.get('enabled') else 0
            state['population_audit_job'].update(status='failed' if error else 'completed',
                error=error, updated_at=finished, archived=sorted(before - set(state['candidates'])),
                admitted=admitted, admitted_strategies=sorted(set(state['candidates']) - before))
            save_state(session, state)
        return True
    return False


def main():
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    logger.info("Futures draft replay worker starting")
    worker_conn, last_touch = hold_worker(), 0
    while not stop.is_set():
        now = int(time.time())
        enabled = os.getenv("FUTURES_DRAFT_REPLAY_ENABLED", "false").lower() == "true"
        try:
            if worker_conn:
                worker_conn.check()
            if now - last_touch >= 30:
                with Session.begin() as session:
                    lock_wallet(session)
                    touch(session, now, consumer_enabled=enabled, phase="idle" if enabled else "disabled")
                last_touch = now
            if enabled:
                lease = acquire()
                if lease:
                    try:
                        admit_waiting(now)
                        if refresh_native_parents(lease, now):
                            continue
                        symbol, draft_id = next_draft(int(time.time()))
                        if symbol and draft_id:
                            process(lease, symbol, draft_id)
                        else:
                            audit_population(lease, int(time.time()))
                    finally:
                        lease.release()
        except InterruptedError:
            break
        except Exception:
            logger.exception("Draft worker iteration failed")
        stop.wait(5)
    if worker_conn:
        try:
            worker_conn.release()
        except Exception:
            pass
    logger.info("Futures draft replay worker stopped")


if __name__ == "__main__":
    main()
