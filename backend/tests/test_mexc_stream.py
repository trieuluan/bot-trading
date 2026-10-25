import json
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from backend.app import mexc_stream, paper
from backend.app.db import PaperTrade, PaperWallet, Session
from backend.app.futures_symbols import SYMBOLS, current_symbol, symbol_scope
from backend.app.main import app


@pytest.fixture(autouse=True)
def isolated_stream_health():
    for symbol in SYMBOLS:
        mexc_stream.clear_stream_health(symbol)
    yield
    for symbol in SYMBOLS:
        mexc_stream.clear_stream_health(symbol)

@pytest.fixture
def stream_clock(monkeypatch):
    clock = [time.time()]
    monkeypatch.setattr(mexc_stream, "time", SimpleNamespace(
        time=lambda: clock[0], monotonic=lambda: clock[0]))
    return clock

def recover_stream(clock, symbol="BTC_USDT"):
    for _ in range(3):
        mexc_stream.note_stream_quote(symbol, {"time": clock[0], "received_at": clock[0]})
        clock[0] += 1


def ticker(price=100, now=None, **updates):
    now = time.time() if now is None else now
    return json.dumps({"channel": "push.ticker", "symbol": "BTC_USDT", "ts": now * 1000,
                       "data": {"symbol": "BTC_USDT", "timestamp": now * 1000,
                                "lastPrice": price, "bid1": price - 0.1,
                                "ask1": price + 0.1, "fairPrice": price, **updates}})


@pytest.mark.parametrize("updates", [
    {"bid1": 102}, {"ask1": -1}, {"lastPrice": "NaN"}, {"fairPrice": True},
    {"bid1": None}, {"symbol": "ETH_USDT"}, {"timestamp": 0},
])
def test_reject_invalid_ticker(updates):
    assert mexc_stream.parse_ticker(ticker(**updates), "BTC_USDT") is None


def test_rest_quote_is_saved_even_if_lock_wait_ages_it_before_stop_check(monkeypatch):
    now = time.time()
    contract = dict(contractSize=0.001, volUnit=1, minVol=1, maxVol=100000000, priceUnit=0.1)
    paper.persist_paper_quote(mexc_stream.parse_ticker(ticker(now=now), "BTC_USDT"), contract)
    assert TestClient(app).post("/api/paper/orders", json={
        "request_id": str(uuid4()), "side": "long", "notional": 1000, "stop": 90, "target": 120,
    }).status_code == 200
    quote = mexc_stream.parse_ticker(ticker(101, now=now + 1), "BTC_USDT", now + 1)
    clock = [now + 1]
    real_lock = paper.lock_wallet
    calls = [0]
    def delayed_lock(session):
        calls[0] += 1
        if calls[0] == 2:
            clock[0] += paper.QUOTE_TTL + 1
        return real_lock(session)
    monkeypatch.setattr(paper.time, "time", lambda: clock[0])
    monkeypatch.setattr(paper, "lock_wallet", delayed_lock)
    assert paper.persist_paper_quote(quote)
    with Session() as session:
        assert session.get(PaperWallet, 1).quote["lastPrice"] == 101
        assert session.get(PaperTrade, 1).exit_time is None

def test_validate_channel_symbol_and_timestamps():
    now = time.time()
    assert mexc_stream.parse_ticker(ticker(now=now), "BTC_USDT", now)["bid1"] == 99.9
    assert mexc_stream.parse_ticker(ticker(now=now - 6), "BTC_USDT", now) is None
    assert mexc_stream.parse_ticker(ticker(now=now + 2), "BTC_USDT", now) is None
    assert mexc_stream.parse_ticker(ticker(now=now), "ETH_USDT", now) is None
    for raw in ('{}', 'null', '[]', 'not json', '{"channel":"pong"}'):
        assert mexc_stream.parse_ticker(raw, "BTC_USDT", now) is None


@pytest.mark.parametrize("side,price,reason", [
    ("long", 89, "stop_loss"), ("short", 111, "stop_loss"),
    ("long", 121, "take_profit"), ("short", 79, "take_profit"),
])
def test_stream_closes_position_without_closed_candle_or_duplicate_exit(monkeypatch, side, price, reason):
    now = time.time()
    contract = dict(contractSize=0.001, volUnit=1, minVol=1, maxVol=100000000, priceUnit=0.1)
    paper.persist_paper_quote(mexc_stream.parse_ticker(ticker(now=now - 1), "BTC_USDT"), contract)
    client = TestClient(app)
    opened = client.post("/api/paper/orders", json={
        "request_id": str(uuid4()), "side": side, "notional": 1000,
        "stop": 90 if side == "long" else 110, "target": 120 if side == "long" else 80,
    })
    assert opened.status_code == 200, opened.text
    monkeypatch.setattr(paper, "reconcile_stops", Mock(side_effect=AssertionError("history on WS")))
    quote = mexc_stream.parse_ticker(ticker(price, now=now), "BTC_USDT")
    paper.persist_paper_quote(quote, realtime=True)
    first = client.get("/api/paper/wallet").json()
    assert first["position"] is None
    assert first["trades"][0]["exit_reason"] == reason
    assert first["trades"][0]["exit"] == pytest.approx(
        quote["bid1"] * 0.9998 if side == "long" else quote["ask1"] * 1.0002)
    paper.persist_paper_quote(quote, realtime=True)
    assert client.get("/api/paper/wallet").json()["balance"] == first["balance"]


def test_rest_cannot_overwrite_newer_stream_and_stream_preserves_funding():
    now = time.time()
    quote = mexc_stream.parse_ticker(ticker(now=now - 1), "BTC_USDT")
    paper.persist_paper_quote({**quote, "nextFundingTime": now + 3600})
    quote = mexc_stream.parse_ticker(ticker(now=now), "BTC_USDT")
    paper.persist_paper_quote(quote, realtime=True)
    paper.persist_paper_quote({**quote, "bid1": 90}, {"contractSize": 0.01})
    paper.persist_paper_quote({**quote, "time": now - 1, "bid1": 80})
    with Session() as session:
        wallet = session.get(PaperWallet, 1)
        assert wallet.quote["source"] == "websocket"
        assert wallet.quote["bid1"] == 99.9
        assert wallet.quote["nextFundingTime"] == now + 3600
        assert wallet.contract == {"contractSize": 0.01}


def test_rest_failure_does_not_disable_fresh_stream():
    paper.persist_paper_quote(mexc_stream.parse_ticker(ticker(), "BTC_USDT"),
                             {"contractSize": 0.001}, realtime=True)
    adapter = Mock()
    adapter.ticker.side_effect = paper.MarketDataError("REST down")
    paper.sync_paper_quote(adapter)
    with Session() as session:
        wallet = session.get(PaperWallet, 1)
        assert wallet.error is None
        assert wallet.retry_at is None


def test_queued_stale_tick_is_rejected_after_wallet_lock():
    quote = mexc_stream.parse_ticker(ticker(), "BTC_USDT")
    paper.persist_paper_quote(quote, realtime=True)
    paper.persist_paper_quote({**quote, "received_at": quote["received_at"] - 6,
                              "bid1": 80}, realtime=True)
    paper.persist_paper_quote({**quote, "time": quote["time"] - 6,
                              "bid1": 70}, realtime=True)
    with Session() as session:
        assert session.get(PaperWallet, 1).quote["bid1"] == 99.9


def test_stream_respects_runner_reference_target_and_symbol_scope():
    client = TestClient(app)
    now = time.time()
    paper.persist_paper_quote(mexc_stream.parse_ticker(ticker(now=now - 1), "BTC_USDT"),
                             dict(contractSize=0.001, volUnit=1, minVol=1,
                                  maxVol=100000000, priceUnit=0.1))
    assert client.post("/api/paper/orders", json={
        "request_id": str(uuid4()), "side": "long", "notional": 1000,
        "stop": 90, "target": 120,
    }).status_code == 200
    with Session.begin() as session:
        trade = session.get(PaperTrade, client.get("/api/paper/wallet").json()["position"]["id"])
        trade.monitoring = {**(trade.monitoring or {}), "exit_policy": "runner"}
    with symbol_scope("ETH_USDT"):
        paper.persist_paper_quote(mexc_stream.parse_ticker(ticker(89, now=now), "BTC_USDT"),
                                 realtime=True)
    assert client.get("/api/paper/wallet").json()["position"] is not None
    paper.persist_paper_quote(mexc_stream.parse_ticker(ticker(121, now=now), "BTC_USDT"),
                             realtime=True)
    assert client.get("/api/paper/wallet").json()["position"] is not None


@pytest.mark.parametrize("committed", [True, False])
def test_stream_subscribes_processes_and_restores_symbol_scope(monkeypatch, committed):
    stop = threading.Event()
    socket = Mock()
    socket.recv.return_value = ticker()
    connection = Mock()
    connection.__enter__ = Mock(return_value=socket)
    connection.__exit__ = Mock(return_value=False)
    monkeypatch.setattr(mexc_stream, "connect", Mock(return_value=connection))
    calls = []

    def persist(quote, realtime):
        calls.append((current_symbol(), realtime, quote["bid1"]))
        stop.set()
        return committed

    monkeypatch.setattr(mexc_stream, "persist_paper_quote", persist)
    health_update = Mock()
    monkeypatch.setattr(mexc_stream, "note_stream_quote", health_update)
    mexc_stream.stream_paper_quotes("BTC_USDT", stop)
    assert calls == [("BTC_USDT", True, 99.9)]
    assert health_update.call_count == int(committed)
    assert json.loads(socket.send.call_args.args[0]) == {
        "method": "sub.ticker", "param": {"symbol": "BTC_USDT"}}


def test_stream_reconnects_without_disabling_rest(monkeypatch):
    stop = Mock()
    stop.is_set.side_effect = [False, True]
    monkeypatch.setattr(mexc_stream, "connect", Mock(side_effect=OSError("offline")))
    mexc_stream.stream_paper_quotes("ETH_USDT", stop)
    stop.wait.assert_called_once_with(1)
    assert current_symbol() == "BTC_USDT"

def test_health_requires_advancing_committed_ticks_and_recovers_after_gap(stream_clock):
    assert not mexc_stream.stream_ready("BTC_USDT")
    quote = {"time": stream_clock[0], "received_at": stream_clock[0]}
    mexc_stream.note_stream_quote("BTC_USDT", quote)
    stream_clock[0] += 2
    for _ in range(3):
        mexc_stream.note_stream_quote("BTC_USDT", quote)
    assert not mexc_stream.stream_ready("BTC_USDT")
    recover_stream(stream_clock)
    assert mexc_stream.stream_ready("BTC_USDT")
    assert not mexc_stream.stream_ready("ETH_USDT")
    stream_clock[0] += 5
    assert not mexc_stream.stream_ready("BTC_USDT")
    mexc_stream.note_stream_quote("BTC_USDT", {
        "time": stream_clock[0], "received_at": stream_clock[0]})
    assert not mexc_stream.stream_ready("BTC_USDT")
    recover_stream(stream_clock)
    assert mexc_stream.stream_ready("BTC_USDT")
    mexc_stream.clear_stream_health("BTC_USDT")
    assert not mexc_stream.stream_ready("BTC_USDT")

def test_rest_ticker_pauses_recovers_and_pauses_again(stream_clock):
    now = stream_clock[0]
    paper.persist_paper_quote({**mexc_stream.parse_ticker(ticker(now=now), "BTC_USDT"),
                               "nextFundingTime": now + 3600}, {"contractSize": 0.001},
                             realtime=True)
    adapter = Mock()
    adapter.ticker.return_value = mexc_stream.parse_ticker(ticker(now=now), "BTC_USDT")
    paper.sync_paper_quote(adapter)
    assert adapter.ticker.call_count == 1
    recover_stream(stream_clock)
    paper.sync_paper_quote(adapter)
    assert adapter.ticker.call_count == 1
    stream_clock[0] += 5
    paper.sync_paper_quote(adapter)
    assert adapter.ticker.call_count == 2
    recover_stream(stream_clock)
    paper.sync_paper_quote(adapter)
    assert adapter.ticker.call_count == 2
    mexc_stream.clear_stream_health("BTC_USDT")
    paper.sync_paper_quote(adapter)
    assert adapter.ticker.call_count == 3
    adapter.contract_info.assert_not_called()
    adapter.funding_next.assert_not_called()

def test_healthy_stream_still_bootstraps_contract_and_funding(stream_clock):
    now = stream_clock[0]
    paper.persist_paper_quote(mexc_stream.parse_ticker(ticker(now=now), "BTC_USDT"),
                             realtime=True)
    recover_stream(stream_clock)
    adapter = Mock()
    adapter.contract_info.return_value = {"contractSize": 0.001}
    adapter.funding_next.return_value = now + 3600
    paper.sync_paper_quote(adapter)
    adapter.ticker.assert_not_called()
    adapter.contract_info.assert_called_once()
    adapter.funding_next.assert_called_once()
    with Session() as session:
        wallet = session.get(PaperWallet, 1)
        assert wallet.contract == {"contractSize": 0.001}
        assert wallet.quote["source"] == "websocket"
        assert wallet.quote["nextFundingTime"] == now + 3600

def test_disconnection_clears_health_before_backoff(monkeypatch, stream_clock):
    stop = Mock()
    stop.is_set.side_effect = [False, True]
    def disconnect(*args, **kwargs):
        recover_stream(stream_clock)
        assert mexc_stream.stream_ready("BTC_USDT")
        raise OSError("offline")

    monkeypatch.setattr(mexc_stream, "connect", disconnect)

    def backoff(delay):
        assert delay == 1
        assert not mexc_stream.stream_ready("BTC_USDT")

    stop.wait.side_effect = backoff
    recover_stream(stream_clock)
    assert mexc_stream.stream_ready("BTC_USDT")
    mexc_stream.stream_paper_quotes("BTC_USDT", stop)
    stop.wait.assert_called_once_with(1)
