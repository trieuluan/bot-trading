import pytest
from fastapi.testclient import TestClient

from backend.app import forex_learning
from backend.app.db import ExnessExecutionState, Session
from backend.app.main import app


@pytest.mark.parametrize(("now", "capture", "count"), [
    (18000, {"status": "captured", "time": 17100, "confirmed": 3}, 3),
    (18120, {"status": "captured", "time": 17100, "confirmed": 3}, 3),
    (18121, {"status": "captured", "time": 17100, "confirmed": 3}, 0),
    (18900, {"status": "captured", "time": 17100, "confirmed": 3}, 0),
    (18000, {"status": "captured", "time": 18000, "confirmed": 3}, 0),
    (18000, {"status": "waiting_data", "time": 17100, "confirmed": 3}, 0),
    (18000, {"status": "captured", "time": 17100, "confirmed": 0}, 0),
    (18000, None, 0),
])
def test_signal_badges_use_only_fresh_capture_without_enabling_execution(monkeypatch, now, capture, count):
    monkeypatch.setenv("FOREX_HTF_V2_EXECUTION_ENABLED", "false")
    monkeypatch.setattr(forex_learning, "read_ledger", lambda *args: {"last_capture": capture})
    monkeypatch.setattr(forex_learning, "qualified_trees", lambda *args: [])
    status = forex_learning.execution_status("", now)
    assert status["signal_count"] == count
    assert status["enabled"] is False
    assert status["selected"] is None
    assert status["blocked_reason"] == "v2_execution_disabled"
    assert status["signal_expires_at"] == (18120 if count else None)


def test_ledger_error_clears_signal_status(monkeypatch):
    def broken(*args):
        raise ValueError("Invalid ledger")

    monkeypatch.setattr(forex_learning, "read_ledger", broken)
    status = forex_learning.execution_status("", 18000)
    assert status["signal_count"] == 0
    assert status["signal_available_at"] is None
    assert status["signal_expires_at"] is None


def test_markets_keep_signal_and_broker_position_counts_separate_per_symbol(monkeypatch):
    monkeypatch.setenv("EXNESS_SYMBOLS", "XAUUSD,EURUSD")
    monkeypatch.setenv("EXNESS_ACCOUNT_ID", "signal-test")
    monkeypatch.setenv("FOREX_HTF_V2_EXECUTION_ENABLED", "false")
    monkeypatch.setattr("backend.app.main.time.time", lambda: 18000)
    monkeypatch.setattr("backend.app.forex_costs.spec_summary", lambda *args: {"status": "stale"})
    monkeypatch.setattr("backend.app.main.feed_summary", lambda symbol: [])
    monkeypatch.setattr("backend.app.main.quotes.snapshot", lambda symbol: {})
    monkeypatch.setattr(forex_learning, "qualified_trees", lambda *args: [])
    monkeypatch.setattr(forex_learning, "read_ledger", lambda account, symbol: {
        "last_capture": {"status": "captured", "time": 17100,
                         "confirmed": 2 if symbol == "XAUUSD" else 0},
    })
    with Session.begin() as session:
        session.add(ExnessExecutionState(account="signal-test", enabled=False, started_at=17000,
                                        snapshot={"broker": {"positions": [
                                            {"instrument": "XAUUSD"}, {"instrument": "XAUUSD"},
                                            {"instrument": "EURUSD"},
                                        ]}}))
    response = TestClient(app).get("/api/workspaces/exness_forex/markets")
    assert response.status_code == 200
    data = response.json()
    markets = {market["symbol"]: market for market in data["markets"]}
    assert markets["XAUUSD"]["execution"]["signal_count"] == 2
    assert markets["EURUSD"]["execution"]["signal_count"] == 0
    assert markets["XAUUSD"]["positions"] == 2
    assert markets["EURUSD"]["positions"] == 1
    assert data["account_positions"] == 3
