import asyncio
import json

from fastapi.testclient import TestClient

from backend.app import forex_stream
from backend.app.main import app


def clean(monkeypatch):
    monkeypatch.setattr(forex_stream, "_snapshots", {})
    monkeypatch.setattr(forex_stream, "_slots", asyncio.Semaphore(2))


def test_stream_validates_symbol_parts_and_pagination():
    client = TestClient(app)
    for query in ("symbol=BTCUSD", "parts=bogus", "parts=", "offset=-1", "offset=10001"):
        assert client.get("/api/workspaces/exness_forex/stream?" + query).status_code == 422


def test_stream_requires_login(monkeypatch):
    monkeypatch.setenv("APP_PASSWORD", "test-only")
    assert TestClient(app).get("/api/workspaces/exness_forex/stream").status_code == 401


def test_snapshots_single_flight_and_isolated(monkeypatch):
    clean(monkeypatch)
    calls = []

    def build(part, symbol, offset):
        calls.append((part, symbol, offset))
        return {"symbol": symbol, "offset": offset}

    monkeypatch.setattr(forex_stream, "build", build)

    async def run():
        first = forex_stream.subscribe("execution", "XAUUSD", 0)
        second = forex_stream.subscribe("execution", "XAUUSD", 0)
        assert first is second
        forex_stream.update(first, "execution", "XAUUSD", 0)
        task = first["task"]
        forex_stream.update(second, "execution", "XAUUSD", 0)
        assert task is second["task"]
        await task
        forex_stream.update(first, "execution", "XAUUSD", 0)
        assert first["task"] is task
        for symbol, offset in (("EURUSD", 0), ("XAUUSD", 50)):
            entry = forex_stream.subscribe("execution", symbol, offset)
            forex_stream.update(entry, "execution", symbol, offset)
            await entry["task"]
            payload = json.loads(entry["wire"][1].split("data: ", 1)[1])
            assert payload["symbol"] == symbol
            assert payload["data"]["offset"] == offset
        monkeypatch.setenv("EXNESS_ACCOUNT_ID", "another-account")
        assert forex_stream.subscribe("execution", "XAUUSD", 0) is not first

    asyncio.run(run())
    assert calls == [("execution", "XAUUSD", 0), ("execution", "EURUSD", 0), ("execution", "XAUUSD", 50)]


def test_abandoned_queued_build_does_no_work(monkeypatch):
    clean(monkeypatch)
    calls = []
    monkeypatch.setattr(forex_stream, "build", lambda *args: calls.append(args))

    async def run():
        entry = forex_stream.subscribe("analysis", "XAUUSD", 0)
        entry["viewers"] = 0
        forex_stream.update(entry, "analysis", "XAUUSD", 0)
        await entry["task"]
        assert entry["wire"] is None

    asyncio.run(run())
    assert calls == []


def test_events_cleanup_and_recover_from_part_failure(monkeypatch):
    clean(monkeypatch)
    monkeypatch.setattr(forex_stream, "TTLS", {"dashboard": 0})

    class Request:
        async def is_disconnected(self):
            return False

    def broken(*args):
        raise RuntimeError("fixture failure")

    monkeypatch.setattr(forex_stream, "build", broken)

    async def run():
        response = await forex_stream.stream(Request(), "XAUUSD", "dashboard", 0)
        events = response.body_iterator
        assert "retry:" in await anext(events)
        try:
            event = await asyncio.wait_for(anext(events), 3)
            assert "event: unavailable" in event
            entry = next(iter(forex_stream._snapshots.values()))
            entry["at"] -= 11
            monkeypatch.setattr(forex_stream, "build", lambda *args: {"ok": True})
            event = await asyncio.wait_for(anext(events), 3)
            assert "event: dashboard" in event and '"ok":true' in event
        finally:
            await events.aclose()
        assert entry["viewers"] == 0

    asyncio.run(run())
