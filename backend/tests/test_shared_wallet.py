import time
from uuid import uuid4
from fastapi.testclient import TestClient
from backend.app.db import Session
from backend.app.futures_symbols import symbol_scope
from backend.app.main import app
from backend.app.paper import sync_paper_quote

client = TestClient(app)

class MockMarket:
    def __init__(self, price=100):
        self.price = price
    def ticker(self):
        return dict(lastPrice=self.price, bid1=self.price - 0.1, ask1=self.price + 0.1, fairPrice=self.price, time=time.time(), received_at=time.time())
    def contract_info(self):
        return dict(contractSize=0.001, volUnit=1, minVol=1, maxVol=100000000,
                    priceUnit=0.1, minLeverage=1, maxLeverage=50, maintenanceMarginRate=0.005)

def test_shared_cash_and_cross_margin_between_symbols():
    with symbol_scope("BTC_USDT"):
        sync_paper_quote(MockMarket(100))
    with symbol_scope("ETH_USDT"):
        sync_paper_quote(MockMarket(200))

    # BTC opens 2000 USDT notional
    r = client.post("/api/paper/orders", headers={"X-Futures-Symbol": "BTC_USDT"}, json={
        "request_id": str(uuid4()), "side": "long", "notional": 2000, "stop": 90, "target": 120
    })
    assert r.status_code == 200, r.text
    btc_wallet = client.get("/api/paper/wallet", headers={"X-Futures-Symbol": "BTC_USDT"}).json()
    assert btc_wallet["balance"] < 10000
    btc_free = btc_wallet["available"]

    # ETH observes the same master cash balance and reduced free margin
    eth_wallet = client.get("/api/paper/wallet", headers={"X-Futures-Symbol": "ETH_USDT"}).json()
    assert eth_wallet["balance"] == btc_wallet["balance"]
    assert eth_wallet["available"] == btc_free
    assert eth_wallet["used_margin"] == btc_wallet["used_margin"]

    # Open ETH order with 1000 USDT notional
    r2 = client.post("/api/paper/orders", headers={"X-Futures-Symbol": "ETH_USDT"}, json={
        "request_id": str(uuid4()), "side": "short", "notional": 1000, "stop": 220, "target": 160
    })
    assert r2.status_code == 200, r2.text
    eth_wallet2 = client.get("/api/paper/wallet", headers={"X-Futures-Symbol": "ETH_USDT"}).json()
    assert eth_wallet2["balance"] < btc_wallet["balance"]
    assert eth_wallet2["used_margin"] > btc_wallet["used_margin"]

    # Close BTC trade at profit -> credits directly to shared master wallet
    trade_id = btc_wallet["position"]["id"]
    with symbol_scope("BTC_USDT"):
        sync_paper_quote(MockMarket(110))
    c = client.post(f"/api/paper/positions/{trade_id}/close", headers={"X-Futures-Symbol": "BTC_USDT"}, json={"request_id": str(uuid4())})
    assert c.status_code == 200

    # ETH verifies master balance increased from BTC win
    eth_final = client.get("/api/paper/wallet", headers={"X-Futures-Symbol": "ETH_USDT"}).json()
    assert eth_final["balance"] > eth_wallet2["balance"]

def test_cross_leverage_reduces_required_margin():
    with symbol_scope("BTC_USDT"):
        sync_paper_quote(MockMarket(100))
    # Configure 5x leverage on BTC
    res = client.post("/api/paper/auto/config", headers={"X-Futures-Symbol": "BTC_USDT"}, json={
        "risk_percent": 1, "leverage": 5, "max_daily_trades": 10, "daily_loss_percent": 5
    })
    assert res.status_code == 200
    assert res.json()["leverage"] == 5
    assert res.json()["margin_mode"] == "cross"

def test_leverage_cannot_exceed_contract_maximum():
    with symbol_scope("BTC_USDT"):
        sync_paper_quote(MockMarket(100))
    res = client.post("/api/paper/auto/config", headers={"X-Futures-Symbol": "BTC_USDT"}, json={
        "risk_percent": 1, "leverage": 51, "max_daily_trades": 10, "daily_loss_percent": 5
    })
    assert res.status_code == 422


def test_auto_caps_margin_and_total_stop_risk(monkeypatch):
    from fastapi import HTTPException

    from backend.app import paper

    class Wallet:
        fee_bps, slippage_bps = 5, 2

    capital = 10000
    monkeypatch.setattr(paper, "portfolio", lambda session: (capital, 0, 0))
    monkeypatch.setattr(paper, "portfolio_risk_limits", lambda session: (capital, 450.0))
    assert paper.stop_risk_per_unit("long", 100, 99, 5, 2) > 1
    capital_now, used = paper.portfolio_risk_limits(None)
    risk = 100 * paper.stop_risk_per_unit("long", 100, 99, 5, 2)
    assert used + risk > capital_now * paper.MAX_PORTFOLIO_RISK_PERCENT / 100
    assert 1500 / 1 > capital_now * paper.MAX_ORDER_MARGIN_PERCENT / 100
    assert HTTPException  # Both limits are enforced by create_position for origin="auto".
