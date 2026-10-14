from types import SimpleNamespace

import pytest

from backend.app.forex_strategies import range_rejection, strategy_snapshot, trend_pullback


def bar(stamp, opening, high, low, close):
    return SimpleNamespace(time=stamp, open=opening, high=high, low=low, close=close)


def test_trend_pullback_long_and_no_lookahead():
    hours = [bar(i * 3600, 100 + i, 101 + i, 99 + i, 100.5 + i) for i in range(60)]
    t = 60 * 3600
    minutes = [bar(t - 900 * (30 - i), 158, 160, 157, 159) for i in range(30)]
    sig = trend_pullback(bar(t, 158, 159.5, 150, 159.4), hours, minutes)
    assert sig and sig["side"] == "long" and sig["stop"] == 150
    assert strategy_snapshot("trend_pullback", bar(t, 158, 159.5, 150, 159.4), hours, minutes)["status"] == "confirmed"
    assert trend_pullback(bar(t, 158, 159.5, 150, 159.4), hours[:49], minutes) is None


def test_range_rejection_short():
    hours = [bar(i * 3600, 100, 110 if i == 3 else 105, 90 if i == 5 else 95, 100) for i in range(20)]
    t = 20 * 3600
    sig = range_rejection(bar(t, 109, 110, 104, 105), hours, [])
    assert sig and sig["side"] == "short" and sig["stop"] == 110


def test_shadow_wallets_start_without_trades_and_do_not_touch_live():
    from backend.app.db import ForexPaperAccount, Session
    from backend.app.exness_feed import WORKSPACE
    from backend.app.forex_paper import _strategy_paper_tick

    bids = {t: bar(t, 100, 101, 99, 100) for t in (0, 900)}
    asks = bids
    with Session.begin() as session:
        out = _strategy_paper_tick(session, {"1h": []}, bids, asks, 2000)
        assert out == {"trend": 0, "range": 0}
        assert session.get(ForexPaperAccount, (WORKSPACE, "trend")).balance == 10000
        assert session.get(ForexPaperAccount, (WORKSPACE, "live")) is None


def test_trend_short_and_snapshot_excludes_future_candles():
    hours = [bar(index * 3600, 200 - index, 201 - index, 199 - index, 200 - index) for index in range(60)]
    stamp = 60 * 3600
    minutes = [bar(stamp - 900 * (30 - index), 142, 143, 140, 141) for index in range(30)]
    candle = bar(stamp, 142, 150, 140, 140.5)
    snapshot = strategy_snapshot("trend_pullback", candle, hours, minutes)
    assert snapshot["side"] == "short"
    assert snapshot["stop"] == 150
    future = bar(stamp + 900, 10000, 10001, 9999, 10000)
    assert snapshot == strategy_snapshot("trend_pullback", candle, hours + [future], minutes + [future])
    assert strategy_snapshot("trend_pullback", candle, hours, minutes[:-1])["status"] == "data_paused"


def test_range_long_and_trending_market_does_not_trade():
    hours = [bar(index * 3600, 100, 110, 90, 100) for index in range(20)]
    candle = bar(20 * 3600, 91, 96, 90, 95)
    signal = range_rejection(candle, hours, [])
    assert signal["side"] == "long" and signal["stop"] == 90
    trending = [bar(index * 3600, 100 + index, 101 + index, 99 + index, 100 + index) for index in range(20)]
    assert range_rejection(candle, trending, []) is None


def test_shadow_wallet_catchup_is_idempotent_and_keeps_strategies_isolated(monkeypatch):
    from sqlalchemy import select

    from backend.app import forex_paper
    from backend.app.db import ForexPaperAccount, ForexPaperTrade, Session
    from backend.app.exness_feed import WORKSPACE

    def signal(strategy, candle, hours, minutes):
        return {"strategy": strategy, "candle_time": candle.time, "status": "confirmed",
                "reason": "test", "side": "long", "stop": 98}

    monkeypatch.setattr(forex_paper, "strategy_snapshot", signal)
    bids = {0: bar(0, 100, 101, 99, 100)}
    with Session.begin() as session:
        forex_paper._strategy_paper_tick(session, {"1h": []}, bids, bids, 900)
        bids[900] = bar(900, 100, 101, 99, 100)
        bids[1800] = bar(1800, 100, 101, 99, 100)
        assert forex_paper._strategy_paper_tick(session, {"1h": []}, bids, bids, 4000) == {"trend": 2, "range": 2}
        assert forex_paper._strategy_paper_tick(session, {"1h": []}, bids, bids, 4000) == {"trend": 0, "range": 0}
        for channel in ("trend", "range"):
            rows = session.scalars(select(ForexPaperTrade).where(ForexPaperTrade.channel == channel)
                                   .order_by(ForexPaperTrade.confirm_time)).all()
            assert [row.snapshot["status"] for row in rows] == ["open", "skipped"]
            assert rows[-1].snapshot["reason"] == "position_already_active"
            assert session.get(ForexPaperAccount, (WORKSPACE, channel)).last_candle == 1800
        assert session.get(ForexPaperAccount, (WORKSPACE, "live")) is None
