import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.app.db import BotState, Session, Signal
from backend.app.main import app
from backend.app.market import Candle, DemoAdapter
from backend.app.strategy import breakout
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
