import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.app.db import BotState, Session, Signal
from backend.app.main import app
from backend.app.market import Candle, DemoAdapter
from backend.app.strategy import breakout, retest
from bot.worker import tick


def test_breakout_uses_previous_bars_and_risk():
    history = [Candle(i, 95, 100, 90, 96, 10) for i in range(20)]
    current = Candle(20, 96, 110, 95, 105, 10)
    setup = breakout(history + [current])
    assert setup.entry == 105 and setup.stop == 90 and setup.target == 135
    # Current high is not accidentally included in breakout resistance.
    assert breakout(history + [Candle(20, 96, 110, 95, 99, 10)]) is None
    assert breakout(history) is None


def test_default_stopped_heartbeat_and_persistent_control():
    with TestClient(app) as client:
        assert client.get("/api/health").status_code == 200
        before = client.get("/api/dashboard").json()
        assert not before["bot"]["enabled"]
        tick()
        assert client.get("/api/dashboard").json()["bot"]["last_bar"] == 60
        assert client.post("/api/bot/control", json={"enabled": True}).status_code == 200
        tick()
        after = client.get("/api/dashboard").json()
        assert after["bot"]["enabled"] and after["bot"]["online"]
        assert after["bot"]["last_bar"] == 61
        client.post("/api/bot/control", json={"enabled": False})
        tick()
        assert client.get("/api/dashboard").json()["bot"]["last_bar"] == 61
        # New session observes persisted stopped state.
        with Session() as session:
            assert not session.get(BotState, 1).enabled


def test_replay_records_unique_signals_and_stops_at_end():
    with Session.begin() as session:
        session.get(BotState, 1).enabled = True
    for _ in range(100):
        tick()
    with Session() as session:
        signals = session.scalars(select(Signal)).all()
        assert len(signals) > 0
        assert len({s.candle_time for s in signals}) == len(signals)
        assert all(s.stop < s.entry < s.target for s in signals)
    with Session.begin() as session:
        session.get(BotState, 1).last_bar = 2000
    tick()
    with Session() as session:
        assert not session.get(BotState, 1).enabled


def test_live_mode_is_rejected(monkeypatch):
    from backend.app import settings

    monkeypatch.setattr(settings, "TRADING_MODE", "live")
    with pytest.raises(RuntimeError, match="Live trading is disabled"):
        settings.validate_mode()


def test_invalid_control_and_stable_replay():
    with TestClient(app) as client:
        assert (
            client.post("/api/bot/control", json={"enabled": True, "mode": "live"}).status_code
            == 422
        )
    assert DemoAdapter().candles(end=80)[-1] == DemoAdapter().candles(end=81)[-2]


def test_retest_waits_for_retest_and_confirmation():
    h = [Candle(i, 95, 100, 90, 96, 10) for i in range(20)]
    brk = Candle(20, 96, 110, 95, 105, 10)
    ret = Candle(21, 105, 106, 99, 101, 10)  # touches 100, holds, bearish
    ok = Candle(22, 101, 108, 100, 107, 10)
    assert retest(h + [brk]) is None and retest(h + [brk, ret]) is None
    s = retest(h + [brk, ret, ok])
    assert s.side == "long" and s.entry == 107 and s.stop == 99 and s.target == 123
    assert retest(h + [brk, ret, Candle(22, 101, 102, 97, 98, 10)]) is None  # lost zone
    assert retest(h + [brk, ret, ok, Candle(23, 107, 109, 106, 108, 10)]) is None  # no repeat


def test_retest_short_expiry_and_no_lookahead():
    h = [Candle(i, 95, 100, 90, 96, 10) for i in range(20)]
    brk = Candle(20, 94, 95, 80, 85, 10)  # closes below 90
    ret = Candle(21, 85, 91, 84, 89, 10)  # touches 90, closes back below
    ok = Candle(22, 89, 89, 80, 82, 10)
    s = retest(h + [brk, ret, ok], allow_short=True)
    assert s.side == "short" and s.entry == 82 and s.stop == 91 and s.target == 64
    assert retest(h + [brk, ret, ok]) is None  # long-only ignores shorts
    # Later candles must not change an earlier verdict (no lookahead).
    assert retest((h + [brk, ret, ok, Candle(23, 82, 120, 70, 110, 10)])[:23], allow_short=True)
    # Retest after expiry is ignored.
    up = [Candle(20, 96, 110, 95, 105, 10)] + [
        Candle(21 + i, 105, 108, 102, 106, 10) for i in range(10)
    ]
    late = [Candle(31, 106, 106, 99, 101, 10), Candle(32, 101, 108, 100, 107, 10)]
    assert retest(h + up + late) is None


def test_walkforward_criteria():
    from scripts.walkforward import evaluate

    def win(pnl):
        return {
            "trades": [{"net_pnl": pnl}] * 50,
            "net_pnl": pnl,
            "max_drawdown_percent": 3,
            "return_percent": pnl,
        }

    assert evaluate([win(5), win(5), win(-1)])["pass"]
    assert not evaluate([win(1), win(-1)])["pass"]  # PF 1.0
    assert not evaluate([win(5)])["pass"]  # < 100 trades
