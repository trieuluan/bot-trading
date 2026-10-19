import time
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from backend.app.db import PaperFunding, PaperTrade, PaperWallet, Session
from backend.app.main import app
from backend.app.market import Candle
from backend.app.mexc import MarketDataError, MexcFuturesAdapter
from backend.app.paper import sync_paper_funding, sync_paper_quote

client = TestClient(app)


class Market:
    def __init__(self, price=100, timestamp=None):
        self.price = price
        self.timestamp = timestamp or time.time()

    def ticker(self):
        return dict(
            lastPrice=self.price,
            bid1=self.price - 0.1,
            ask1=self.price + 0.1,
            fairPrice=self.price,
            time=self.timestamp,
            received_at=time.time(),
        )

    def contract_info(self):
        return dict(contractSize=0.001, volUnit=1, minVol=1, maxVol=100000000, priceUnit=0.1)


def order(side="long", **changes):
    data = dict(
        request_id=str(uuid4()),
        side=side,
        notional=1000,
        stop=90 if side == "long" else 110,
        target=120 if side == "long" else 80,
    )
    return {**data, **changes}


def test_wallet_long_costs_and_idempotent_commands():
    sync_paper_quote(Market())
    request = order()
    opened = client.post("/api/paper/orders", json=request)
    assert opened.status_code == 200
    assert client.post("/api/paper/orders", json=request).json() == opened.json()
    assert client.post("/api/paper/orders", json={**request, "notional": 2000}).status_code == 409
    wallet = client.get("/api/paper/wallet").json()
    trade = wallet["position"]
    assert trade["entry"] == pytest.approx(100.1 * 1.0002)
    assert trade["quantity"] == trade["contracts"] * 0.001
    assert wallet["balance"] == pytest.approx(10000 - trade["entry_fee"])
    assert wallet["available"] == pytest.approx(
        wallet["balance"] - trade["entry"] * trade["quantity"]
    )
    sync_paper_quote(Market(105))
    close_request = {"request_id": str(uuid4())}
    path = f"/api/paper/positions/{trade['id']}/close"
    assert client.post(path, json=close_request).status_code == 200
    balance = client.get("/api/paper/wallet").json()["balance"]
    assert client.post(path, json=close_request).status_code == 200
    final = client.get("/api/paper/wallet").json()
    assert final["balance"] == balance
    closed = final["trades"][0]
    gross = (104.9 * 0.9998 - trade["entry"]) * trade["quantity"]
    assert closed["net_pnl"] == pytest.approx(gross - closed["entry_fee"] - closed["exit_fee"])
    assert balance == pytest.approx(10000 + closed["net_pnl"])
    assert final["position"] is None


def test_short_gap_stop_and_restart_never_closes_twice():
    sync_paper_quote(Market())
    assert client.post("/api/paper/orders", json=order("short")).status_code == 200
    sync_paper_quote(Market(120))
    wallet = client.get("/api/paper/wallet").json()
    assert wallet["position"] is None
    trade = wallet["trades"][0]
    assert trade["exit_reason"] == "stop_loss"
    assert trade["exit"] > trade["stop"]  # Gap fill at ask, not an invented stop fill.
    assert trade["net_pnl"] < 0
    sync_paper_quote(Market(120))
    assert client.get("/api/paper/wallet").json()["balance"] == wallet["balance"]


def test_take_profit_stale_quotes_invalid_sizes_and_levels():
    sync_paper_quote(Market())
    for changes in ({"stop": 110}, {"notional": 11000}, {"notional": 0.001}, {"target": -1}):
        assert client.post("/api/paper/orders", json=order(**changes)).status_code == 422
    with Session.begin() as session:
        wallet = session.get(PaperWallet, 1)
        wallet.quote = {**wallet.quote, "received_at": time.time() - 21}
    sync_paper_quote(Market())
    assert client.post("/api/paper/orders", json=order()).status_code == 200
    sync_paper_quote(Market(125))
    assert client.get("/api/paper/wallet").json()["trades"][0]["exit_reason"] == "take_profit"


def test_funding_applies_once_including_late_post_to_closed_position():
    sync_paper_quote(Market())
    opened = client.post("/api/paper/orders", json=order()).json()
    settlement = int(time.time()) // 900 * 900 - 900
    with Session.begin() as session:
        trade = session.get(PaperTrade, opened["trade_id"])
        trade.entry_time = settlement - 10
        wallet = session.get(PaperWallet, 1)
        wallet.funding_cursor = settlement - 100
    client.post(
        f"/api/paper/positions/{opened['trade_id']}/close", json={"request_id": str(uuid4())}
    )

    class Funding:
        def funding_history(self, start, end):
            return [dict(time=settlement, rate=0.001)]

        def history(self, start, end, now, fair=False):
            return [Candle(start, 102, 102, 102, 102, 0)]

    before = client.get("/api/paper/wallet").json()
    now = settlement + 1800
    sync_paper_funding(Funding(), now)
    after = client.get("/api/paper/wallet").json()
    cost = before["trades"][0]["quantity"] * 102 * 0.001
    assert after["balance"] == pytest.approx(before["balance"] - cost)
    assert after["trades"][0]["net_pnl"] == pytest.approx(before["trades"][0]["net_pnl"] - cost)
    # Simulate retry/restart before cursor progress was observed by caller.
    with Session.begin() as session:
        wallet = session.get(PaperWallet, 1)
        wallet.funding_cursor = settlement - 100
        wallet.funding_synced_at = 0
    sync_paper_funding(Funding(), now)
    assert client.get("/api/paper/wallet").json()["balance"] == after["balance"]
    with Session() as session:
        assert session.scalar(select(func.count()).select_from(PaperFunding)) == 1


def test_failed_quote_keeps_position_and_rate_limit_cooldown():
    sync_paper_quote(Market())
    client.post("/api/paper/orders", json=order())

    class Failed(Market):
        def ticker(self):
            raise MarketDataError("Rate limited", 60)

    sync_paper_quote(Failed())
    wallet = client.get("/api/paper/wallet").json()
    assert wallet["stale"] and wallet["position"]
    assert (
        client.post(
            f"/api/paper/positions/{wallet['position']['id']}/close",
            json={"request_id": str(uuid4())},
        ).status_code
        == 409
    )
    # Should not request ticker during persisted cooldown.
    sync_paper_quote(Failed())
    assert client.get("/api/paper/wallet").json()["balance"] == wallet["balance"]


def test_ticker_validates_exchange_timestamp_and_book():
    def response(request):
        return httpx.Response(
            200,
            json={
                "success": True,
                "data": dict(
                    symbol="BTC_USDT",
                    lastPrice=100,
                    bid1=99,
                    ask1=101,
                    fairPrice=100,
                    timestamp=int((time.time() - 90) * 1000),
                ),
            },
        )

    adapter = MexcFuturesAdapter(
        httpx.Client(transport=httpx.MockTransport(response), base_url="https://test")
    )
    with pytest.raises(MarketDataError, match="stale"):
        adapter.ticker()


def test_short_receives_positive_funding_and_empty_interval_advances():
    sync_paper_quote(Market())
    opened = client.post("/api/paper/orders", json=order("short")).json()
    settlement = int(time.time()) // 900 * 900 - 900
    with Session.begin() as session:
        session.get(PaperTrade, opened["trade_id"]).entry_time = settlement - 1
        session.get(PaperWallet, 1).funding_cursor = settlement - 100

    class Funding:
        def funding_history(self, start, end):
            return [dict(time=settlement, rate=0.001)]

        def history(self, start, end, now, fair=False):
            return [Candle(start, 100, 100, 100, 100, 0)]

    before = client.get("/api/paper/wallet").json()
    sync_paper_funding(Funding(), settlement + 1800)
    after = client.get("/api/paper/wallet").json()
    assert after["balance"] > before["balance"]
    assert after["position"]["funding_cost"] < 0
    # No settlement in the next interval is valid, rather than missing data.
    sync_paper_funding(Funding(), settlement + 2700)
    final = client.get("/api/paper/wallet").json()
    assert final["balance"] == after["balance"]
    assert final["funding_cursor"] == settlement + 2700


def test_multiple_positions_reserve_margin_and_close_independently():
    sync_paper_quote(Market())
    first = client.post("/api/paper/orders", json=order()).json()["trade_id"]
    second = client.post("/api/paper/orders", json=order("short")).json()["trade_id"]
    wallet = client.get("/api/paper/wallet").json()
    assert {t["id"] for t in wallet["positions"]} == {first, second}
    assert wallet["used_margin"] == pytest.approx(sum(t["entry"] * t["quantity"] for t in wallet["positions"]))
    expected = sum((1 if t["side"] == "long" else -1) * t["quantity"] * (100 - t["entry"]) for t in wallet["positions"])
    assert wallet["unrealized_pnl"] == pytest.approx(expected)
    assert client.post("/api/paper/orders", json=order(notional=9000)).status_code == 422
    assert client.post(f"/api/paper/positions/{first}/close", json={"request_id": str(uuid4())}).status_code == 200
    assert [t["id"] for t in client.get("/api/paper/wallet").json()["positions"]] == [second]
    sync_paper_quote(Market(120))
    assert client.get("/api/paper/wallet").json()["positions"] == []


def test_unbounded_positions_and_all_stops_execute_once():
    sync_paper_quote(Market())
    # Enter 8 positions with same strategy/side without cap; bounded only by free balance.
    for _ in range(8):
        assert client.post("/api/paper/orders", json=order(notional=500)).status_code == 200
    wallet = client.get("/api/paper/wallet").json()
    assert len(wallet["positions"]) == 8
    sync_paper_quote(Market(125))
    wallet = client.get("/api/paper/wallet").json()
    assert wallet["positions"] == []
    assert all(t["exit_reason"] == "take_profit" for t in wallet["trades"])
    assert wallet["balance"] == pytest.approx(10000 + sum(t["net_pnl"] for t in wallet["trades"]))
    sync_paper_quote(Market(125))
    assert client.get("/api/paper/wallet").json()["balance"] == wallet["balance"]


def test_double_cross_uses_m1_order_else_stop_first(monkeypatch):
    from backend.app import paper
    trade = type("T", (), {"side": "long", "stop": 90, "target": 120, "symbol": "BTC_USDT"})
    candle = Candle(0, 100, 125, 85, 100, 1)
    bars = [Candle(0, 100, 101, 99, 100, 1), Candle(60, 100, 121, 99, 120, 1),
            Candle(120, 120, 120, 85, 90, 1)]

    class Adapter:
        def __init__(self, symbol=None): pass
        def history(self, *a, **k): return bars
        def close(self): pass

    monkeypatch.setattr(paper, "MexcFuturesAdapter", Adapter)
    kind, bar, ambiguous = paper.resolve_with_m1(trade, candle)
    assert (kind, bar.time, ambiguous) == ("target", 60, False)
    bars[1] = Candle(60, 100, 121, 85, 100, 1)
    assert paper.resolve_with_m1(trade, candle)[::2] == ("stop", True)

    class Down(Adapter):
        def history(self, *a, **k): raise MarketDataError("down")

    monkeypatch.setattr(paper, "MexcFuturesAdapter", Down)
    assert paper.resolve_with_m1(trade, candle) is None

def test_paper_wallet_reports_open_interest_context():
    from backend.app.db import FuturesOpenInterest
    now = 1_792_432_000
    with Session.begin() as session:
        session.add(FuturesOpenInterest(symbol="BTC_USDT", time=now - 86400, hold_vol=1000.0))

    class OiMarket(Market):
        def ticker(self):
            return {**super().ticker(), "holdVol": 1100.0, "time": now}

    sync_paper_quote(OiMarket(100, timestamp=now))
    wallet = client.get("/api/paper/wallet").json()
    assert wallet["open_interest"]["contracts"] == 1100.0
    assert wallet["open_interest"]["change_24h_pct"] == pytest.approx(10.0)
