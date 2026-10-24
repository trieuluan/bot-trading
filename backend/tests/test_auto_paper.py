import time
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from fastapi import HTTPException
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

    def seed(side="long", price=None, enabled=True, policy="market"):
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
            monkeypatch.setattr(learner, "strategy_setup", lambda *args, **kwargs: setups[side])
            from backend.app.strategy import Setup
            setups = {"long": Setup("long", 105, 95, 125, "test"),
                      "short": Setup("short", 85, 95, 65, "test"), "none": None}
            monkeypatch.setattr(learner, "strategy_setup", lambda *args, **kwargs: setups[side])
            learner.add_candidate(learning, {**SEEDS[0], **({} if policy == "market" else {"entry_policy": policy})}, 0)
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


def test_quote_expiring_during_selection_waits_and_retries_without_disabling(ready, monkeypatch):
    from backend.app import auto_paper

    clock, seed = ready
    last = seed()
    select_setup = auto_paper.select_for_auto

    def slow_selection(*args):
        result = select_setup(*args)
        clock[0] += 31
        return result

    monkeypatch.setattr(auto_paper, "select_for_auto", slow_selection)
    auto_tick()
    with Session.begin() as session:
        state = session.get(AutoPaperState, 1)
        assert state.enabled
        assert "Giá MEXC đã chậm" in state.status
        assert session.get(AutoPaperDecision, ("BTC_USDT", last)) is None
        assert session.scalar(select(func.count()).select_from(PaperTrade)) == 0
        wallet = session.get(PaperWallet, 1)
        assert wallet.balance == 10000
        wallet.quote = {**wallet.quote, "time": clock[0], "received_at": clock[0]}
    monkeypatch.setattr(auto_paper, "select_for_auto", select_setup)
    auto_tick()
    auto_tick()
    with Session() as session:
        assert session.get(AutoPaperState, 1).enabled
        assert session.get(AutoPaperDecision, ("BTC_USDT", last)).action == "opened"
        assert session.scalar(select(func.count()).select_from(PaperTrade)) == 1

@pytest.mark.parametrize("failure", [ValueError("unexpected failure"), HTTPException(503, "unavailable")])
def test_unexpected_entry_failures_still_reach_worker(ready, monkeypatch, failure):
    from backend.app import auto_paper

    _, seed = ready
    seed()

    def fail(*args, **kwargs):
        raise failure

    monkeypatch.setattr(auto_paper, "execution_price", fail)
    with pytest.raises(type(failure)):
        auto_tick()

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
        seed("long")
        auto_tick()
        clock[0] += 900
    with Session() as session:
        trades = session.scalars(select(PaperTrade).where(PaperTrade.exit_time.is_(None))).all()
        assert len(trades) == 4
        assert len({t.id for t in trades}) == 4
        # All entered under the same breakout strategy
        assert all(t.origin == "auto" for t in trades)


def test_timeline_counts_actions_and_unrecorded_candles(ready):
    clock, seed = ready
    last = seed()
    with Session.begin() as session:
        session.add(AutoPaperDecision(symbol="BTC_USDT", candle_time=last - 1800, action="no_setup",
                                      reason="quiet", parameters={}))
        session.add(AutoPaperDecision(symbol="BTC_USDT", candle_time=last - 900, action="missed",
                                      reason="Auto Paper đang tắt", parameters={}))
        session.add(AutoPaperDecision(symbol="BTC_USDT", candle_time=last - 97 * 900,
                                      action="skipped", reason="too old", parameters={}))
    auto_tick()
    state = client.get("/api/paper/auto").json()
    assert [row["action"] for row in state["timeline"]] == ["opened", "missed", "no_setup"]
    assert state["timeline"][0]["trade_id"] and state["timeline"][0]["side"] == "long"
    assert state["coverage"] == {"window_candles": 96, "end_time": last,
                                 "start_time": last - 95 * 900, "tracked_candles": 96,
                                 "evaluated": 2, "unrecorded": 93, "opened": 1, "skipped": 0,
                                 "no_setup": 1, "missed": 1, "pending": 0}


def test_coverage_starts_at_first_record_for_new_pairs(ready):
    clock, seed = ready
    last = seed()
    assert client.get("/api/paper/auto").json()["coverage"]["tracked_candles"] == 0
    with Session.begin() as session:
        session.add(AutoPaperDecision(symbol="BTC_USDT", candle_time=last - 3 * 900,
                                      action="no_setup", reason="quiet", parameters={}))
    auto_tick()
    coverage = client.get("/api/paper/auto").json()["coverage"]
    assert (coverage["start_time"], coverage["tracked_candles"]) == (last - 3 * 900, 4)
    assert (coverage["evaluated"], coverage["unrecorded"]) == (2, 2)


def test_disabled_or_blocked_candles_are_recorded_as_missed_after_window(ready):
    clock, seed = ready
    last = seed(enabled=False)
    auto_tick()
    with Session() as session:
        assert session.get(AutoPaperDecision, ("BTC_USDT", last)) is None
    clock[0] += 61
    auto_tick()
    with Session() as session:
        decision = session.get(AutoPaperDecision, ("BTC_USDT", last))
        assert (decision.action, decision.reason) == ("missed", "Auto Paper đang tắt")

    clock[0] += 900
    last = seed()
    with Session.begin() as session:
        wallet = session.get(PaperWallet, 1)
        wallet.quote = {**wallet.quote, "received_at": clock[0] - 21}
    clock[0] += 61
    auto_tick()
    with Session() as session:
        decision = session.get(AutoPaperDecision, ("BTC_USDT", last))
        assert decision.action == "missed" and decision.reason
        assert session.scalar(select(func.count()).select_from(PaperTrade)) == 0


@pytest.mark.parametrize("side,level", [("long", 100.0), ("short", 90.0)])
def test_limit_entry_queues_then_fills_at_level(ready, side, level):
    from backend.app.auto_paper import sync_pending_orders
    from backend.app.db import PaperPendingOrder
    clock, seed = ready
    last = seed(side, policy="body50")
    auto_tick()
    with Session() as session:
        order = session.scalar(select(PaperPendingOrder))
        assert (order.status, order.level, order.entry_policy) == ("pending", level, "body50")
        assert session.get(AutoPaperDecision, ("BTC_USDT", last)).action == "pending"
        assert session.scalar(select(func.count()).select_from(PaperTrade)) == 0
    assert client.get("/api/paper/wallet").json()["pending_orders"][0]["level"] == level
    # Next bar trades through the level: fills at 100 (no slippage), position opens.
    clock[0] += 900
    with Session.begin() as session:
        save_candles(session, [Candle(last + 900, 105, 106, 99, 104, 10) if side == "long"
                               else Candle(last + 900, 85, 91, 84, 86, 10)])
        wallet = session.get(PaperWallet, 1)
        wallet.quote = {**wallet.quote, "time": clock[0], "received_at": clock[0]}
        session.get(MarketState, 1).last_sync = datetime.fromtimestamp(clock[0], timezone.utc)
    sync_pending_orders()
    with Session() as session:
        order = session.scalar(select(PaperPendingOrder))
        trade = session.get(PaperTrade, order.trade_id)
        assert order.status == "filled" and trade.entry == level and trade.entry_time == last + 900
        assert session.get(AutoPaperDecision, ("BTC_USDT", last)).action == "opened"
    sync_pending_orders()
    with Session() as session:
        assert session.scalar(select(func.count()).select_from(PaperTrade)) == 1

def test_limit_entry_expires_untouched(ready):
    from backend.app.auto_paper import sync_pending_orders
    from backend.app.db import PaperPendingOrder
    clock, seed = ready
    last = seed("long", policy="body50")
    auto_tick()
    clock[0] += 5 * 900
    with Session.begin() as session:
        save_candles(session, [Candle(last + 900 * i, 105, 106, 101, 104, 10) for i in range(1, 6)])
        wallet = session.get(PaperWallet, 1)
        wallet.quote = {**wallet.quote, "time": clock[0], "received_at": clock[0]}
        session.get(MarketState, 1).last_sync = datetime.fromtimestamp(clock[0], timezone.utc)
    sync_pending_orders()
    with Session() as session:
        assert session.scalar(select(PaperPendingOrder.status)) == "expired"
        assert session.scalar(select(func.count()).select_from(PaperTrade)) == 0

@pytest.mark.parametrize("case", ["touch", "target", "stop_first", "old", "gap", "fourth", "stale", "risk", "funding"])
def test_pending_execution_boundaries(ready, case):
    from backend.app.auto_paper import sync_pending_orders
    from backend.app.db import PaperPendingOrder
    clock, seed = ready
    last = seed("long", policy="body50")
    auto_tick()
    auto_tick()
    with Session() as session:
        assert session.scalar(select(func.count()).select_from(PaperPendingOrder)) == 1
    clock[0] += 4 * 900 if case == "fourth" else 900
    if case == "old":
        clock[0] += 61
    bar = Candle(last + 900, 105, 106, 100, 104, 10)
    if case in ("stale", "old", "funding", "risk"):
        bar = Candle(last + 900, 105, 106, 99, 104, 10)
    elif case == "target":
        bar = Candle(last + 900, 105, 126, 101, 124, 10)
    elif case == "stop_first":
        bar = Candle(last + 900, 105, 126, 94, 125.5, 10)
    with Session.begin() as session:
        if case == "gap":
            save_candles(session, [Candle(last + 1800, 105, 106, 99, 104, 10)])
        elif case == "fourth":
            save_candles(session, [Candle(last + 900 * index, 105, 106, 101, 104, 10) for index in range(1, 4)]
                         + [Candle(last + 3600, 105, 106, 99, 104, 10)])
        else:
            save_candles(session, [bar])
        wallet = session.get(PaperWallet, 1)
        if case != "stale":
            wallet.quote = {**wallet.quote, "time": clock[0], "received_at": clock[0]}
        if case == "funding":
            wallet.funding_error = "test funding unavailable"
        if case == "risk":
            session.get(AutoPaperState, 1).max_portfolio_risk_percent = 0
    sync_pending_orders()
    with Session() as session:
        order = session.scalar(select(PaperPendingOrder))
        expected = {"touch": "pending", "gap": "pending", "stale": "pending", "funding": "pending",
                    "old": "cancelled", "target": "cancelled", "risk": "cancelled",
                    "stop_first": "filled", "fourth": "filled"}[case]
        assert order.status == expected
        if case in ("stale", "funding", "gap"):
            assert order.next_bar == last + 900
        if case == "stop_first":
            trade = session.get(PaperTrade, order.trade_id)
            assert trade.exit_reason == "recovered_stop_loss" and trade.net_pnl < 0
            assert trade.exit == pytest.approx(95 * 0.9998)
        if order.status != "filled":
            assert session.scalar(select(func.count()).select_from(PaperTrade)) == 0

@pytest.mark.parametrize("cancel", ["endpoint", "auto_off", "worker_off"])
def test_pending_cancel_is_scoped_and_idempotent(ready, cancel):
    from backend.app.auto_paper import sync_pending_orders
    from backend.app.db import PaperPendingOrder
    clock, seed = ready
    seed(policy="body50")
    auto_tick()
    order = client.get("/api/paper/wallet").json()["pending_orders"][0]
    assert client.post(f"/api/paper/auto/pending/{order['id']}/cancel",
                       headers={"X-Futures-Symbol": "ETH_USDT"}).status_code == 404
    if cancel == "endpoint":
        for _ in range(2):
            response = client.post(f"/api/paper/auto/pending/{order['id']}/cancel")
            assert response.status_code == 200 and response.json()["status"] == "cancelled"
    elif cancel == "auto_off":
        assert client.post("/api/paper/auto/control", json={"enabled": False}).status_code == 200
    else:
        with Session.begin() as session:
            session.get(AutoPaperState, 1).enabled = False
        sync_pending_orders()
    with Session() as session:
        assert session.scalar(select(PaperPendingOrder.status)) == "cancelled"
        assert session.scalar(select(func.count()).select_from(PaperTrade)) == 0

@pytest.mark.parametrize("confirm", [False, True])
def test_pending_retest_waits_for_close_then_market(ready, confirm):
    from backend.app.auto_paper import sync_pending_orders
    from backend.app.db import PaperPendingOrder
    clock, seed = ready
    last = seed(policy="body50")
    auto_tick()
    with Session.begin() as session:
        session.scalar(select(PaperPendingOrder)).entry_policy = "retest_h1"
    clock[0] += 900
    with Session.begin() as session:
        save_candles(session, [Candle(last + 900, 100.5, 104, 99, 102 if confirm else 99.5, 10)])
        wallet = session.get(PaperWallet, 1)
        wallet.quote = {**wallet.quote, "time": clock[0], "received_at": clock[0]}
    sync_pending_orders()
    with Session() as session:
        order = session.scalar(select(PaperPendingOrder))
        assert order.status == ("filled" if confirm else "cancelled")
        if confirm:
            trade = session.get(PaperTrade, order.trade_id)
            assert trade.entry == pytest.approx(105.1 * 1.0002)
            assert trade.entry_time == clock[0]
            assert trade.monitoring["entry_mode"] == "retest_h1"

def test_pending_fill_rechecks_daily_limit(ready):
    from backend.app.auto_paper import sync_pending_orders
    from backend.app.db import PaperPendingOrder
    clock, seed = ready
    last = seed(policy="body50")
    auto_tick()
    clock[0] += 900
    with Session.begin() as session:
        save_candles(session, [Candle(last + 900, 105, 106, 99, 104, 10)])
        wallet = session.get(PaperWallet, 1)
        wallet.quote = {**wallet.quote, "time": clock[0], "received_at": clock[0]}
        wallet.balance = 1
    sync_pending_orders()
    with Session() as session:
        order = session.scalar(select(PaperPendingOrder))
        assert order.status == "cancelled" and "lỗ ngày" in order.reason

def test_pending_runner_does_not_close_at_reference_target(ready):
    from backend.app.auto_paper import sync_pending_orders
    from backend.app.db import PaperPendingOrder
    clock, seed = ready
    last = seed(policy="body50")
    auto_tick()
    clock[0] += 900
    with Session.begin() as session:
        order = session.scalar(select(PaperPendingOrder))
        order.snapshot = {**order.snapshot, "exit_policy": "runner"}
        save_candles(session, [Candle(last + 900, 105, 126, 99, 126, 10)])
        wallet = session.get(PaperWallet, 1)
        wallet.quote = {**wallet.quote, "time": clock[0], "received_at": clock[0]}
    sync_pending_orders()
    with Session() as session:
        order = session.scalar(select(PaperPendingOrder))
        trade = session.get(PaperTrade, order.trade_id)
        assert order.status == "filled" and trade.exit_time is None

def test_pending_retry_does_not_lose_uncommitted_fill_bar(ready, monkeypatch):
    from backend.app import auto_paper
    from backend.app.db import PaperPendingOrder
    clock, seed = ready
    last = seed(policy="body50")
    auto_tick()
    clock[0] += 900
    with Session.begin() as session:
        save_candles(session, [Candle(last + 900, 105, 106, 99, 104, 10)])
        wallet = session.get(PaperWallet, 1)
        wallet.quote = {**wallet.quote, "time": clock[0], "received_at": clock[0]}
    original = auto_paper.create_position
    def unavailable(*args, **kwargs):
        raise HTTPException(409, "Waiting for metadata")
    monkeypatch.setattr(auto_paper, "create_position", unavailable)
    auto_paper.sync_pending_orders()
    with Session() as session:
        order = session.scalar(select(PaperPendingOrder))
        assert order.status == "pending" and order.next_bar == last + 900
    monkeypatch.setattr(auto_paper, "create_position", original)
    auto_paper.sync_pending_orders()
    with Session() as session:
        assert session.scalar(select(PaperPendingOrder.status)) == "filled"
