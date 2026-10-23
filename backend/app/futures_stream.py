"""SSE cho Futures: server tính một lần (cache dùng chung), mọi client chỉ nhận bản đã tính.

Tầng nhanh (board: ví + Auto mọi cặp; selected: ví kèm nến của cặp đang xem) tính lại vài giây một lần.
Tầng chậm (learning summary, state learner ~1MB/cặp) chỉ đổi theo nến 15m nên cache lâu hơn.
"""

import asyncio
import hashlib
import json
import logging
import threading
import time
from collections import defaultdict

from fastapi import APIRouter, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse

from .db import Session
from .futures_symbols import SYMBOLS, symbol_scope

router = APIRouter()
logger = logging.getLogger(__name__)
# ponytail: cache trong RAM theo process; chạy nhiều worker uvicorn thì chuyển sang Postgres LISTEN/NOTIFY.
BOARD_TTL, SELECTED_TTL, STATE_TTL, LEARNING_TTL = 3, 2, 10, 30
TICK, PING = 2, 15
_cache, _locks, _guard = {}, defaultdict(threading.Lock), threading.Lock()
_generation = 0

def invalidate():
    global _generation
    with _guard:
        _generation += 1
        _cache.clear()

def cached(key, ttl, build):
    """Mỗi key build tối đa một lần mỗi ttl cho mọi client; key khác không chờ nhau."""
    with _guard:
        lock = _locks[key]
    with lock:
        with _guard:
            hit = _cache.get(key)
            generation = _generation
        if hit and time.monotonic() - hit[0] < ttl:
            return hit[1]
        value = build()
        with _guard:
            if generation == _generation:
                _cache[key] = (time.monotonic(), value)
        return value

def load(fn):
    try:
        return fn()
    except HTTPException:
        return None

def learner_state(symbol):
    from .futures_learner import read_state

    def build():
        with symbol_scope(symbol), Session() as session:
            return read_state(session, int(time.time()))
    return cached(("state", symbol), STATE_TTL, build)

def slim_decision(decision):
    # Bảng tổng hợp chỉ cần trạng thái từng cây; bỏ levels/curves vẽ chart (~900KB mỗi cặp).
    if not isinstance(decision, dict):
        return decision
    rows = [{key: row.get(key) for key in ("strategy", "status", "side")}
            for row in decision.get("strategies") or []]
    return {**decision, "strategies": rows}

def slim_board(wallet, auto, learning):
    if isinstance(wallet, dict):
        wallet = {key: value for key, value in wallet.items() if key != "candles"}
    if isinstance(auto, dict):
        auto = {**auto, "learner_decision": slim_decision(auto.get("learner_decision"))}
    if isinstance(learning, dict):
        learning = {**learning, "last_decision": slim_decision(learning.get("last_decision"))}
    return {"wallet": wallet, "auto": auto, "learning": learning}

def build_board():
    from .auto_paper import auto_snapshot
    from .paper import wallet_snapshot

    board = {}
    for symbol in SYMBOLS:
        state = learner_state(symbol)
        with symbol_scope(symbol):
            board[symbol] = slim_board(load(lambda: wallet_snapshot(False)),
                                       load(lambda: auto_snapshot(state)), None)
    return board

def build_learning():
    from .futures_learner import paper_by_strategy, summary

    learning = {}
    for symbol in SYMBOLS:
        state, now = learner_state(symbol), int(time.time())
        with symbol_scope(symbol), Session() as session:
            learning[symbol] = slim_board(None, None, summary(state, now, paper_by_strategy(session)))["learning"]
    return learning

def board_snapshot():
    """Nguồn chung cho GET /board và SSE: ví + Auto (nhanh) ghép learning (chậm)."""
    board = cached("board", BOARD_TTL, build_board)
    learning = cached("learning", LEARNING_TTL, build_learning)
    return {symbol: {**row, "learning": learning.get(symbol)} for symbol, row in board.items()}

def selected_auto(symbol):
    from .auto_paper import auto_snapshot

    def build():
        with symbol_scope(symbol):
            return load(lambda: auto_snapshot(learner_state(symbol)))
    return cached(("selected_auto", symbol), BOARD_TTL, build)

def selected_wallet(symbol):
    from .paper import wallet_snapshot

    with symbol_scope(symbol):
        return {"symbol": symbol, "wallet": load(wallet_snapshot)}

def dashboard_snapshot():
    from .main import dashboard

    return cached("dashboard", SELECTED_TTL, dashboard)

def event(name, payload):
    text = json.dumps(jsonable_encoder(payload), separators=(",", ":"))
    return hashlib.blake2b(text.encode(), digest_size=8).digest(), f"event: {name}\ndata: {text}\n\n"

def events_for(symbol, parts):
    """board (nhanh) và learning (chậm) tách event; client nhận phần đổi, không nhận lại phần nặng."""
    out = []
    if "board" in parts:
        board = cached("board", BOARD_TTL, build_board)
        learning = cached("learning", LEARNING_TTL, build_learning)
        out += [("board", *cached("wire_board", BOARD_TTL, lambda: event("board", {"symbols": board}))),
                ("learning", *cached("wire_learning", LEARNING_TTL,
                                    lambda: event("learning", {"symbols": learning})))]
    if "selected" in parts:
        selected = cached(("selected", symbol), SELECTED_TTL, lambda: selected_wallet(symbol))
        out.append(("selected", *cached(("wire_selected", symbol), SELECTED_TTL,
                                       lambda: event("selected", selected))))
        out.append(("auto", *cached(("wire_auto", symbol), BOARD_TTL,
                                   lambda: event("auto", selected_auto(symbol)))))
    if "dashboard" in parts:
        out.append(("dashboard", *cached("wire_dashboard", SELECTED_TTL,
                                        lambda: event("dashboard", dashboard_snapshot()))))
    return out

@router.get("/api/workspaces/mexc_futures/stream")
async def stream(request: Request, symbol: str = SYMBOLS[0], parts: str = "board,learning,selected"):
    if symbol not in SYMBOLS:
        raise HTTPException(422, "Unsupported Futures symbol")
    parts = set(parts.split(","))
    if not parts or parts - {"board", "learning", "selected", "dashboard"}:
        raise HTTPException(422, "Unsupported stream parts")

    async def events():
        seen, last = {}, time.monotonic()
        yield "retry: 3000\n\n"
        while not await request.is_disconnected():
            try:
                updates = await asyncio.to_thread(events_for, symbol, parts)
            except Exception:
                logger.exception("Futures stream snapshot failed: %s", symbol)
                yield "event: unavailable\ndata: {}\n\n"
                await asyncio.sleep(10)
                continue
            for name, digest, text in updates:
                if seen.get(name) != digest:
                    seen[name], last = digest, time.monotonic()
                    yield text
            if time.monotonic() - last > PING:
                last = time.monotonic()
                yield ": ping\n\n"
            await asyncio.sleep(TICK)

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})
