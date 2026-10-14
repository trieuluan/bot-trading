import json
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from backend.app import exness_execution as ee
from backend.app.db import (ExnessDeal, ExnessExecutionState, ExnessExecutionTrade,
                            ForexObservation, Session, WorkspaceCandle, WorkspaceFeedState)
from backend.app.exness import ExnessError, load_private_key
from backend.app.exness_feed import WORKSPACE
from backend.app.main import app

NOW = 1791900000

def route(positions, orders):
    def get(path, params=None):
        if path.endswith("/snapshot"):
            return {"positions": list(positions), "orders": list(orders), "account_state": {}}
        return {"items": [], "has_more": False}
    return get

def dummy_api(positions=(), orders=(), is_demo=True, account_id="31000117657"):
    key = load_private_key("00" * 32)
    api = SimpleNamespace(
        account_id=account_id, api_key="k", key=key, base_url="https://api.exness.com",
        next_request=0, request_interval=0.01, rate_limit_until=0,
        account=lambda: {"account_id": account_id, "is_demo": is_demo, "settings": {"currency": "USD"}},
        snapshot=lambda: {"positions": list(positions), "orders": list(orders), "account_state": {}},
        condition=lambda sym: {"instrument": "XAUUSD", "trade_mode": "enabled", "exec_mode": "market",
                               "quote_currency": "USD", "volume_min": "0.01", "volume_max": "100.0",
                               "volume_step": "0.01", "contract_size": "100", "point_digits": 3},
        limits=lambda: {"limits": {"rest": {
            "global_account_rate": {"limit": 100, "window_seconds": 1},
            "methods": [{"api_operation_id": op, "rate_limit": {"limit": 100, "window_seconds": 1}}
                        for op in ("openPosition", "closePosition", "getTradingStateSnapshot", "getAccountDetails")]
        }}},
        get=route(positions, orders),
        client=SimpleNamespace(request=MagicMock(return_value=SimpleNamespace(status_code=202, json=lambda: {"operation_id": "op-1"})))
    )
    return api

@pytest.fixture(autouse=True)
def setup_env(monkeypatch):
    monkeypatch.setenv("EXNESS_ENVIRONMENT", "demo")
    monkeypatch.setenv("EXNESS_SYMBOL", "XAUUSD")
    monkeypatch.setenv("EXNESS_DEMO_EXECUTION_ENABLED", "true")
    monkeypatch.setenv("EXNESS_DEMO_ORDERS_ENABLED", "true")
    monkeypatch.setenv("EXNESS_ACCOUNT_ID", "31000117657")
    monkeypatch.setenv("EXNESS_DEMO_MAX_POSITIONS", "1")
    monkeypatch.setenv("EXNESS_DEMO_MAX_LOT", "0.01")
    monkeypatch.setenv("EXNESS_DEMO_RISK_PER_TRADE_USD", "25")
    monkeypatch.setattr(ee, "live_quote", lambda: {"bid": 4000.0, "ask": 4000.25, "time": NOW, "received_at": NOW})

def test_disabled_by_default(monkeypatch):
    monkeypatch.delenv("EXNESS_DEMO_EXECUTION_ENABLED", raising=False)
    api = dummy_api()
    assert ee.execution_tick(api, now=NOW) == "disabled"

def test_refuses_real_account(monkeypatch):
    monkeypatch.setenv("EXNESS_ENVIRONMENT", "real")
    api = dummy_api(is_demo=False)
    assert ee.execution_tick(api, now=NOW) == "disabled"

def test_demo_env_never_overrides_broker_real_account():
    api = dummy_api(is_demo=False)
    with pytest.raises(ExnessError, match="demo account"):
        ee._execution_tick(api, now=NOW)
    api.client.request.assert_not_called()

def signal():
    return {"status": "confirmed", "candle_time": NOW - 900, "available_at": NOW,
            "side": "long", "stop": 3995, "target": 4010, "reason": "router signal"}

def seed_executor(api, monkeypatch):
    monkeypatch.setattr(ee.time, "time", lambda: NOW)
    monkeypatch.setattr(ee, "evaluate", lambda *_: signal())
    with Session.begin() as session:
        session.add(ExnessExecutionState(account=api.account_id, enabled=True, started_at=NOW - 10,
                                        snapshot={"history_until": NOW}))

def test_commits_intent_before_submit_and_native_sltp(monkeypatch):
    api = dummy_api()
    seed_executor(api, monkeypatch)
    def request(method, url, **kwargs):
        with Session() as session:
            row = session.get(ExnessExecutionTrade, (api.account_id, NOW - 900))
            assert row.snapshot["status"] == "submitting"
            body = json.loads(kwargs["content"])
            assert row.snapshot["body"] == body
            assert body["volume"] == "0.01"
            assert float(body["stop_loss_price"]) == 3994.75
            assert float(body["take_profit_price"]) > 4000.25
        return SimpleNamespace(status_code=202, json=lambda: {"operation_id": "123"})
    api.client.request.side_effect = request
    assert ee._execution_tick(api, now=NOW) == "submitted"
    api.get = lambda path, params=None: (
        {"account_state": {}, "positions": [], "orders": []} if path.endswith("/snapshot") else
        {"status": "pending"} if "/operations/" in path else {"items": [], "has_more": False})
    assert ee._execution_tick(api, now=NOW) == "reconciling"
    assert api.client.request.call_count == 1

def test_timeout_blocks_next_signal_and_never_resubmits(monkeypatch):
    import httpx
    api = dummy_api()
    seed_executor(api, monkeypatch)
    api.client.request.side_effect = httpx.ReadTimeout("timeout")
    with pytest.raises(ExnessError, match="outcome unknown"):
        ee._execution_tick(api, now=NOW)
    assert ee._execution_tick(api, now=NOW) == "reconciling"
    assert api.client.request.call_count == 1

def test_old_signal_not_executed_on_first_start(monkeypatch):
    api = dummy_api()
    seed_executor(api, monkeypatch)
    monkeypatch.setattr(ee, "evaluate", lambda *_: {**signal(), "available_at": NOW - 100})
    assert ee._execution_tick(api, now=NOW) == "old_or_duplicate_signal"
    api.client.request.assert_not_called()

def test_pause_persists_and_does_not_send_orders(monkeypatch):
    api = dummy_api()
    seed_executor(api, monkeypatch)
    client = TestClient(app)
    assert client.patch("/api/workspaces/exness_forex/execution", json={"enabled": False}).status_code == 200
    assert ee._execution_tick(api, now=NOW) == "paused"
    api.client.request.assert_not_called()

def test_close_rejects_unowned_or_missing_position():
    assert TestClient(app).post("/api/workspaces/exness_forex/execution/trades/1/close").status_code == 409

def test_reconciles_and_pauses_when_active_position():
    api = dummy_api(positions=[{"position_id": "100", "instrument": "XAUUSD", "direction": "buy",
                                "volume": "0.01", "open_price": "4000.0", "create_time": "2026-10-15T00:00:00Z"}],
                    orders=[{"position_id": "100", "order_type": "sltp", "stop_loss_price": "3995", "take_profit_price": "4010"}])
    with Session.begin() as s:
        s.add(ExnessExecutionState(account=api.account_id, enabled=True, started_at=NOW - 3600, snapshot={}))
    res = ee._execution_tick(api, now=NOW)
    assert res == "position_active"

def test_closes_position_if_missing_sltp_protection():
    api = dummy_api(positions=[{"position_id": "100", "instrument": "XAUUSD", "direction": "buy",
                                "volume": "0.01", "open_price": "4000.0", "create_time": "2026-10-15T00:00:00Z"}],
                    orders=[])  # no SLTP order on broker
    with Session.begin() as s:
        s.add(ExnessExecutionState(account=api.account_id, enabled=True, started_at=NOW - 3600, snapshot={}))
        s.add(ExnessExecutionTrade(account=api.account_id, signal_time=NOW - 900,
                                   snapshot={"status": "open", "position_id": "100"}))
    res = ee._execution_tick(api, now=NOW)
    assert res == "closing"
    assert api.client.request.called

def test_summary_pairs_deals_into_positions():
    with Session.begin() as s:
        s.add(ExnessDeal(account="31000117657", deal_id="1", position_id="99", event_time=NOW - 100,
                         snapshot={"deal_id": "1", "position_id": "99", "instrument": "XAUUSD",
                                   "type": "open", "direction": "buy", "volume": "0.01", "price": "4000.0",
                                   "event_time": NOW - 100}))
        s.add(ExnessDeal(account="31000117657", deal_id="2", position_id="99", event_time=NOW - 10,
                         snapshot={"deal_id": "2", "position_id": "99", "instrument": "XAUUSD",
                                   "type": "close", "direction": "sell", "volume": "0.01", "price": "4010.0",
                                   "profit": {"amount": "10.0"}, "event_time": NOW - 10}))
    client = TestClient(app)
    res = client.get("/api/workspaces/exness_forex/execution")
    assert res.status_code == 200
    data = res.json()
    assert data["closed_count"] == 1
    assert data["broker_profit"] == 10.0
    pos = data["positions"][0]
    assert pos["position_id"] == "99"
    assert pos["status"] == "closed"
    assert pos["pnl"] == 10.0

def test_max_positions_env_allows_more_than_one(monkeypatch):
    monkeypatch.delenv("EXNESS_DEMO_MAX_POSITIONS", raising=False)
    pos = {"position_id": "1", "instrument": "XAUUSD", "direction": "buy"}
    broker = {"positions": [pos], "orders": []}
    assert ee.busy(broker)
    monkeypatch.setenv("EXNESS_DEMO_MAX_POSITIONS", "2")
    assert not ee.busy(broker)
    assert ee.busy({"positions": [pos, pos], "orders": []})
    monkeypatch.setenv("EXNESS_DEMO_MAX_POSITIONS", "999")
    assert ee.max_positions() == 20

@pytest.mark.parametrize("value", ["0", "-1", "invalid", ""])
def test_invalid_position_limit_fails_safe(monkeypatch, value):
    monkeypatch.setenv("EXNESS_DEMO_MAX_POSITIONS", value)
    assert ee.max_positions() == 1

def test_open_position_and_its_protection_allow_new_signal(monkeypatch):
    monkeypatch.setenv("EXNESS_DEMO_MAX_POSITIONS", "5")
    api = dummy_api(positions=[{"position_id": "100", "instrument": "XAUUSD"}],
                    orders=[{"position_id": "100", "order_type": "sltp",
                             "stop_loss_price": "3995", "take_profit_price": "4010"}])
    seed_executor(api, monkeypatch)
    with Session.begin() as session:
        session.add(ExnessExecutionTrade(account=api.account_id, signal_time=NOW - 1800,
                                       snapshot={"status": "open", "position_id": "100"}))
    assert ee._execution_tick(api, now=NOW) == "submitted"
    assert api.client.request.call_count == 1

def test_pending_order_blocks_new_position(monkeypatch):
    monkeypatch.setenv("EXNESS_DEMO_MAX_POSITIONS", "5")
    assert ee.busy({"positions": [], "orders": [{"order_type": "limit"}]})
    assert not ee.busy({"positions": [], "orders": [{"order_type": "sltp"}]})


def test_signal_splits_into_native_tp1_then_runner_only_after_first_fill(monkeypatch):
    monkeypatch.setenv("EXNESS_DEMO_MAX_POSITIONS", "2")
    monkeypatch.setenv("EXNESS_DEMO_MAX_LOT", "0.10")
    api = dummy_api()
    seed_executor(api, monkeypatch)
    bodies = []
    def request(method, url, **kwargs):
        bodies.append(json.loads(kwargs["content"]))
        return SimpleNamespace(status_code=202, json=lambda: {"operation_id": str(len(bodies))})
    api.client.request.side_effect = request
    assert ee._execution_tick(api, now=NOW) == "submitted"
    assert len(bodies) == 1 and bodies[0]["volume"] == "0.02"
    with Session() as session:
        row = session.get(ExnessExecutionTrade, (api.account_id, NOW - 900))
        assert [leg["status"] for leg in row.snapshot["legs"]] == ["submitted", "planned"]
