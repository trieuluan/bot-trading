import asyncio
import json
import time
from datetime import datetime, timezone

import pytest

from backend.app import exness_quotes
from backend.app.exness import ExnessError, load_private_key
from backend.app.exness_quotes import TICKS_PATH, QuoteStream, parse_quote, stream_config

NOW = datetime(2026, 10, 14, tzinfo=timezone.utc).timestamp()


def tick(**values):
    return {
        "instrument": "XAUUSD",
        "timestamp": "2026-10-14T00:00:00Z",
        "bid": "4000.5",
        "ask": "4001.0",
        **values,
    }


def limits():
    rate = {"limit": 1, "window_seconds": 10}
    return {
        "limits": {
            "websocket": {
                "global_account_limits": {
                    "max_active_connections": 1,
                    "max_inbound_message_bytes": 8192,
                    "connection_rate": rate,
                    "inbound_message_rate": rate,
                },
                "endpoints": [
                    {
                        "path": TICKS_PATH,
                        "connection_rate": rate,
                        "inbound_message_rate": rate,
                        "subscription_operation_rate": {"ticks": rate},
                    }
                ],
            }
        }
    }


def test_valid_bid_ask_and_timestamp():
    assert parse_quote(tick(), NOW) == {
        "bid": 4000.5,
        "ask": 4001.0,
        "time": NOW,
        "received_at": NOW,
    }


def quote_at(seconds, bid=4000, ask=4001):
    return {"time": NOW + seconds, "bid": bid, "ask": ask, "received_at": NOW + seconds}


def test_live_candles_include_extrema_between_sse_updates():
    stream = QuoteStream()
    stream.accept_quote(quote_at(10))
    stream.accept_quote(quote_at(10.1, 4005, 4008))
    stream.accept_quote(quote_at(10.2, 3998, 3999))
    stream.accept_quote(quote_at(10.3, 4002, 4004))
    for frame in ("15m", "1h", "4h"):
        bar = stream.snapshot()["candles"][frame][-1]
        assert bar["time"] == NOW and bar["partial"]
        assert bar["bid"] == {"open": 4000, "high": 4005, "low": 3998, "close": 4002}
        assert bar["ask"] == {"open": 4001, "high": 4008, "low": 3999, "close": 4004}


def test_candle_rollover_and_unknown_tick_gap():
    stream = QuoteStream()
    stream.accept_quote(quote_at(899))
    stream.accept_quote(quote_at(900, 4002, 4003))
    assert len(stream.candles["15m"]) == 2
    assert stream.candles["15m"][-1]["time"] == NOW + 900
    assert not stream.candles["15m"][-1]["partial"]
    assert len(stream.candles["1h"]) == 1
    stream.accept_quote(quote_at(920))
    assert stream.candles["15m"][-1]["partial"]


def test_out_of_order_tick_does_not_rewrite_live_bar():
    stream = QuoteStream()
    stream.accept_quote(quote_at(10))
    before = json.dumps(stream.snapshot()["candles"])
    stream.accept_quote(quote_at(9, 4500, 4501))
    assert json.dumps(stream.snapshot()["candles"]) == before
    assert stream.quote["time"] == NOW + 10


def test_live_buffer_is_bounded_and_does_not_fill_missing_bars():
    stream = QuoteStream()
    for index in range(40):
        stream.accept_quote(quote_at(index * 14400))
    for rows in stream.candles.values():
        assert len(rows) == 32
        assert all(bar["partial"] for bar in rows)
        assert rows[-1]["time"] == NOW + 39 * 14400


@pytest.mark.parametrize(
    "values",
    [
        {"instrument": "BTC_USDT"},
        {"bid": True},
        {"ask": "nan"},
        {"ask": "inf"},
        {"bid": 0},
        {"ask": 3999},
        {"timestamp": "2026-10-14T00:01:00Z"},
        {"timestamp": "2026-10-14T00:00:00"},
        {"timestamp": None},
    ],
)
def test_reject_invalid_quotes(values):
    assert parse_quote(tick(**values), NOW) is None


def test_ignore_ack_and_non_object():
    assert parse_quote({"id": "chart-xauusd", "success": True}, NOW) is None
    assert parse_quote([], NOW) is None


def test_limits_and_ticks_only_subscription():
    class API:
        def use_access_point(self):
            self.discovered = True

        def limits(self):
            assert self.discovered
            return limits()

    delay, message = stream_config(API())
    assert delay == 10
    assert json.loads(message) == {
        "id": "chart-xauusd",
        "subscribe": {"event": "ticks", "instruments": ["XAUUSD"]},
    }


def test_invalid_limits_fail_closed():
    class API:
        def use_access_point(self):
            pass

        def limits(self):
            data = limits()
            data["limits"]["websocket"]["global_account_limits"]["connection_rate"]["limit"] = 0
            return data

    with pytest.raises(ValueError):
        stream_config(API())


def test_freshness_uses_source_time_not_reconnect_time(monkeypatch):
    monkeypatch.setattr(exness_quotes.time, "time", lambda: NOW + 20)
    stream = QuoteStream()
    stream.status = "connected"
    assert stream.snapshot()["status"] == "waiting"
    stream.quote = parse_quote(tick(), NOW + 20)
    assert stream.snapshot()["status"] == "stale"
    monkeypatch.setattr(exness_quotes.time, "time", lambda: NOW + 1)
    assert stream.snapshot()["status"] == "live"
    stream.status = "reconnecting"
    assert stream.snapshot()["status"] == "reconnecting"


def test_signed_read_only_stream_ignores_older_tick_and_stops_on_error(monkeypatch):
    sent = []
    client_closed = []
    paper_quotes = []

    class Client:
        def close(self):
            client_closed.append(True)

    class API:
        api_key = "test-key"
        key = load_private_key("00" * 32)
        account_id = "123"
        base_url = "https://test.exness.com"
        client = Client()

    class Socket:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def send(self, message):
            sent.append(json.loads(message))

        async def __aiter__(self):
            yield json.dumps(tick())
            yield json.dumps(tick(timestamp="2026-10-13T23:59:00Z", bid=3990))
            yield json.dumps({"error": {"code": "FORBIDDEN"}})

    def connect(url, **kwargs):
        import base64

        assert url == "wss://test.exness.com/v1/server-events/accounts/123/ws/ticks"
        assert kwargs["subprotocols"] == ["exness-ws-protocol"]
        assert kwargs["compression"] is None
        encoded = kwargs["additional_headers"]["EXN-DATA"]
        signed = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
        assert signed["method"] == "GET"
        assert signed["path"] == "/v1/server-events/accounts/123/ws/ticks"
        return Socket()

    monkeypatch.setattr(exness_quotes.ExnessReadOnly, "from_env", lambda: API())
    monkeypatch.setattr(
        exness_quotes, "stream_config", lambda api: (5, '{"subscribe":{"event":"ticks"}}')
    )
    monkeypatch.setattr(exness_quotes, "connect", connect)
    monkeypatch.setattr(exness_quotes, "sync_paper_quote", lambda quote, gap: paper_quotes.append((quote, gap)))
    monkeypatch.setattr(exness_quotes.time, "time", lambda: NOW)
    stream = QuoteStream()
    asyncio.run(stream.run())
    assert stream.quote["bid"] == 4000.5
    assert stream.status == "rejected"
    assert len(sent) == 1
    assert len(paper_quotes) == 1
    assert paper_quotes[0][0]["bid"] == 4000.5
    assert paper_quotes[0][1] is True
    assert client_closed == [True]

def test_paper_write_failure_keeps_chart_live_and_marks_gap(monkeypatch):
    calls = []

    class API:
        api_key = "test-key"
        key = load_private_key("00" * 32)
        account_id = "123"
        base_url = "https://test.exness.com"
        client = type("Client", (), {"close": lambda self: None})()

    class Socket:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def send(self, message):
            pass

        async def __aiter__(self):
            for second in range(3):
                yield json.dumps(tick(timestamp=f"2026-10-14T00:00:0{second}Z"))
            yield json.dumps({"error": {"code": "FORBIDDEN"}})

    def paper_quote(quote, gap):
        calls.append(gap)
        if len(calls) == 1:
            raise RuntimeError("DB unavailable")

    monkeypatch.setattr(exness_quotes.ExnessReadOnly, "from_env", lambda: API())
    monkeypatch.setattr(exness_quotes, "stream_config", lambda api: (5, "{}"))
    monkeypatch.setattr(exness_quotes, "connect", lambda *args, **kwargs: Socket())
    monkeypatch.setattr(exness_quotes, "sync_paper_quote", paper_quote)
    monkeypatch.setattr(exness_quotes.time, "time", lambda: NOW + 3)
    stream = QuoteStream()
    asyncio.run(stream.run())
    assert calls == [True, True, False]
    assert stream.quote["time"] == NOW + 2
    assert stream.status == "rejected"


def test_start_disabled_and_stop_cleanly(monkeypatch):
    monkeypatch.setenv("EXNESS_DATA_ENABLED", "false")
    stream = QuoteStream()
    stream.start()
    assert stream.task is None and stream.snapshot()["status"] == "disabled"
    asyncio.run(stream.stop())


def test_rate_limit_backs_off_and_reconnects(monkeypatch):
    attempts = []
    waits = []

    class API:
        account_id = "123"
        api_key = "test-key"
        key = load_private_key("00" * 32)
        base_url = "https://test.exness.com"
        client = type("Client", (), {"close": lambda self: None})()

    class Socket:
        async def __aenter__(self):
            attempts.append(True)
            return self

        async def __aexit__(self, *args):
            pass

        async def send(self, message):
            pass

        async def __aiter__(self):
            yield json.dumps({"error": {"code": "REQUEST_RATE_LIMIT"}})

    async def sleep(seconds):
        waits.append(seconds)
        if len(waits) == 2:
            raise asyncio.CancelledError

    monkeypatch.setattr(exness_quotes.ExnessReadOnly, "from_env", lambda: API())
    monkeypatch.setattr(exness_quotes, "stream_config", lambda api: (10, "{}"))
    monkeypatch.setattr(exness_quotes, "connect", lambda *args, **kwargs: Socket())
    monkeypatch.setattr(exness_quotes.asyncio, "sleep", sleep)
    stream = QuoteStream()
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(stream.run())
    assert len(attempts) == 2
    assert waits == [60, 120]

@pytest.mark.parametrize("phase", ["setup", "handshake"])
def test_rate_limit_recovers_to_ticks_after_retry_after(monkeypatch, phase):
    from types import SimpleNamespace

    from websockets.exceptions import InvalidStatus

    attempts, waits = [], []
    stream = QuoteStream()

    class API:
        account_id = "123"
        api_key = "test-key"
        key = load_private_key("00" * 32)
        base_url = "https://test.exness.com"
        client = type("Client", (), {"close": lambda self: None})()

    class Socket:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def send(self, message):
            pass

        async def __aiter__(self):
            yield json.dumps(tick())
            yield json.dumps({"error": {"code": "FORBIDDEN"}})

    def configure(api):
        if phase == "setup":
            attempts.append(True)
            if len(attempts) == 1:
                raise ExnessError("Exness rate limit; waiting before retry", 120, rate_limited=True)
        return 5, "{}"

    def connect(*args, **kwargs):
        if phase == "handshake":
            attempts.append(True)
            if len(attempts) == 1:
                raise InvalidStatus(SimpleNamespace(status_code=429, headers={"Retry-After": "120"}))
        return Socket()

    async def sleep(seconds):
        assert stream.status == "rate_limited"
        waits.append(seconds)

    monkeypatch.setattr(exness_quotes.ExnessReadOnly, "from_env", lambda: API())
    monkeypatch.setattr(exness_quotes, "stream_config", configure)
    monkeypatch.setattr(exness_quotes, "connect", connect)
    monkeypatch.setattr(exness_quotes, "sync_paper_quote", lambda *args: None)
    monkeypatch.setattr(exness_quotes.asyncio, "sleep", sleep)
    monkeypatch.setattr(exness_quotes.time, "time", lambda: NOW)
    asyncio.run(stream.run())
    assert len(attempts) == 2
    assert waits == [120]
    assert stream.quote["bid"] == 4000.5
    assert stream.status == "rejected"


def test_network_drop_backs_off_and_can_be_cancelled(monkeypatch):
    class Client:
        closed = False

        def close(self):
            self.closed = True

    class API:
        account_id = "123"
        api_key = "test-key"
        key = load_private_key("00" * 32)
        base_url = "https://test.exness.com"
        client = Client()

    waits = []
    api = API()

    def disconnected(*args, **kwargs):
        raise OSError("offline")

    async def sleep(delay):
        waits.append(delay)
        if len(waits) == 3:
            raise asyncio.CancelledError

    monkeypatch.setattr(exness_quotes.ExnessReadOnly, "from_env", lambda: api)
    monkeypatch.setattr(exness_quotes, "stream_config", lambda api: (10, "{}"))
    monkeypatch.setattr(exness_quotes, "connect", disconnected)
    monkeypatch.setattr(exness_quotes.asyncio, "sleep", sleep)
    stream = QuoteStream()
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(stream.run())
    assert waits == [10, 20, 40]
    assert stream.status == "reconnecting"
    assert api.client.closed


def test_sse_uses_shared_state_without_upstream_per_tab(monkeypatch):
    stream = QuoteStream()
    monkeypatch.setattr(exness_quotes, "quotes", stream)

    async def read_once():
        response = await exness_quotes.quote_events()
        assert response.headers["x-accel-buffering"] == "no"
        chunk = await anext(response.body_iterator)
        await response.body_iterator.aclose()
        return json.loads(chunk.removeprefix("data: "))

    assert asyncio.run(read_once())["status"] == "disabled"
    assert stream.task is None


def test_chart_sse_pushes_on_tick_without_waiting_full_second(monkeypatch):
    stream = QuoteStream()
    stream.status = "connected"
    monkeypatch.setattr(exness_quotes, "quotes", stream)
    monkeypatch.setattr(exness_quotes, "chart_subscribers", {})

    async def run():
        response = await exness_quotes.quote_events("XAUUSD")
        events = response.body_iterator
        try:
            first = json.loads((await anext(events)).removeprefix("data: "))
            assert first["status"] == "waiting"
            now = time.time()
            quote = {"bid": 2400.1, "ask": 2400.3, "time": now, "received_at": now}
            stream.accept_quote(quote, "XAUUSD")
            exness_quotes.publish_entry({"status": "live", "symbol": "XAUUSD", "quote": quote})
            second = json.loads((await asyncio.wait_for(anext(events), timeout=0.3)).removeprefix("data: "))
            assert second["status"] == "live"
            assert second["quote"]["bid"] == 2400.1
        finally:
            await events.aclose()
        assert not exness_quotes.chart_subscribers["XAUUSD"]

    asyncio.run(run())
