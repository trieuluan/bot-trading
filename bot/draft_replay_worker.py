"""Dedicated single-job screening worker; never blocks stop execution or REST polling."""

import json
import logging
import multiprocessing
import os
import signal
import time
from threading import Event

from sqlalchemy import select, text

from backend.app.db import FuturesLearningState, Session
from backend.app.futures_draft_replay import MissingInputError, ReplayDataError, run_screen
from backend.app.futures_drafts import TIMEOUT_SECONDS, claim, complete, ready
from backend.app.futures_learner import (
    active_strategies,
    admit_drafts,
    read_state,
    save_state,
)
from backend.app.futures_research_lock import acquire, hold_worker
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
        report = run_screen(symbol, frozen, lambda **changes: send.send(("progress", changes)))
        send.send(("result", report))
    except Exception as exc:
        logger.exception("Replay calculation failed")
        send.send(("error", {"error": f"{type(exc).__name__}: {exc}",
                              "retryable": retryable(exc)}))
    finally:
        send.close()


def supervise(lease, symbol, frozen, emit):
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
            if time.monotonic() - started >= TIMEOUT_SECONDS:
                return None, "Replay exceeded 20 minutes", True
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
                        symbol, draft_id = next_draft(int(time.time()))
                        if symbol and draft_id:
                            process(lease, symbol, draft_id)
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
