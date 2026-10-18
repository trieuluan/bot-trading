import time
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from backend.app.auto_paper import auto_tick, baseline_auto_on_startup
from backend.app.db import (
    AutoPaperDecision,
    AutoPaperState,
    BotState,
    MarketState,
    PaperTrade,
    PaperWallet,
    Session,
)
from backend.app.feed import save_candles
from backend.app.main import app
from backend.app.market import Candle
from backend.app.paper import sync_paper_quote

client = TestClient(app)


@pytest.fixture
def ready(monkeypatch):
    clock = [int(time.time()) // 900 * 900 + 5]
    monkeypatch.setattr(time, "time", lambda: clock[0])

    def seed(side="long", price=None, enabled=True):
        now = clock[0]
        last = int(now) // 900 * 900 - 900
        close = {"long": 105, "short": 85, "none": 95}[side]
        price = price or close
        candles = [Candle(last - (20 - i) * 900, 95, 100, 90, 95, 10) for i in range(20)]
        candles.append(Candle(last, 95, max(106, close), min(84, close), close, 10))
        with Session.begin() as session:
            save_candles(session, candles)
            wallet = session.get(PaperWallet, 1)
            wallet.quote = dict(
                lastPrice=price,
                fairPrice=price,
                bid1=price - 0.1,
                ask1=price + 0.1,
                time=now,
                received_at=now,
                bar=dict(
                    time=now // 900 * 900, open=price, high=price, low=price, close=price, volume=0
                ),
            )
            wallet.contract = dict(contractSize=0.001, volUnit=1, minVol=1, maxVol=100000000)
            market = session.get(MarketState, 1)
            market.source = "mexc_futures"
            market.last_sync = datetime.fromtimestamp(now, timezone.utc)
            session.get(BotState, 1).heartbeat = datetime.fromtimestamp(now, timezone.utc)
            state = session.get(AutoPaperState, 1)
            state.enabled = enabled
            state.last_candle = last - 900
            from backend.app import futures_learner as learner
            from backend.app.futures_strategies import SEEDS
            learning = learner.new_state(0)
            learning.update(auto_mode="paper_learning", candidates={})
            from backend.app.strategy import Setup
            setups = {"long": Setup("long", 105, 95, 125, "test"),
                      "short": Setup("short", 85, 95, 65, "test"), "none": None}
            monkeypatch.setattr(learner, "strategy_setup", lambda *args: setups[side])
            from backend.app.strategy import Setup
            setups = {"long": Setup("long", 105, 95, 125, "test"),
                      "short": Setup("short", 85, 95, 65, "test"), "none": None}
            monkeypatch.setattr(learner, "strategy_setup", lambda *args: setups[side])
            learner.add_candidate(learning, SEEDS[0], 0)
            learner.step(learning, candles, candles[-1], now)
            learner.save_state(session, learning)
        return last

    return clock, seed


@pytest.mark.parametrize("side", ["long", "short"])
def test_auto_entries_share_fills_risk_sizing_and_bar_deduplication(ready, side):
    clock, seed = ready
    last = seed(side)
    auto_tick()
    wallet = client.get("/api/paper/wallet").json()
    trade = wallet["position"]
    assert trade["side"] == side and trade["origin"] == "auto"
    assert trade["signal_time"] == last
    assert trade["entry_time"] >= last + 900
    assert trade["quantity"] * abs(trade["entry"] - trade["stop"]) <= 100
    assert trade["quantity"] * trade["entry"] + trade["entry_fee"] <= 10000
    assert wallet["balance"] == pytest.approx(10000 - trade["entry_fee"])
    assert trade["entry"] == pytest.approx((105.1 * 1.0002) if side == "long" else (84.9 * 0.9998))
    auto_tick()
    # Even a stale cursor cannot replay a committed decision.
    with Session.begin() as session:
        session.get(AutoPaperState, 1).last_candle = last - 900
    auto_tick()
    with Session() as session:
        assert session.scalar(select(func.count()).select_from(PaperTrade)) == 1
        decision = session.get(AutoPaperDecision, ("BTC_USDT", last))
        assert decision.action == "opened" and decision.trade_id == trade["id"]
        assert not session.get(BotState, 1).enabled  # Independent from signal-only monitoring.


def test_enable_waits_for_new_bar_and_startup_skips_missed_history(ready):
    clock, seed = ready
    last = seed(enabled=False)
    assert client.post("/api/paper/auto/control", json={"enabled": True}).status_code == 200
    auto_tick()
    assert client.get("/api/paper/wallet").json()["position"] is None
    with Session.begin() as session:
        session.get(AutoPaperState, 1).last_candle = last - 10 * 900
    baseline_auto_on_startup()
    auto_tick()
    with Session() as session:
        assert session.get(AutoPaperState, 1).last_candle == last
        assert session.scalar(select(func.count()).select_from(PaperTrade)) == 0
    clock[0] += 900
    seed()
    auto_tick()
    assert client.get("/api/paper/wallet").json()["position"]["signal_time"] == last + 900


def test_stale_quote_waits_then_expired_signal_is_skipped(ready):
    clock, seed = ready
    last = seed()
    with Session.begin() as session:
        wallet = session.get(PaperWallet, 1)
        wallet.quote = {**wallet.quote, "received_at": clock[0] - 21}
    auto_tick()
    with Session() as session:
        assert session.get(AutoPaperDecision, ("BTC_USDT", last)) is None
    clock[0] += 65
    seed()  # Fresh quote, but already outside the entry window.
    auto_tick()
    with Session() as session:
        assert session.get(AutoPaperDecision, ("BTC_USDT", last)).action == "skipped"
        assert session.scalar(select(func.count()).select_from(PaperTrade)) == 0


def test_no_setup_and_gap_beyond_target_never_create_trades(ready):
    clock, seed = ready
    last = seed("none")
    auto_tick()
    with Session() as session:
        assert session.get(AutoPaperDecision, ("BTC_USDT", last)).action == "no_setup"
    clock[0] += 900
    last = seed("long", price=140)
    auto_tick()
    with Session() as session:
        assert session.get(AutoPaperDecision, ("BTC_USDT", last)).action == "skipped"
        assert session.scalar(select(func.count()).select_from(PaperTrade)) == 0
        assert session.get(PaperWallet, 1).balance == 10000


def test_pause_keeps_position_and_stops_work_while_auto_is_disabled(ready):
    clock, seed = ready
    seed()
    auto_tick()
    assert client.post("/api/paper/auto/control", json={"enabled": False}).status_code == 200
    position = client.get("/api/paper/wallet").json()["position"]
    assert position is not None

    class StopQuote:
        def ticker(self):
            return dict(
                lastPrice=70,
                fairPrice=70,
                bid1=69.9,
                ask1=70.1,
                time=clock[0],
                received_at=clock[0],
            )

    sync_paper_quote(StopQuote())
    wallet = client.get("/api/paper/wallet").json()
    assert wallet["position"] is None
    assert wallet["trades"][0]["exit_reason"] == "stop_loss"
    assert not client.get("/api/paper/auto").json()["enabled"]


def test_daily_trade_limit_stops_entries_without_closing_position(ready):
    clock, seed = ready
    seed()
    with Session.begin() as session:
        session.get(AutoPaperState, 1).max_daily_trades = 1
    auto_tick()
    auto_tick()
    state = client.get("/api/paper/auto").json()
    assert not state["enabled"] and state["daily_trades"] == 1
    assert client.get("/api/paper/wallet").json()["position"]
    assert client.post("/api/paper/auto/control", json={"enabled": True}).status_code == 409


def test_daily_loss_cap_and_remaining_budget(ready):
    clock, seed = ready
    seed()
    with Session.begin() as session:
        wallet = session.get(PaperWallet, 1)
        wallet.balance = 9800
        state = session.get(AutoPaperState, 1)
        state.day = datetime.fromtimestamp(clock[0], timezone.utc).date().isoformat()
        state.day_start_equity = 10000
        state.risk_percent = 5
    auto_tick()
    trade = client.get("/api/paper/wallet").json()["position"]
    assert trade["quantity"] * abs(trade["entry"] - trade["stop"]) <= 100
    with Session.begin() as session:
        session.get(PaperWallet, 1).balance = 9500
    auto_tick()
    state = client.get("/api/paper/auto").json()
    assert not state["enabled"]
    assert "giới hạn lỗ" in state["status"]


def test_auto_config_source_guard_and_manual_entry_interlock(ready):
    clock, seed = ready
    seed()
    assert client.post("/api/paper/auto/config", json={"risk_percent": 2}).status_code == 409
    assert client.post("/api/market/config", json={"source": "synthetic"}).status_code == 409
    request = dict(request_id=str(uuid4()), side="long", notional=1000, stop=90, target=135)
    assert client.post("/api/paper/orders", json=request).status_code == 409
    assert client.post("/api/paper/auto/control", json={"enabled": False}).status_code == 200
    assert (
        client.post(
            "/api/paper/auto/config",
            json={"risk_percent": 2, "max_daily_trades": 3, "daily_loss_percent": 4},
        ).status_code
        == 200
    )
    assert client.post("/api/paper/auto/config", json={"risk_percent": 99}).status_code == 422
    assert client.post("/api/market/config", json={"source": "synthetic"}).status_code == 200
    assert client.post("/api/paper/auto/control", json={"enabled": True}).status_code == 409


def test_waits_for_quote_after_close_and_contiguous_fresh_candles(ready):
    clock, seed = ready
    last = seed()
    with Session.begin() as session:
        wallet = session.get(PaperWallet, 1)
        wallet.quote = {**wallet.quote, "time": last + 899}
    auto_tick()
    with Session() as session:
        assert session.get(AutoPaperDecision, ("BTC_USDT", last)) is None
    seed()
    with Session.begin() as session:
        market = session.get(MarketState, 1)
        market.last_sync = datetime.fromtimestamp(clock[0] - 61, timezone.utc)
    auto_tick()
    with Session() as session:
        assert session.get(AutoPaperDecision, ("BTC_USDT", last)) is None
    seed()
    auto_tick()
    assert client.get("/api/paper/wallet").json()["position"] is not None


def test_unexpected_auto_failure_keeps_stops_and_position(ready):
    from backend.app.auto_paper import report_auto_failure

    _, seed = ready
    seed()
    auto_tick()
    report_auto_failure()
    state = client.get("/api/paper/auto").json()
    assert not state["enabled"] and "lỗi xử lý" in state["status"]
    assert client.get("/api/paper/wallet").json()["position"] is not None


def test_daily_baseline_does_not_roll_on_stale_price(ready):
    clock, seed = ready
    seed(enabled=False)
    old_day = datetime.fromtimestamp(clock[0] - 86400, timezone.utc).date().isoformat()
    with Session.begin() as session:
        state = session.get(AutoPaperState, 1)
        state.day = old_day
        wallet = session.get(PaperWallet, 1)
        wallet.balance = 9800
        wallet.quote = {**wallet.quote, "received_at": clock[0] - 30}
    state = client.get("/api/paper/auto").json()
    assert state["day"] == old_day and state["day_start_equity"] == 10000
    seed(enabled=False)
    state = client.get("/api/paper/auto").json()
    assert state["day"] != old_day and state["day_start_equity"] == 9800
    assert state["daily_loss"] == 0 and not state["enabled"]


def test_auto_opens_second_position_after_funding_recovers(ready):
    clock, seed = ready
    last = seed()
    with Session.begin() as session:
        session.get(PaperWallet, 1).funding_error = "Waiting for settlement history"
    auto_tick()
    with Session() as session:
        assert session.get(AutoPaperDecision, ("BTC_USDT", last)) is None
    with Session.begin() as session:
        session.get(PaperWallet, 1).funding_error = None
    auto_tick()
    clock[0] += 900
    last = seed()
    auto_tick()
    with Session() as session:
        assert session.get(AutoPaperDecision, ("BTC_USDT", last)).action == "opened"
        assert session.scalar(select(func.count()).select_from(PaperTrade)) == 2


def test_trade_audit_preserves_entry_context_after_settings_and_candles_change(ready):
    _, seed = ready
    last = seed("short")
    auto_tick()
    trade = client.get("/api/paper/wallet").json()["position"]
    audit = trade["audit"]
    assert audit["strategy"].startswith("rule_tree:")
    assert audit["selector"] == "futures_learner"
    assert audit["signal_close"] == 85
    assert audit["signal_time"] == last
    assert audit["risk_percent"] == 1
    assert audit["risk_budget"] == 100
    assert trade["entry_reason"] == audit["reason"]
    with Session.begin() as session:
        session.get(AutoPaperState, 1).risk_percent = 2
        save_candles(session, [Candle(last - 900, 95, 120, 80, 95, 10)])
    updated = client.get("/api/paper/wallet").json()["position"]
    assert updated["audit"] == audit
    candles = client.get(f"/api/paper/trades/{trade['id']}/candles").json()
    assert candles[0]["time"] <= last
    assert any(c["time"] == last for c in candles)
    assert candles[-1]["time"] == last + 900  # Current observed bar keeps fills visible.
    assert client.get("/api/paper/trades/99999/candles").status_code == 404


def test_legacy_auto_trade_has_reason_without_inventing_breakout_snapshot(ready):
    _, seed = ready
    last = seed()
    auto_tick()
    with Session.begin() as session:
        decision = session.get(AutoPaperDecision, ("BTC_USDT", last))
        decision.parameters = {"risk_percent": 1}
    trade = client.get("/api/paper/wallet").json()["position"]
    assert trade["audit"] is None
    assert trade["entry_reason"] == "test"


def test_auto_enters_same_strategy_repeatedly_without_cap(ready):
    clock, seed = ready
    for i in range(4):
        last = seed("long")
        auto_tick()
        clock[0] += 900
    with Session() as session:
        trades = session.scalars(select(PaperTrade).where(PaperTrade.exit_time.is_(None))).all()
        assert len(trades) == 4
        assert len({t.id for t in trades}) == 4
        # All entered under the same breakout strategy
        assert all(t.origin == "auto" for t in trades)
