"""Shared read-only Forex snapshots; slow analysis cannot block quotes or other parts."""

import asyncio
import hashlib
import json
import logging
import os
import time

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse

from .forex_symbols import request_symbol

router = APIRouter()
TTLS = {"dashboard": 10, "execution": 5, "markets": 5, "analysis": 60}
_snapshots = {}
_slots = asyncio.Semaphore(2)


def build(part, symbol, offset):
    if part == "execution":
        from .exness_execution import execution_summary
        return execution_summary(offset=offset, limit=50, symbol=symbol)
    if part == "analysis":
        from .forex_learning import analysis_endpoint
        return analysis_endpoint(symbol)
    from .main import forex_markets, workspace_dashboard
    return forex_markets() if part == "markets" else workspace_dashboard("exness_forex", symbol)


def wire_snapshot(part, symbol, offset):
    value = build(part, symbol, offset)
    text = json.dumps(jsonable_encoder({"symbol": symbol, "data": value}), separators=(",", ":"))
    return (hashlib.blake2b(text.encode(), digest_size=8).digest(),
            f"event: {part}\ndata: {text}\n\n")


async def refresh(entry, part, symbol, offset):
    async with _slots:
        if not entry["viewers"]:
            return
        try:
            entry["wire"] = await asyncio.to_thread(wire_snapshot, part, symbol, offset)
            entry["error"] = False
        except Exception:
            entry["error"] = True
            logging.exception("Forex stream snapshot unavailable: %s %s", symbol, part)
        finally:
            entry["at"] = time.monotonic()


def subscribe(part, symbol, offset):
    key = (os.getenv("EXNESS_ACCOUNT_ID", ""), part,
           None if part == "markets" else symbol, offset if part == "execution" else 0)
    entry = _snapshots.setdefault(key, {"at": 0, "task": None, "wire": None, "error": False, "viewers": 0})
    entry["viewers"] += 1
    # ponytail: one uvicorn process; use external pub/sub if the API gains multiple workers.
    if len(_snapshots) > 64:
        for old_key, old in list(_snapshots.items()):
            if not old["viewers"] and (old["task"] is None or old["task"].done()):
                del _snapshots[old_key]
                if len(_snapshots) <= 64:
                    break
    return entry


def update(entry, part, symbol, offset):
    task = entry["task"]
    if (task is None or task.done()) and (entry["wire"] is None or time.monotonic() - entry["at"] >= TTLS[part]):
        if not entry["error"] or time.monotonic() - entry["at"] >= 10:
            entry["task"] = asyncio.create_task(refresh(entry, part, symbol, offset))


@router.get("/api/workspaces/exness_forex/stream")
async def stream(request: Request, symbol: str = "XAUUSD", parts: str = "dashboard,execution,markets",
                 offset: int = Query(0, ge=0, le=10000)):
    request_symbol(symbol)
    selected = set(parts.split(","))
    if not selected or selected - TTLS.keys():
        raise HTTPException(422, "Unsupported Forex stream parts")

    async def events():
        entries, seen, failed = {}, {}, set()
        last = time.monotonic()
        try:
            yield "retry: 3000\n\n"
            for part in sorted(selected):
                entries[part] = subscribe(part, symbol, offset)
            while not await request.is_disconnected():
                for part, entry in entries.items():
                    update(entry, part, symbol, offset)
                    if entry["error"]:
                        if part not in failed:
                            yield 'event: unavailable\ndata: ' + json.dumps({"part": part, "symbol": symbol}) + '\n\n'
                        failed.add(part)
                        continue
                    wire = entry["wire"]
                    if wire and (part in failed or seen.get(part) != wire[0]):
                        seen[part], last = wire[0], time.monotonic()
                        failed.discard(part)
                        # Markets share a global snapshot; always tag with this subscriber's symbol.
                        if part == "markets":
                            text = json.loads(wire[1].split("\ndata: ", 1)[1])
                            text["symbol"] = symbol
                            yield 'event: markets\ndata: ' + json.dumps(text, separators=(",", ":")) + '\n\n'
                        else:
                            yield wire[1]
                if time.monotonic() - last >= 10:
                    last = time.monotonic()
                    yield "event: heartbeat\ndata: {}\n\n"
                await asyncio.sleep(0.5)
        finally:
            for entry in entries.values():
                entry["viewers"] -= 1

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})
