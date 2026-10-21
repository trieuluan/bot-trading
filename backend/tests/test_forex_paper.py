from types import SimpleNamespace

import pytest
from sqlalchemy import select

from backend.app.db import (
    ForexObservation,
    ForexPaperAccount,
    ForexPaperTrade,
    PaperWallet,
    Session,
    WorkspaceCandle,
    WorkspaceFeedState,
)
from backend.app.exness_feed import FRAMES, WORKSPACE
from backend.app.forex_paper import _step_trade, forex_paper_summary, sync_paper_quote
from backend.app.forex_paper import legacy_paper_tick as paper_tick

NOW = 720000

def seed_tick_position(side="long", status="open"):
    with Session.begin() as session:
        for channel in ("live", "replay"):
            session.add(ForexPaperAccount(
                workspace_id=WORKSPACE, channel=channel, started_at=NOW - 1800,
                last_candle=NOW - 900, balance=10000, blocked=False,
            ))
            session.add(ForexPaperTrade(
                workspace_id=WORKSPACE, channel=channel, confirm_time=NOW - 1800,
                snapshot={
                    "status": status, "side": side, "confirm_time": NOW - 1800,
                    "last_candle": NOW - 900, "entry": 100, "entry_time": NOW - 900,
                    "risk_per_unit": 2, "stop": 98 if side == "long" else 102,
                    "target": 104 if side == "long" else 96, "units": 10, "bars": 1,
                    "slippage_bps": 2, "commission_per_unit": 0,
                    "last_quote_time": NOW - 1,
                },
            ))

def live_quote(stamp=NOW, **values):
    return {"bid": 100, "ask": 100.5, "time": stamp, "received_at": stamp, **values}

@pytest.mark.parametrize("side,bid,ask,outcome,base", [
    ("long", 97, 97.5, "stop", 97),
    ("short", 102, 103, "stop", 103),
    ("long", 105, 105.5, "target", 104),
    ("short", 94.5, 95, "target", 96),
])
def test_live_exit_bid_ask_costs_once_and_replay_isolated(side, bid, ask, outcome, base):
    seed_tick_position(side)
    quote = live_quote(bid=bid, ask=ask)
    sync_paper_quote(quote, now=NOW)
    first = forex_paper_summary()
    trade = trades()[0]
    direction = 1 if side == "long" else -1
    assert trade["exit_source"] == "live_quote"
    assert trade["outcome"] == outcome
    assert trade["exit"] == pytest.approx(base * (1 - direction * 0.0002))
    assert trade["exit_time"] == NOW
    assert first["portfolios"][0]["balance"] == round(10000 + trade["pnl"], 2)
    assert first["portfolios"][1]["balance"] == 10000
    assert trades()[1]["status"] == "open"
    sync_paper_quote(quote, now=NOW)
    sync_paper_quote(live_quote(NOW + 1, bid=bid, ask=ask), now=NOW + 1)
    assert forex_paper_summary() == first
    with Session() as session:
        assert session.get(PaperWallet, 1).balance == 10000

@pytest.mark.parametrize("side,bid,ask", [("long", 103.5, 104.5), ("short", 95.5, 96.5)])
def test_non_executable_side_cannot_trigger_target(side, bid, ask):
    seed_tick_position(side)
    sync_paper_quote(live_quote(bid=bid, ask=ask), now=NOW)
    assert trades()[0]["status"] == "open"
    assert trades()[0]["monitoring"] == "live"

@pytest.mark.parametrize("values,now", [
    ({}, NOW + 16), ({"received_at": NOW - 16}, NOW), ({"time": NOW + 1}, NOW),
    ({"bid": float("nan")}, NOW), ({"ask": float("inf")}, NOW),
    ({"bid": True}, NOW), ({"bid": -1}, NOW), ({"ask": 99}, NOW),
    ({"time": NOW - 1}, NOW),
])
def test_bad_stale_duplicate_or_old_quote_cannot_change_paper(values, now):
    seed_tick_position()
    before = trades()
    sync_paper_quote(live_quote(**values), now=now)
    assert trades() == before

@pytest.mark.parametrize("status", ["pending", "resolved", "censored", "skipped"])
def test_tick_never_opens_or_changes_non_open_trade(status):
    seed_tick_position(status=status)
    before = trades()
    sync_paper_quote(live_quote(bid=97, ask=97.5), now=NOW)
    assert trades() == before

@pytest.mark.parametrize("gap_kind", ["reconnect", "missing_ticks", "first_tick"])
def test_stream_gap_defers_current_bar_to_historical_first_touch(gap_kind):
    seed_tick_position()
    if gap_kind != "reconnect":
        with Session.begin() as session:
            row = session.get(ForexPaperTrade, (WORKSPACE, "live", NOW - 1800))
            data = dict(row.snapshot)
            if gap_kind == "first_tick":
                data.pop("last_quote_time")
            else:
                data["last_quote_time"] = NOW - 20
            row.snapshot = data
    sync_paper_quote(live_quote(bid=105, ask=105.5), stream_gap=gap_kind == "reconnect", now=NOW)
    assert trades()[0]["status"] == "open"
    assert trades()[0]["monitoring"] == "waiting_closed_bar"
    seed([NOW - 900, NOW])
    with Session.begin() as session:
        bar = session.get(WorkspaceCandle, (WORKSPACE, "XAUUSD", "15m", "bid", NOW))
        bar.low = 97
        bar.high = 106
        ask = session.get(WorkspaceCandle, (WORKSPACE, "XAUUSD", "15m", "ask", NOW))
        ask.low = 97.5
        ask.high = 106.5
    paper_tick(NOW + 900)
    trade = trades()[0]
    assert trade["outcome"] == "stop"
    assert trade["exit_source"] == "closed_candle"
    assert trade["ambiguous_bar"] is True
    balance = forex_paper_summary()["portfolios"][0]["balance"]
    sync_paper_quote(live_quote(NOW + 900, bid=105, ask=105.5), now=NOW + 900)
    assert forex_paper_summary()["portfolios"][0]["balance"] == balance

def test_history_backlog_preserves_first_live_touch_until_checkpoint_catches_up():
    seed_tick_position()
    with Session.begin() as session:
        account = session.get(ForexPaperAccount, (WORKSPACE, "live"))
        account.last_candle = NOW - 1800
    sync_paper_quote(live_quote(bid=97, ask=97.5), now=NOW)
    assert trades()[0]["monitoring"] == "waiting_history"
    with Session.begin() as session:
        account = session.get(ForexPaperAccount, (WORKSPACE, "live"))
        account.last_candle = NOW - 900
    sync_paper_quote(live_quote(NOW + 1, bid=105, ask=105.5), now=NOW + 1)
    trade = trades()[0]
    assert trade["outcome"] == "stop"
    assert trade["exit_time"] == NOW
    assert trade["exit"] == pytest.approx(97 * 0.9998)

def test_continuous_quotes_resume_after_gap_bar_is_reconciled():
    seed_tick_position()
    sync_paper_quote(live_quote(), stream_gap=True, now=NOW)
    for stamp in range(NOW + 10, NOW + 901, 10):
        sync_paper_quote(live_quote(stamp), now=stamp)
    seed([NOW - 900, NOW])
    paper_tick(NOW + 900)
    sync_paper_quote(live_quote(NOW + 901, bid=105, ask=105.5), now=NOW + 901)
    assert trades()[0]["outcome"] == "target"
    assert trades()[0]["exit_source"] == "live_quote"

def test_failed_live_exit_rolls_back_balance_and_trade(monkeypatch):
    from backend.app import forex_paper

    seed_tick_position()
    before = forex_paper_summary()
    original = forex_paper._resolve_trade

    def fail(*args):
        original(*args)
        raise RuntimeError("DB write failed")

    monkeypatch.setattr(forex_paper, "_resolve_trade", fail)
    with pytest.raises(RuntimeError, match="DB write failed"):
        sync_paper_quote(live_quote(bid=97, ask=97.5), now=NOW)
    assert forex_paper_summary() == before


def seed(stamps, confirmations=()):
    with Session.begin() as session:
        for frame, (_, step) in FRAMES.items():
            feed = session.get(WorkspaceFeedState, (WORKSPACE, "XAUUSD", frame))
            feed.checked_until = NOW + 100 * 14400
            for stamp in stamps if frame == "15m" else [NOW - step]:
                for side in ("bid", "ask"):
                    offset = 0.5 if side == "ask" else 0
                    session.merge(
                        WorkspaceCandle(
                            workspace_id=WORKSPACE,
                            symbol="XAUUSD",
                            timeframe=frame,
                            price_type=side,
                            time=stamp,
                            open=100 + offset,
                            high=101 + offset,
                            low=99 + offset,
                            close=100 + offset,
                        )
                    )
        for stamp, provenance in confirmations:
            session.merge(
                ForexObservation(
                    workspace_id=WORKSPACE,
                    candle_time=stamp,
                    snapshot={
                        "status": "confirmed",
                        "side": "long",
                        "candle_time": stamp,
                        "stop": 98,
                        "level": 99,
                        "provenance": provenance,
                    },
                )
            )
        for stamp in stamps:
            if stamp not in {item[0] for item in confirmations}:
                session.merge(
                    ForexObservation(
                        workspace_id=WORKSPACE,
                        candle_time=stamp,
                        snapshot={"status": "waiting_breakout", "candle_time": stamp},
                    )
                )


def trades():
    with Session() as session:
        return [
            row.snapshot
            for row in session.scalars(
                select(ForexPaperTrade).order_by(
                    ForexPaperTrade.channel, ForexPaperTrade.confirm_time
                )
            )
        ]


def test_unpaired_feed_prevents_progress():
    seed([NOW - 900])
    paper_tick(NOW)
    seed([NOW], [(NOW, "live")])
    with Session.begin() as session:
        session.delete(session.get(WorkspaceCandle, (WORKSPACE, "XAUUSD", "15m", "ask", NOW)))
    assert paper_tick(NOW + 900)["status"] == "data_paused"
    assert trades() == []


@pytest.mark.parametrize(
    "side,outcome", [("long", "stop"), ("short", "stop"), ("long", "target"), ("short", "target")]
)
def test_bid_ask_stop_target_and_ambiguous_stop_first(side, outcome):
    trade = {
        "status": "open",
        "side": side,
        "last_candle": NOW - 900,
        "entry": 100,
        "risk_per_unit": 2,
        "stop": 98 if side == "long" else 102,
        "target": 104 if side == "long" else 96,
        "units": 10,
        "bars": 1,
        "slippage_bps": 0,
        "commission_per_unit": 0,
    }
    bid = SimpleNamespace(time=NOW, open=100, high=105, low=95, close=100)
    ask = SimpleNamespace(time=NOW, open=100.5, high=105.5, low=95.5, close=100.5)
    if outcome == "target":
        bid.low = ask.low = 99 if side == "long" else 95
        bid.high = ask.high = 105 if side == "long" else 101
    _step_trade(trade, bid, ask)
    assert trade["outcome"] == outcome
    assert trade["pnl"] == (-20 if outcome == "stop" else 40)
    if outcome == "stop":
        assert trade["ambiguous_bar"] is True


def test_stop_gap_fills_at_open_not_stop_and_future_bars_ignored():
    trade = {
        "status": "open",
        "side": "long",
        "last_candle": NOW - 900,
        "entry": 100,
        "risk_per_unit": 2,
        "stop": 98,
        "target": 104,
        "units": 10,
        "bars": 1,
        "slippage_bps": 0,
        "commission_per_unit": 0,
    }
    bar = SimpleNamespace(time=NOW, open=96, high=100, low=95, close=99)
    _step_trade(trade, bar, bar)
    assert trade["outcome"] == "stop_gap" and trade["exit"] == 96
    seed([NOW - 900, NOW + 900], [(NOW + 900, "live")])
    paper_tick(NOW)
    assert trades() == []


def test_api_exposes_separate_paper_and_never_broker_execution():
    from fastapi.testclient import TestClient

    from backend.app.main import app

    result = TestClient(app).get("/api/workspaces/exness_forex/dashboard").json()
    assert result["execution_enabled"] is False
    assert [row["channel"] for row in result["paper"]["portfolios"]] == ["live", "replay", "trend", "range", "router"]


def test_short_exits_on_ask_not_bid():
    trade = {
        "status": "open",
        "side": "short",
        "last_candle": NOW - 900,
        "entry": 100,
        "risk_per_unit": 2,
        "stop": 102,
        "target": 96,
        "units": 10,
        "bars": 1,
        "slippage_bps": 0,
        "commission_per_unit": 0,
    }
    bid = SimpleNamespace(time=NOW, open=100, high=101.5, low=99, close=100)
    ask = SimpleNamespace(time=NOW, open=100.5, high=102.5, low=99.5, close=100.5)
    _step_trade(trade, bid, ask)
    assert trade["outcome"] == "stop"


def test_next_open_past_stop_rejects_entry():
    trade = {
        "status": "pending",
        "side": "long",
        "last_candle": NOW - 900,
        "stop": 101,
        "slippage_bps": 0,
        "commission_per_unit": 0,
        "capital": 10000,
        "allocated_risk": 100,
    }
    bar = SimpleNamespace(time=NOW, open=100, high=101, low=99, close=100)
    _step_trade(trade, bar, bar)
    assert trade["status"] == "invalid"
    assert "entry" not in trade

@pytest.mark.parametrize("outcome", ["stop", "target"])
def test_sleep_recovery_closes_historical_touch_before_opposite_new_tick(outcome):
    seed_tick_position()
    seed([NOW - 900, NOW])
    with Session.begin() as session:
        for side in ("bid", "ask"):
            bar = session.get(WorkspaceCandle, (WORKSPACE, "XAUUSD", "15m", side, NOW))
            if outcome == "stop":
                bar.low = 97 if side == "bid" else 97.5
            else:
                bar.high = 105 if side == "bid" else 105.5
    opposite = live_quote(NOW + 901, bid=105 if outcome == "stop" else 97,
                          ask=105.5 if outcome == "stop" else 97.5)
    sync_paper_quote(opposite, stream_gap=True, now=NOW + 901)
    assert trades()[0]["status"] == "open"
    paper_tick(NOW + 901)
    trade = trades()[0]
    assert trade["outcome"] == outcome
    assert trade["exit_time"] == NOW
    assert trade["exit_source"] == "closed_candle"
    balance = forex_paper_summary()["portfolios"][0]["balance"]
    sync_paper_quote(opposite, now=NOW + 901)
    assert forex_paper_summary()["portfolios"][0]["balance"] == balance

def test_process_restart_cannot_close_persisted_trade_twice():
    import json
    import subprocess
    import sys

    seed_tick_position()
    script = "import json,sys; from backend.app.forex_paper import sync_paper_quote; sync_paper_quote(json.loads(sys.argv[1]), stream_gap=sys.argv[2]=='true', now=int(sys.argv[3]))"
    quote = json.dumps(live_quote(bid=97, ask=97.5))
    subprocess.run([sys.executable, "-c", script, quote, "false", str(NOW)], check=True, capture_output=True)
    before = forex_paper_summary()
    assert trades()[0]["status"] == "resolved"
    subprocess.run([sys.executable, "-c", script, quote, "true", str(NOW)], check=True, capture_output=True)
    assert forex_paper_summary() == before

@pytest.mark.parametrize("capital,expected_units", [(10000, 2), (3000, None)])
def test_entry_uses_lot_floor_without_increasing_risk_to_reach_minimum(capital, expected_units):
    from backend.app.forex_costs import normalize_specs
    from backend.tests.test_forex_costs import account, specification

    trade = {
        "status": "pending", "side": "long", "last_candle": NOW - 900, "stop": 3980,
        "slippage_bps": 2, "commission_per_unit": 0, "capital": capital, "allocated_risk": 100,
        "cost_profile": normalize_specs(account(), specification(), NOW),
    }
    bid = SimpleNamespace(time=NOW, open=4000, high=4001, low=3999, close=4000)
    ask = SimpleNamespace(time=NOW, open=4000.5, high=4001.5, low=3999.5, close=4000.5)
    _step_trade(trade, bid, ask)
    if expected_units is None:
        assert trade["reason"] == "below_minimum_lot"
        assert "entry" not in trade
    else:
        assert trade["units"] == expected_units
        assert trade["lots"] == 0.02
        assert trade["units"] * trade["entry"] <= capital
        assert trade["units"] * trade["risk_per_unit"] <= 100
