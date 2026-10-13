import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from backend.app.db import BotState, MarketCandle, PaperWallet, Session, WorkspaceCandle
from backend.app.main import app


def test_workspaces_seeded_without_changing_mexc_wallet():
    with TestClient(app) as client:
        rows = client.get("/api/workspaces").json()["workspaces"]
        assert {row["id"] for row in rows} == {"mexc_futures", "exness_forex"}
        assert all(not row["execution_enabled"] for row in rows)
        forex = client.get("/api/workspaces/exness_forex/dashboard").json()
        assert forex["workspace"]["symbol"] == "XAUUSD"
        assert forex["mode"] == "demo_read_only"
        assert forex["paper"]["status"] == "unavailable"
        assert "balance" not in forex["paper"]
        assert "signals" not in forex
        assert client.get("/api/workspaces/mexc_futures/dashboard").json()["bot"]["enabled"] is False
    with Session() as session:
        assert session.query(PaperWallet).count() == 1
        assert session.get(PaperWallet, 1).balance == 10000


def test_workspace_candles_isolate_venue_symbol_and_price_side():
    with Session.begin() as session:
        session.add(MarketCandle(source="mexc_futures", symbol="BTC/USDT:USDT", timeframe="15m",
                                 time=900, open=100, high=102, low=99, close=101, volume=10))
        for symbol, price_type, price in (("XAUUSD", "bid", 2000), ("XAUUSD", "ask", 2001),
                                          ("EURUSD", "bid", 1)):
            session.add(WorkspaceCandle(workspace_id="exness_forex", symbol=symbol,
                                         timeframe="15m", price_type=price_type, time=900,
                                         open=price, high=price, low=price, close=price))
    with TestClient(app) as client:
        mexc = client.get("/api/workspaces/mexc_futures/candles").json()
        assert mexc["price_type"] == "last"
        assert [row["close"] for row in mexc["candles"]] == [101]
        for price_type, price in (("bid", 2000), ("ask", 2001)):
            response = client.get(f"/api/workspaces/exness_forex/candles?price_type={price_type}")
            assert response.status_code == 200
            assert [row["close"] for row in response.json()["candles"]] == [price]
            assert "volume" not in response.json()["candles"][0]
        assert client.get("/api/workspaces/exness_forex/candles?timeframe=1h").json()["candles"] == []


@pytest.mark.parametrize("path", [
    "/api/workspaces/missing/dashboard", "/api/workspaces/missing/candles",
])
def test_unknown_workspace_is_not_mexc_fallback(path):
    with TestClient(app) as client:
        assert client.get(path).status_code == 404


@pytest.mark.parametrize("path", [
    "/api/workspaces/exness_forex/candles?price_type=last",
    "/api/workspaces/mexc_futures/candles?price_type=ask",
    "/api/workspaces/exness_forex/candles?limit=0",
    "/api/workspaces/exness_forex/candles?timeframe=5m",
])
def test_invalid_workspace_candle_request_rejected(path):
    with TestClient(app) as client:
        assert client.get(path).status_code == 422


@pytest.mark.parametrize("scope", [
    {"params": {"workspace_id": "exness_forex"}},
    {"headers": {"X-Workspace-ID": "exness_forex"}},
])
def test_exness_cannot_control_legacy_mexc_bot(scope):
    with TestClient(app) as client:
        assert client.post("/api/bot/control", json={"enabled": True}, **scope).status_code == 409
        for path in ("/api/dashboard", "/api/paper", "/api/observations/mtf"):
            assert client.get(path, **scope).status_code == 409
    with Session() as session:
        assert session.get(BotState, 1).enabled is False


def test_workspace_candle_rejects_invalid_price_type():
    with pytest.raises(IntegrityError), Session.begin() as session:
        session.add(WorkspaceCandle(workspace_id="exness_forex", symbol="XAUUSD",
                                     timeframe="15m", price_type="last", time=900,
                                     open=2000, high=2000, low=2000, close=2000))
