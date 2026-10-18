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
        assert forex["paper"]["status"] == "not_started"
        assert [row["channel"] for row in forex["paper"]["portfolios"]] == ["live", "replay", "trend", "range", "router"]
        assert "signals" not in forex
        assert client.get("/api/workspaces/mexc_futures/dashboard").json()["bot"]["enabled"] is False
    with Session() as session:
        assert session.query(PaperWallet).count() == 10
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
        for timestamp in range(1800, 1800 + 3 * 900, 900):
            with Session.begin() as session:
                session.add(WorkspaceCandle(workspace_id="exness_forex", symbol="XAUUSD",
                                     timeframe="15m", price_type="bid", time=timestamp,
                                     open=2000, high=2000, low=2000, close=2000))
        first = client.get("/api/workspaces/exness_forex/candles?price_type=bid&limit=2").json()
        assert [bar["time"] for bar in first["candles"]] == [2700, 3600]
        assert first["has_more"] is True
        assert first["next_before"] == 2700
        second = client.get(f"/api/workspaces/exness_forex/candles?price_type=bid&limit=2&before={first['next_before']}").json()
        assert [bar["time"] for bar in second["candles"]] == [900, 1800]
        assert second["has_more"] is False
        assert second["next_before"] is None
        assert client.get("/api/workspaces/exness_forex/candles?before=900").json()["candles"] == []


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
    "/api/workspaces/exness_forex/candles?before=0",
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

def test_futures_pairs_have_isolated_wallets_and_reject_unknown_symbol():
    with TestClient(app) as client:
        assert len(client.get("/api/workspaces/mexc_futures/symbols").json()["symbols"]) == 10
        assert client.get("/api/paper/wallet", headers={"X-Futures-Symbol": "XAU_USDT"}).status_code == 422
        assert client.get("/api/paper/wallet", headers={"X-Futures-Symbol": "ETH_USDT"}).json()["symbol"] == "ETH_USDT"

def test_mexc_candles_backfill_older_page_into_db(monkeypatch):
    import backend.app.main as main
    from backend.app.market import Candle
    from backend.app.mexc import SOURCE

    now, calls = 1_800_000_000, []

    class Fake:
        def server_time(self): return now
        def close(self): pass
        def history(self, start, end, server_time):
            calls.append((start, end))
            return [Candle(t, 1, 2, 0.5, 1.5, 1) for t in range(start, end, 900)]

    monkeypatch.setattr(main, "MexcFuturesAdapter", Fake)
    oldest = now // 900 * 900 - 10 * 900
    with Session.begin() as session:
        session.query(MarketCandle).delete()
        session.add(MarketCandle(source=SOURCE, symbol=main.market_symbol(), timeframe="15m",
                                 time=oldest, open=1, high=2, low=0.5, close=1.5, volume=1))
    with TestClient(app) as client:
        page = client.get(f"/api/workspaces/mexc_futures/candles?limit=50&before={oldest + 900}").json()
        assert calls == [(oldest - main.BACKFILL_BARS * 900, oldest)]
        assert len(page["candles"]) == 50 and page["has_more"] is True
        assert all(bar["time"] >= oldest - 49 * 900 for bar in page["candles"])
        client.get(f"/api/workspaces/mexc_futures/candles?limit=50&before={oldest + 900}")
        assert len(calls) == 1  # enough stored history: no second MEXC call
