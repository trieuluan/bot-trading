import time
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from backend.app.auto_paper import auto_tick
from backend.app.db import AutoPaperState, PaperFunding, PaperTrade, PaperWallet, Session
from backend.app.feed import save_candles
from backend.app.main import app
from backend.app.market import Candle
from backend.app.paper import sync_paper_quote, sync_paper_stops
from backend.tests.test_paper import Market

client = TestClient(app)

@pytest.fixture
def recovery(monkeypatch):
    clock = [int(time.time()) // 900 * 900 + 10]
    monkeypatch.setattr(time, "time", lambda: clock[0])

    def seed(side="long"):
        sync_paper_quote(Market())
        response = client.post("/api/paper/orders", json=dict(
            request_id=str(uuid4()), side=side, notional=1000,
            stop=95 if side == "long" else 105, target=110 if side == "long" else 90,
        ))
        assert response.status_code == 200
        with Session.begin() as session:
            trade = session.get(PaperTrade, response.json()["trade_id"])
            cursor = trade.monitoring["next_bar"]
            session.get(AutoPaperState, 1).enabled = True
        clock[0] = cursor + 900 + 10
        return cursor

    return clock, seed

@pytest.mark.parametrize("side,high,low,reason", [
    ("long", 101, 94, "recovered_stop_loss"),
    ("short", 106, 99, "recovered_stop_loss"),
    ("long", 111, 99, "recovered_take_profit"),
    ("short", 101, 89, "recovered_take_profit"),
    ("long", 111, 94, "recovered_stop_loss"),
])
def test_crossings_close_at_historical_level_once_and_pause_auto(recovery, side, high, low, reason):
    clock, seed = recovery
    cursor = seed(side)
    with Session.begin() as session:
        save_candles(session, [Candle(cursor, 100, high, low, 100, 1)])
    sync_paper_quote(Market())
    with Session() as session:
        trade = session.get(PaperTrade, 1)
        assert trade.exit_reason == reason
        assert trade.exit_time == cursor + 899
        level = (95 if side == "long" else 105) if reason == "recovered_stop_loss" else (110 if side == "long" else 90)
        assert trade.exit == pytest.approx(level * (0.9998 if side == "long" else 1.0002))
        assert trade.monitoring["execution_model"] == "historical_levels_v1"
        assert trade.monitoring["recovered_at"] == clock[0]
        assert trade.monitoring["time_basis"] == "bar_end_proxy"
        assert trade.exit_fee == pytest.approx(trade.exit * trade.quantity * 8 / 10000)
        assert trade.monitoring["trigger_bar"] == cursor
        assert trade.monitoring["ambiguous"] == (high == 111 and low == 94)
        assert not session.get(AutoPaperState, 1).enabled
        balance = session.get(PaperWallet, 1).balance
    sync_paper_quote(Market())
    with Session() as session:
        assert session.get(PaperWallet, 1).balance == balance

@pytest.mark.parametrize("side,opened,expected,reason", [
    ("long", 92, 92 * 0.9998, "recovered_stop_loss"),
    ("short", 108, 108 * 1.0002, "recovered_stop_loss"),
    ("long", 115, 110 * 0.9998, "recovered_take_profit"),
    ("short", 85, 90 * 1.0002, "recovered_take_profit"),
])
def test_gaps_use_worse_stop_open_and_do_not_improve_target(recovery, side, opened, expected, reason):
    _, seed = recovery
    cursor = seed(side)
    with Session.begin() as session:
        save_candles(session, [Candle(cursor, opened, opened + 1, opened - 1, opened, 1)])
    sync_paper_quote(Market())
    with Session() as session:
        trade = session.get(PaperTrade, 1)
        assert trade.exit_reason == reason
        assert trade.exit == pytest.approx(expected)
        assert trade.exit_time == (cursor if reason == "recovered_stop_loss" else cursor + 899)

@pytest.mark.parametrize("cost", [2.5, -2.5])
def test_recovery_reverses_only_funding_after_simulated_exit_once(recovery, cost):
    _, seed = recovery
    cursor = seed()
    with Session.begin() as session:
        save_candles(session, [Candle(cursor, 100, 101, 94, 100, 1)])
        trade = session.get(PaperTrade, 1)
        wallet = session.get(PaperWallet, 1)
        balance = wallet.balance
        trade.funding_cost = cost * 2
        wallet.balance -= cost * 2
        for timestamp in (cursor, cursor + 900):
            session.add(PaperFunding(trade_id=trade.id, time=timestamp, rate=0.001, price=100, cost=cost))
    sync_paper_quote(Market())
    with Session() as session:
        trade = session.get(PaperTrade, 1)
        assert trade.funding_cost == cost
        assert session.get(PaperFunding, (1, cursor)).cost == cost
        assert session.get(PaperFunding, (1, cursor + 900)).cost == 0
        assert trade.monitoring["reversed_funding"] == [dict(time=cursor + 900, cost=cost)]
        gross = (trade.exit - trade.entry) * trade.quantity
        assert trade.net_pnl == pytest.approx(gross - trade.entry_fee - trade.exit_fee - cost)
        final_balance = session.get(PaperWallet, 1).balance
        assert final_balance == pytest.approx(balance + gross - trade.exit_fee - cost)
    sync_paper_quote(Market())
    with Session() as session:
        assert session.get(PaperWallet, 1).balance == final_balance

def test_missing_history_waits_across_restart_then_reconciles(recovery):
    clock, seed = recovery
    cursor = seed()
    clock[0] += 900
    with Session.begin() as session:
        save_candles(session, [Candle(cursor + 900, 100, 111, 99, 100, 1)])
    sync_paper_quote(Market())
    auto_tick()
    with Session() as session:
        trade = session.get(PaperTrade, 1)
        assert trade.exit_time is None
        assert {k: v for k, v in trade.monitoring.items() if k != "maintenance_margin_rate"} == dict(next_bar=cursor, status="history_pending")
        assert "đối soát" in session.get(AutoPaperState, 1).status
    with Session.begin() as session:
        save_candles(session, [Candle(cursor, 100, 101, 94, 100, 1)])
    sync_paper_quote(Market())
    with Session() as session:
        assert session.get(PaperTrade, 1).exit_reason == "recovered_stop_loss"

def test_live_stop_still_runs_when_history_missing(recovery):
    _, seed = recovery
    seed()
    sync_paper_quote(Market(93))
    with Session() as session:
        assert session.get(PaperTrade, 1).exit_reason == "stop_loss"

@pytest.mark.parametrize("side,high,low,reason", [
    ("long", 101, 94, "recovered_stop_loss"),
    ("short", 106, 99, "recovered_stop_loss"),
    ("long", 111, 99, "recovered_take_profit"),
    ("short", 101, 89, "recovered_take_profit"),
])
def test_historical_stops_run_without_fresh_quote_or_network(recovery, monkeypatch, side, high, low, reason):
    clock, seed = recovery
    cursor = seed(side)
    with Session.begin() as session:
        save_candles(session, [Candle(cursor, 100, high, low, 100, 1)])
        wallet = session.get(PaperWallet, 1)
        wallet.quote = None
        wallet.error = "Cannot reach MEXC after 3 attempts"
    def no_network(*args, **kwargs):
        pytest.fail("Historical stop must not fetch ticker")
    monkeypatch.setattr("backend.app.paper.MexcFuturesAdapter.ticker", no_network)
    sync_paper_stops(clock[0])
    with Session() as session:
        trade = session.get(PaperTrade, 1)
        assert trade.exit_reason == reason
        assert trade.exit_time == cursor + 899
        assert not session.get(AutoPaperState, 1).enabled
        balance = session.get(PaperWallet, 1).balance
    sync_paper_stops(clock[0])
    with Session() as session:
        assert session.get(PaperWallet, 1).balance == balance

def test_historical_stop_precedes_present_target_and_has_no_96_bar_limit(recovery):
    clock, seed = recovery
    cursor = seed()
    clock[0] = cursor + 110 * 900 + 10
    with Session.begin() as session:
        save_candles(session, [
            Candle(cursor + index * 900, 100, 101, 94 if index == 0 else 99, 100, 1)
            for index in range(110)
        ])
    sync_paper_quote(Market(115))
    with Session() as session:
        trade = session.get(PaperTrade, 1)
        assert trade.exit_reason == "recovered_stop_loss"
        assert trade.monitoring["trigger_bar"] == cursor

def test_legacy_trade_excludes_entry_bar_and_unclosed_bar(recovery):
    clock, seed = recovery
    cursor = seed()
    clock[0] = cursor + 10
    with Session.begin() as session:
        session.get(PaperTrade, 1).monitoring = None
        save_candles(session, [
            Candle(cursor - 900, 100, 111, 94, 100, 1),
            Candle(cursor, 100, 111, 94, 100, 1),
        ])
    sync_paper_quote(Market())
    with Session() as session:
        trade = session.get(PaperTrade, 1)
        assert trade.exit_time is None
        assert trade.monitoring["next_bar"] == cursor
