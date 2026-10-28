import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from backend.app import exness_execution as ee
from backend.app.db import (
    ExnessDeal,
    ExnessExecutionState,
    ExnessExecutionTrade,
    Session,
)
from backend.app.exness import ExnessError, load_private_key
from backend.app.forex_costs import account_tag
from backend.app.main import app

NOW = 1791900000

def route(positions, orders, equity="1000"):
    def get(path, params=None):
        if path.endswith("/snapshot"):
            return {"positions": list(positions), "orders": list(orders), "account_state": {"equity": equity}}
        return {"items": [], "has_more": False}
    return get

def dummy_api(positions=(), orders=(), is_demo=True, account_id="31000117657", equity="1000"):
    key = load_private_key("00" * 32)
    api = SimpleNamespace(
        account_id=account_id, api_key="k", key=key, base_url="https://api.exness.com",
        next_request=0, request_interval=0.01, rate_limit_until=0,
        account=lambda: {"account_id": account_id, "is_demo": is_demo, "settings": {"currency": "USD"}},
        snapshot=lambda: {"positions": list(positions), "orders": list(orders), "account_state": {"equity": equity}},
        condition=lambda sym: {"instrument": "XAUUSD", "trade_mode": "enabled", "exec_mode": "market",
                               "quote_currency": "USD", "volume_min": "0.01", "volume_max": "100.0",
                               "volume_step": "0.01", "contract_size": "100", "point_digits": 3},
        limits=lambda: {"limits": {"rest": {
            "global_account_rate": {"limit": 100, "window_seconds": 1},
            "methods": [{"api_operation_id": op, "rate_limit": {"limit": 100, "window_seconds": 1}}
                        for op in ("openPosition", "closePosition", "getTradingStateSnapshot", "getAccountDetails")]
        }}},
        get=route(positions, orders, equity),
        client=SimpleNamespace(request=MagicMock(return_value=SimpleNamespace(status_code=202, json=lambda: {"operation_id": "op-1"})))
    )
    return api

@pytest.fixture(autouse=True)
def setup_env(monkeypatch):
    monkeypatch.setenv("EXNESS_ENVIRONMENT", "demo")
    monkeypatch.setenv("EXNESS_SYMBOL", "XAUUSD")
    monkeypatch.delenv("EXNESS_SYMBOLS", raising=False)
    monkeypatch.setenv("EXNESS_DEMO_EXECUTION_ENABLED", "true")
    monkeypatch.setenv("EXNESS_DEMO_ORDERS_ENABLED", "true")
    monkeypatch.setenv("EXNESS_ACCOUNT_ID", "31000117657")
    monkeypatch.setenv("EXNESS_DEMO_MAX_POSITIONS", "1")
    monkeypatch.setenv("EXNESS_DEMO_MAX_LOT", "0.01")
    monkeypatch.setenv("EXNESS_DEMO_RISK_PER_TRADE_USD", "25")
    monkeypatch.setattr(ee, "live_quote", lambda *_: {"bid": 4000.0, "ask": 4000.25, "time": NOW, "received_at": NOW})
    monkeypatch.setattr("backend.app.forex_costs.spec_summary", lambda *_: {"profile": {
        "observed_at": NOW, "account_currency": "USD", "quote_currency": "USD", "commission_per_unit": 0,
        "instrument": "XAUUSD", "account_tag": account_tag("31000117657"),
    }})

def test_disabled_by_default(monkeypatch):
    monkeypatch.delenv("EXNESS_DEMO_EXECUTION_ENABLED", raising=False)
    api = dummy_api()
    assert ee.execution_tick(api, now=NOW) == "disabled"

def seed_virtual_entry(api, monkeypatch, tmp_path):
    from backend.app.forex_entry_intents import create_intent
    from backend.app.forex_entry_pilot import initialize, logic_version
    from backend.app.forex_entry_policy import with_entry
    from backend.app.forex_htf_v2 import g0_trees, tree_id
    from backend.app.forex_migration import atomic_json
    monkeypatch.setenv("FOREX_ENTRY_PILOT_ENABLED", "true")
    monkeypatch.setenv("FOREX_HTF_V2_EXECUTION_ENABLED", "true")
    monkeypatch.setattr(ee.time, "time", lambda: NOW)
    path = tmp_path / "pilot.json"
    monkeypatch.setattr("backend.app.forex_entry_pilot.ledger_path", lambda *_: path)
    sources = g0_trees()
    tree = with_entry(sources[0])
    ledger = initialize(path, api.account_id, "XAUUSD", sources, NOW - 1)
    proposal = {**signal(), "symbol": "XAUUSD", "strategy": tree_id(tree), "setup_id": "setup",
                "htf_ast_v2": True, "shadow_only": True, "generated": True,
                "target": 4015, "exit_policy": "partial_be_h1", "management_frame": "H1",
                "htf_structure": {"atr": 2, "level": 3999, "setup_time": NOW - 3600,
                                  "confirmed_h1": NOW - 3600, "invalidation": 3995.5, "target_reason": "H1 swing"},
                "entry_policy": {"policy": "limit_or_stop", "limit_price": 3999.5, "stop_price": 4000.25,
                                 "available_at": NOW, "expires_at": NOW + 3600}}
    ledger["samples"] = [{"strategy": tree_id(tree), "strategy_version": logic_version(), "setup_id": "setup",
                          "signal": proposal, "costs": {"verified": True}}]
    atomic_json(path, ledger)
    with Session.begin() as session:
        session.add(ExnessExecutionState(account=api.account_id, enabled=True, started_at=NOW - 1,
                                        snapshot={"history_until": NOW}))
        row = create_intent(session, api.account_id, proposal, tree, NOW, 2)
        row.status = "triggered"
        row.snapshot = {**row.snapshot, "status": "triggered", "winner": "stop", "triggered_at": NOW,
                        "last_quote": ee.live_quote(), "trigger_quote": ee.live_quote()}
        return row.id

def test_verified_virtual_entry_only_one_submit_plan(monkeypatch, tmp_path):
    from backend.app.db import ExnessEntryIntent
    from backend.app.forex_entry_intents import execute_trigger
    monkeypatch.setenv("EXNESS_DEMO_MAX_POSITIONS", "6")
    api = dummy_api()
    identifier = seed_virtual_entry(api, monkeypatch, tmp_path)
    execute_trigger(api, identifier, "XAUUSD")
    assert api.client.request.call_count == 1
    with Session() as session:
        row = session.get(ExnessEntryIntent, identifier)
        assert row.status == "submitted"
        assert row.snapshot["winner"] == "stop"
    execute_trigger(api, identifier, "XAUUSD")
    assert api.client.request.call_count == 1

def test_virtual_callback_refreshes_history_at_fractional_tick(monkeypatch, tmp_path):
    from backend.app.forex_entry_intents import execute_trigger
    api = dummy_api()
    identifier = seed_virtual_entry(api, monkeypatch, tmp_path)
    monkeypatch.setenv("EXNESS_DEMO_MAX_POSITIONS", "6")
    monkeypatch.setattr(ee.time, "time", lambda: NOW + 0.5)
    execute_trigger(api, identifier, "XAUUSD")
    api.client.request.assert_called_once()
    with Session() as session:
        assert session.get(ExnessExecutionState, api.account_id).snapshot["history_until"] == NOW + 0.5
@pytest.mark.parametrize("gate", ["FOREX_ENTRY_PILOT_ENABLED", "FOREX_HTF_V2_EXECUTION_ENABLED", "EXNESS_DEMO_EXECUTION_ENABLED"])
def test_virtual_entry_gates_never_submit(monkeypatch, tmp_path, gate):
    from backend.app.forex_entry_intents import execute_trigger
    api = dummy_api()
    identifier = seed_virtual_entry(api, monkeypatch, tmp_path)
    monkeypatch.setenv(gate, "false")
    execute_trigger(api, identifier, "XAUUSD")
    api.client.request.assert_not_called()

def test_virtual_entry_rollback_and_restart_reservations(monkeypatch, tmp_path):
    from backend.app.db import ExnessEntryIntent
    from backend.app.forex_entry_intents import reconcile_intents
    api = dummy_api()
    identifier = seed_virtual_entry(api, monkeypatch, tmp_path)
    reconcile_intents(api.account_id, NOW + 1, restart=True)
    with Session.begin() as session:
        row = session.get(ExnessEntryIntent, identifier)
        assert row.status == "cancelled"
        row.status = "submitting"
    reconcile_intents(api.account_id, NOW + 2, restart=True)
    with Session() as session:
        assert session.get(ExnessEntryIntent, identifier).status == "uncertain"
    api.client.request.assert_not_called()

def test_virtual_entry_forged_payload_rejected_at_broker_boundary(monkeypatch, tmp_path):
    from backend.app.db import ExnessEntryIntent
    from backend.app.forex_entry_intents import execution_signal
    api = dummy_api()
    identifier = seed_virtual_entry(api, monkeypatch, tmp_path)
    with Session() as session:
        proposal = execution_signal(session.get(ExnessEntryIntent, identifier))
    proposal["target"] += 1
    with pytest.raises(ExnessError, match="unverified_virtual_entry"):
        ee.ExnessDemoClient(api).strategy_body(proposal, api.condition("XAUUSD"), ee.live_quote(), "forged")
    api.client.request.assert_not_called()

def test_virtual_quotes_race_only_one_winner(monkeypatch, tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    from backend.app.db import ExnessEntryIntent, WorkspaceCandle
    from backend.app.forex_entry_intents import consume_quote
    api = dummy_api()
    identifier = seed_virtual_entry(api, monkeypatch, tmp_path)
    with Session.begin() as session:
        row = session.get(ExnessEntryIntent, identifier)
        row.status = "waiting"
        row.snapshot = {**row.snapshot, "status": "waiting", "last_quote": None}
        session.add(WorkspaceCandle(workspace_id="exness_forex", symbol="XAUUSD", timeframe="1h",
                                   price_type="bid", time=NOW - 3600, open=4000, high=4002, low=3998, close=4000))
    def consume(stamp, ask):
        with Session.begin() as session:
            return consume_quote(session, api.account_id, "XAUUSD",
                                 {"bid": ask - 0.25, "ask": ask, "time": stamp, "received_at": stamp}, stamp)
    assert consume(NOW, 4000.1) == []
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda price: consume(NOW + 1, price), (4000.25, 3999.5)))
    assert sum(len(result) for result in results) == 1
    with Session() as session:
        assert session.get(ExnessEntryIntent, identifier).snapshot["winner"] in ("limit", "stop")
    api.client.request.assert_not_called()

def test_virtual_capacity_reserves_whole_plan_and_no_double_count(monkeypatch, tmp_path):
    from backend.app.db import ExnessEntryIntent
    api = dummy_api()
    identifier = seed_virtual_entry(api, monkeypatch, tmp_path)
    monkeypatch.setenv("EXNESS_DEMO_MAX_POSITIONS", "66")
    monkeypatch.setenv("EXNESS_DEMO_MAX_POSITIONS_PER_SYMBOL", "6")
    positions = [{"position_id": str(index), "instrument": "EURUSD"} for index in range(64)]
    broker = {"positions": positions, "orders": []}
    assert ee.capacity_available(broker, api.account_id, "XAUUSD")
    assert not ee.capacity_available(broker, api.account_id, "XAUUSD", 1)
    assert ee.capacity_available(broker, api.account_id, "XAUUSD", 2, entry_intent_id=identifier)
    broker["positions"] = [{"position_id": str(index), "instrument": "XAUUSD"} for index in range(5)]
    assert not ee.capacity_available(broker, api.account_id, "XAUUSD")
    with Session.begin() as session:
        session.get(ExnessEntryIntent, identifier).status = "cancelled"
    assert ee.capacity_available(broker, api.account_id, "XAUUSD", 1)

@pytest.mark.parametrize("second_fails", [False, True])
def test_virtual_split_submit_and_uncertain_restart(monkeypatch, tmp_path, second_fails):
    import httpx

    from backend.app.db import ExnessEntryIntent
    from backend.app.forex_entry_intents import execute_trigger, reconcile_intents
    api = dummy_api(equity="2500")
    identifier = seed_virtual_entry(api, monkeypatch, tmp_path)
    monkeypatch.setenv("EXNESS_DEMO_MAX_POSITIONS", "6")
    monkeypatch.setenv("EXNESS_DEMO_MAX_LOT", "0.10")
    if second_fails:
        api.client.request.side_effect = [api.client.request.return_value, httpx.ReadTimeout("unknown result")]
        with pytest.raises(ExnessError, match="Submission outcome unknown"):
            execute_trigger(api, identifier, "XAUUSD")
    else:
        execute_trigger(api, identifier, "XAUUSD")
    assert api.client.request.call_count == 2
    keys = [call.kwargs["headers"]["EXN-IDEMPOTENCY-KEY"] for call in api.client.request.call_args_list]
    assert len(set(keys)) == 2
    reconcile_intents(api.account_id, NOW + 1, restart=True)
    with Session() as session:
        assert session.get(ExnessEntryIntent, identifier).status == ("uncertain" if second_fails else "submitted")
    execute_trigger(api, identifier, "XAUUSD")
    assert api.client.request.call_count == 2

def test_virtual_missing_h1_bucket_cancels(monkeypatch, tmp_path):
    from backend.app.db import ExnessEntryIntent, WorkspaceCandle
    from backend.app.forex_entry_intents import consume_quote
    api = dummy_api()
    identifier = seed_virtual_entry(api, monkeypatch, tmp_path)
    with Session.begin() as session:
        row = session.get(ExnessEntryIntent, identifier)
        row.status = "waiting"
        signal = {**row.snapshot["signal"], "htf_structure": {
            **row.snapshot["signal"]["htf_structure"], "confirmed_h1": NOW - 3 * 3600}}
        row.snapshot = {**row.snapshot, "status": "waiting", "signal": signal, "last_quote": None}
        for stamp in (NOW - 3 * 3600, NOW - 3600):
            session.add(WorkspaceCandle(workspace_id="exness_forex", symbol="XAUUSD", timeframe="1h",
                                       price_type="bid", time=stamp, open=4000, high=4002, low=3998, close=4000))
    with Session.begin() as session:
        assert consume_quote(session, api.account_id, "XAUUSD", ee.live_quote(), NOW) == []
        assert session.get(ExnessEntryIntent, identifier).status == "cancelled"
    api.client.request.assert_not_called()

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
            row = session.get(ExnessExecutionTrade, (api.account_id, "XAUUSD", NOW - 900))
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
    assert ee.max_positions() == 66

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

@pytest.mark.parametrize("quote_offset", [9.5, 7, 11])
def test_stop_management_validates_quote_at_current_time(monkeypatch, quote_offset):
    api = dummy_api()
    seed_executor(api, monkeypatch)
    monkeypatch.setattr(ee.time, "time", lambda: NOW + 10)
    fresh_quote = {"bid": 4006.0, "ask": 4006.25,
                   "time": NOW + quote_offset, "received_at": NOW + quote_offset}
    monkeypatch.setattr(ee, "live_quote", lambda *_: fresh_quote)
    client = MagicMock(can_modify=True)
    client.prepare.return_value = api.condition("XAUUSD")
    client.mutate.return_value = {"operation_id": "stop-100"}
    monkeypatch.setattr(ee, "ExnessDemoClient", lambda *_: client)
    monkeypatch.setattr(ee, "session_closed_ahead", lambda *args, **kwargs: False)
    monkeypatch.setattr(ee, "load_frames", lambda *_: {})
    position = {"position_id": "100", "instrument": "XAUUSD",
                "open_price": "4000", "direction": "buy"}
    protection = {"position_id": "100", "order_id": "protect-100", "order_type": "sltp",
                  "stop_loss_price": "3995", "take_profit_price": "4010"}
    with Session.begin() as session:
        session.add(ExnessExecutionTrade(account=api.account_id, symbol="XAUUSD",
                    signal_time=NOW - 1800, snapshot={"status": "open", "position_id": "100",
                    "signal": {"exit_policy": "partial_be_m15", "partial_be": True},
                    "body": {"stop_loss_price": "3995"}}))
    broker = {"positions": [position], "orders": [protection]}
    if quote_offset != 9.5:
        with pytest.raises(ValueError, match="Stale quote or excessive spread"):
            ee._symbol_execution_tick(api, broker, NOW, "XAUUSD")
        client.mutate.assert_not_called()
        return
    assert ee._symbol_execution_tick(api, broker, NOW, "XAUUSD") == "managing_stop"
    client.mutate.assert_called_once()
    assert client.mutate.call_args.kwargs["body"] == {"stop_loss_price": "4000.000"}
    with Session() as session:
        row = session.get(ExnessExecutionTrade, (api.account_id, "XAUUSD", NOW - 1800))
        assert row.snapshot["management"]["status"] == "submitted"

def test_pending_order_blocks_new_position(monkeypatch):
    monkeypatch.setenv("EXNESS_DEMO_MAX_POSITIONS", "5")
    assert ee.busy({"positions": [], "orders": [{"order_type": "limit"}]})
    assert not ee.busy({"positions": [], "orders": [{"order_type": "sltp"}]})


def test_signal_splits_into_native_tp1_then_runner_only_after_first_fill(monkeypatch):
    monkeypatch.setenv("EXNESS_DEMO_MAX_POSITIONS", "2")
    monkeypatch.setenv("EXNESS_DEMO_MAX_LOT", "0.10")
    api = dummy_api(equity="2500")
    seed_executor(api, monkeypatch)
    bodies = []
    def request(method, url, **kwargs):
        bodies.append(json.loads(kwargs["content"]))
        return SimpleNamespace(status_code=202, json=lambda: {"operation_id": str(len(bodies))})
    api.client.request.side_effect = request
    assert ee._execution_tick(api, now=NOW) == "submitted"
    assert len(bodies) == 1 and bodies[0]["volume"] == "0.02"
    with Session() as session:
        row = session.get(ExnessExecutionTrade, (api.account_id, "XAUUSD", NOW - 900))
        assert [leg["status"] for leg in row.snapshot["legs"]] == ["submitted", "planned"]

@pytest.mark.parametrize("equity", [None, "", "NaN", "Infinity", "0", "-100", True])
def test_invalid_broker_equity_blocks_auto_without_submission(monkeypatch, equity):
    api = dummy_api(equity=equity)
    seed_executor(api, monkeypatch)
    assert ee._execution_tick(api, now=NOW) == "preflight_failed"
    api.client.request.assert_not_called()
    with Session() as session:
        assert session.get(ExnessExecutionTrade, (api.account_id, "XAUUSD", NOW - 900)) is None

@pytest.mark.parametrize("fresh_equity,ask", [("5000", 4000.25)])
def test_split_rechecks_current_equity_and_original_setup_budget(monkeypatch, fresh_equity, ask):
    monkeypatch.setenv("EXNESS_DEMO_MAX_POSITIONS", "6")
    api = dummy_api(equity="10000")
    seed_executor(api, monkeypatch)
    assert ee._execution_tick(api, now=NOW) == "submitted"
    with Session() as session:
        row = session.get(ExnessExecutionTrade, (api.account_id, "XAUUSD", NOW - 900))
        assert row.snapshot["risk_equity"] == "10000"
        assert row.snapshot["risk_budget_usd"] == "100"
        assert [leg["body"]["volume"] for leg in row.snapshot["legs"]] == ["0.09", "0.09"]
    api.get = route((), (), fresh_equity)
    monkeypatch.setattr(ee, "live_quote", lambda *_: {"bid": 4000.0, "ask": ask, "time": NOW, "received_at": NOW})
    condition = {**api.condition("XAUUSD"), "account_currency": "USD", "observed_at": NOW,
                 "account_tag": account_tag(api.account_id), "commission_per_unit": 0}
    with pytest.raises(ExnessError, match="Combined split risk exceeds setup budget"):
        ee.submit_leg(ee.ExnessDemoClient(api), row, 1, row.snapshot["legs"][1], condition, NOW)
    assert api.client.request.call_count == 1
    with Session() as session:
        row = session.get(ExnessExecutionTrade, (api.account_id, "XAUUSD", NOW - 900))
        assert row.snapshot["legs"][1]["status"] == "skipped"

def test_equity_missing_at_submission_does_not_send_planned_order(monkeypatch):
    api = dummy_api(equity="1000")
    seed_executor(api, monkeypatch)
    original = api.get
    snapshots = 0
    def get(path, params=None):
        nonlocal snapshots
        result = original(path, params)
        if path.endswith("/snapshot"):
            snapshots += 1
            if snapshots > 1:
                result["account_state"] = {}
        return result
    api.get = get
    with pytest.raises(ExnessError, match="Invalid account equity"):
        ee._execution_tick(api, now=NOW)
    api.client.request.assert_not_called()
    with Session() as session:
        row = session.get(ExnessExecutionTrade, (api.account_id, "XAUUSD", NOW - 900))
        assert row.snapshot["status"] == "skipped"

@pytest.mark.parametrize("symbol_count,total_count", [(5, 5), (0, 65)])
def test_entire_split_plan_must_fit_account_and_symbol_limits(monkeypatch, symbol_count, total_count):
    monkeypatch.setenv("EXNESS_DEMO_MAX_POSITIONS", "66")
    monkeypatch.setenv("EXNESS_DEMO_MAX_POSITIONS_PER_SYMBOL", "6")
    monkeypatch.setenv("EXNESS_DEMO_MAX_LOT", "0.10")
    positions = [{"position_id": str(index), "instrument": "XAUUSD" if index < symbol_count else "EURUSD"}
                 for index in range(total_count)]
    api = dummy_api(positions=positions, equity="2500")
    seed_executor(api, monkeypatch)
    assert ee._execution_tick(api, now=NOW) == "position_capacity_for_split"
    api.client.request.assert_not_called()
    with Session() as session:
        assert session.get(ExnessExecutionTrade, (api.account_id, "XAUUSD", NOW - 900)) is None

def test_account_lock_blocks_competing_executor(monkeypatch, tmp_path):
    import fcntl
    import hashlib
    monkeypatch.chdir(tmp_path)
    directory = tmp_path / "research-output"
    directory.mkdir()
    api = dummy_api()
    fingerprint = hashlib.sha256(api.account_id.encode()).hexdigest()[:16]
    with (directory / f"exness-probe-{fingerprint}.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        assert ee.execution_tick(api, now=NOW) == "calibration_or_executor_active"
    api.client.request.assert_not_called()

def test_one_symbol_failure_does_not_repeat_account_reconcile_or_block_other(monkeypatch):
    monkeypatch.setenv("EXNESS_SYMBOLS", "XAUUSD,EURUSD")
    api = dummy_api()
    snapshot_mock = MagicMock(side_effect=api.get)
    api.get = snapshot_mock
    reconcile = MagicMock()
    monkeypatch.setattr(ee, "reconcile", reconcile)
    monkeypatch.setattr(ee, "sync_deals", lambda *_: None)
    def evaluate_symbol(api, broker, now, symbol):
        if symbol == "XAUUSD":
            raise ExnessError("instrument unavailable")
        return "waiting_signal"
    monkeypatch.setattr(ee, "_symbol_execution_tick", evaluate_symbol)
    result = ee._execution_tick(api, now=NOW)
    assert result["EURUSD"] == "waiting_signal"
    assert "instrument unavailable" in result["XAUUSD"]
    snapshot_mock.assert_called_once()
    reconcile.assert_called_once()

def test_operation_404_falls_back_and_heartbeat_survives_api_error():
    def missing(path, params=None):
        raise ExnessError("Exness HTTP 404")
    assert ee.operation_result(SimpleNamespace(account_id="1", get=missing), "op") == {}
    api = dummy_api()
    api.account = lambda: (_ for _ in ()).throw(ExnessError("Exness rate limit"))
    with pytest.raises(ExnessError):
        ee._execution_tick(api, now=NOW)
    with Session() as session:
        assert session.get(ExnessExecutionState, api.account_id).snapshot["heartbeat"] == NOW

def test_reconciles_intent_older_than_one_day():
    api = dummy_api()
    old = NOW - 3 * 86400
    leg = {"status": "submitted", "key": "rr-old", "recorded_at": old, "ack": {"operation_id": "9"}}
    with Session.begin() as session:
        session.add(ExnessExecutionTrade(account=api.account_id, signal_time=old, snapshot=leg))
        session.add(ExnessDeal(account=api.account_id, deal_id="2", position_id="P", event_time=old + 9,
                               snapshot={"type": "close", "price": "1", "position_id": "P"}))
    def get(path, params=None):
        if "/operations/" in path:
            return {"status": "accepted"}
        if path.endswith("/orders"):
            return {"items": [{"order": {"comment": "rr-old", "instrument": "XAUUSD",
                                         "state": "filled", "position_id": "P", "order_id": "1"}}], "has_more": False}
        return {"items": [], "has_more": False}
    api.get = get
    ee.reconcile(api, {"positions": [], "orders": []}, NOW)
    with Session() as session:
        assert session.get(ExnessExecutionTrade, (api.account_id, "XAUUSD", old)).snapshot["status"] == "closed"


def test_adaptive_signal_splits_and_manages_via_broker(monkeypatch):
    monkeypatch.setenv("EXNESS_DEMO_MAX_POSITIONS", "2")
    monkeypatch.setenv("EXNESS_DEMO_MAX_LOT", "0.10")
    api = dummy_api(equity="2500")
    seed_executor(api, monkeypatch)
    adaptive_sig = {
        **signal(),
        "htf_ast_v2": True,
        "exit_policy": "evidence_h1",
        "exit_params": {"fraction": 0.3, "min_r": 1.0, "trail_r": 1.5},
        "management_frame": "H1",
    }
    monkeypatch.setattr(ee, "evaluate", lambda *_: adaptive_sig)
    bodies = []
    def request(method, url, **kwargs):
        bodies.append(json.loads(kwargs["content"]))
        return SimpleNamespace(status_code=202, json=lambda: {"operation_id": str(len(bodies))})
    api.client.request.side_effect = request
    assert ee._execution_tick(api, now=NOW) == "submitted"
    with Session() as session:
        row = session.get(ExnessExecutionTrade, (api.account_id, "XAUUSD", NOW - 900))
        assert [leg["role"] for leg in row.snapshot["legs"]] == ["partial1", "runner"]

@pytest.mark.parametrize("copies", [1, 2])
def test_reconciles_when_broker_drops_comment(copies):
    api = dummy_api()
    old = NOW - 100
    leg = {
        "status": "submitted", "key": "rr-no-comment", "recorded_at": old, "submitted_at": old + 1,
        "body": {"instrument": "XAUUSD", "side": "buy", "volume": "0.01", "stop_loss_price": "4000.0", "take_profit_price": "4020.0"}
    }
    with Session.begin() as session:
        session.add(ExnessExecutionTrade(account=api.account_id, signal_time=old, snapshot=leg))
        session.add(ExnessDeal(account=api.account_id, deal_id="3", position_id="P2", event_time=old + 5,
                               snapshot={"type": "close", "price": "4020.0", "position_id": "P2"}))
    def get(path, params=None):
        if path.endswith("/orders"):
            return {"items": [{"order": {
                "comment": None, "instrument": "XAUUSD", "direction": "buy", "order_type": "market",
                "volume": "0.01", "stop_loss_price": "4000.0", "take_profit_price": "4020.0",
                "create_time": datetime.fromtimestamp(old + 1, timezone.utc).isoformat(), "state": "filled", "position_id": "P2", "order_id": "2"
            }}] * copies, "has_more": False}
        return {"items": [], "has_more": False}
    api.get = get
    ee.reconcile(api, {"positions": [], "orders": []}, NOW)
    with Session() as session:
        trade = session.get(ExnessExecutionTrade, (api.account_id, "XAUUSD", old))
        assert trade.snapshot["status"] == ("closed" if copies == 1 else "submitted")
        assert trade.snapshot.get("position_id") == ("P2" if copies == 1 else None)

def test_journal_records_why_bot_stayed_out(monkeypatch):
    api = dummy_api()
    seed_executor(api, monkeypatch)
    def blocked(session, now, symbol="XAUUSD"):
        session.info["execution_evaluation"] = {
            "candle_time": NOW - 900, "evaluated": True, "mode": "explore", "selected": "squeeze_breakout",
            "confirmed": [{"strategy": "squeeze_breakout", "side": "short", "shadow": False}]}
        return None
    monkeypatch.setattr(ee, "evaluate", blocked)
    with Session.begin() as session:
        row = session.get(ExnessExecutionState, api.account_id)
        row.snapshot = {**row.snapshot, "history_until": 0}  # catching up -> blocked
    assert ee._execution_tick(api, now=NOW) == "history_catching_up"
    with Session() as session:
        journal = session.get(ExnessExecutionState, api.account_id).snapshot["journal"]
    assert journal[-1]["outcome"] == "blocked:history_catching_up"
    assert journal[-1]["selected"] == "squeeze_breakout"
    rows = journal + [{**journal[-1], "time": 1, "outcome": "order_sent"}]
    assert ee.funnel(rows)["orders"] == 1 and ee.funnel(rows)["selected"] == 2

@pytest.mark.parametrize("existing_gold", [False, True])
def test_multi_symbol_tick_outcome_handles_dict_result(monkeypatch, tmp_path, existing_gold):
    monkeypatch.chdir(tmp_path)
    api = dummy_api()
    seed_executor(api, monkeypatch)
    monkeypatch.setenv("EXNESS_SYMBOLS", "XAUUSD,EURUSD")
    outcomes = {"XAUUSD": "waiting_signal", "EURUSD": "paused"}
    monkeypatch.setattr(ee, "_execution_tick", lambda api, now: outcomes)
    evaluated = []
    def evaluate(session, now, symbol):
        evaluated.append(symbol)
        session.info["execution_evaluation"] = {"candle_time": now - 900, "evaluated": True}
        return None
    monkeypatch.setattr(ee, "evaluate", evaluate)
    if existing_gold:
        with Session.begin() as session:
            state = session.get(ExnessExecutionState, api.account_id)
            ee.record_journal(state, {"candle_time": NOW - 900, "evaluated": True}, "order_sent", NOW)
    assert ee.execution_tick(api, now=NOW) == outcomes
    assert evaluated == (["EURUSD"] if existing_gold else ["XAUUSD", "EURUSD"])
    with Session() as session:
        journal = session.get(ExnessExecutionState, api.account_id).snapshot["journal"]
    rows = {row["symbol"]: row for row in journal}
    assert len(journal) == 2
    assert rows["XAUUSD"]["outcome"] == ("order_sent" if existing_gold else "no_signal")
    assert rows["EURUSD"]["outcome"] == "blocked:paused"
    api.client.request.assert_not_called()

def test_journal_preserves_error_until_fresh_evaluation():
    state = SimpleNamespace(snapshot={})
    detail = {"symbol": "EURUSD", "candle_time": NOW - 900, "evaluated": True}
    ee.record_journal(state, detail, "no_signal", NOW)
    error = "TypeError: test failure"
    ee.record_journal(state, {**detail, "evaluated": False, "reason": error}, "worker_error", NOW + 1)
    ee.record_journal(state, {**detail, "evaluated": False}, "unavailable:outside_signal_window", NOW + 2)
    row = state.snapshot["journal"][0]
    assert row["outcome"] == "worker_error"
    assert row["reason"] == error
    ee.record_journal(state, detail, "no_signal", NOW + 3)
    row = state.snapshot["journal"][0]
    assert row["outcome"] == "no_signal"
    assert row["reason"] is None

@pytest.mark.parametrize("outcome", ["no_signal", "order_sent", "execution_error", "worker_error", "order_planned"])
def test_journal_records_transitions_not_repeated_unavailable_checks(outcome):
    state = SimpleNamespace(snapshot={})
    detail = {"symbol": "NZDUSD", "candle_time": NOW - 900, "evaluated": True}
    ee.record_journal(state, detail, outcome, NOW)
    unavailable = {**detail, "evaluated": False}
    for offset in range(1, 20):
        ee.record_journal(state, unavailable, "unavailable:outside_signal_window", NOW + offset)
    row = state.snapshot["journal"][0]
    assert row["symbol"] == "NZDUSD"
    assert row["outcome"] == outcome
    assert row["events"] == [{"at": NOW, "outcome": outcome},
                             {"at": NOW + 1, "outcome": "unavailable:outside_signal_window"}]
    ee.record_journal(state, detail, outcome, NOW + 20)
    assert state.snapshot["journal"][0]["events"][-1] == {"at": NOW + 20, "outcome": outcome}

def test_instrument_error_does_not_pollute_gold_journal(monkeypatch):
    api = dummy_api()
    seed_executor(api, monkeypatch)
    monkeypatch.setenv("EXNESS_SYMBOLS", "XAUUSD,EURUSD")
    ee.record_error(api.account_id, ExnessError("instrument unavailable"), NOW, symbol="EURUSD")
    with Session() as session:
        rows = session.get(ExnessExecutionState, api.account_id).snapshot["journal"]
    assert len(rows) == 1
    assert rows[0]["symbol"] == "EURUSD"
    assert rows[0]["outcome"] == "worker_error"

def test_summary_exposes_active_error_scope_and_ignores_cleared_detail(monkeypatch):
    api = dummy_api()
    seed_executor(api, monkeypatch)
    monkeypatch.setenv("EXNESS_SYMBOLS", "XAUUSD,EURUSD")
    monkeypatch.setattr(ee, "learning_summary", lambda **_: {})
    ee.record_error(api.account_id, ExnessError("Market execution unavailable for EURUSD"), NOW, symbol="EURUSD")
    summary = ee.execution_summary(offset=0, limit=50, symbol="XAUUSD")
    assert summary["error"] == "ExnessError"
    assert summary["error_msg"] == "ExnessError: Market execution unavailable for EURUSD"
    assert summary["error_at"] == NOW
    assert summary["error_symbol"] == "EURUSD"
    ee.record_error(api.account_id, ExnessError("account unavailable"), NOW + 1)
    assert ee.execution_summary(offset=0, limit=50, symbol="XAUUSD")["error_symbol"] is None
    with Session.begin() as session:
        state = session.get(ExnessExecutionState, api.account_id)
        state.snapshot = {**state.snapshot, "error": None}
    summary = ee.execution_summary(offset=0, limit=50, symbol="XAUUSD")
    assert all(summary[key] is None for key in ("error", "error_msg", "error_at", "error_symbol"))

def test_rate_limit_is_labeled_not_order_error(monkeypatch):
    api = dummy_api()
    seed_executor(api, monkeypatch)
    with Session.begin() as s:
        st = s.get(ExnessExecutionState, api.account_id)
        ee.record_journal(st, {"candle_time": NOW - 900}, "no_signal", NOW)
    ee.record_error(api.account_id, ExnessError("limit", 5, rate_limited=True), NOW)
    with Session() as s:
        row = s.get(ExnessExecutionState, api.account_id).snapshot["journal"][-1]
    assert row["outcome"] == "api_rate_limited"

def test_late_retry_blocked_when_price_drifts():
    sig = {"available_at": NOW - 200, "ref_price": 4000.0, "stop": 3990.0}
    ee.late_drift_ok(sig, 4002.0, NOW)  # 0.2R ok
    with pytest.raises(ExnessError, match="price_drift"):
        ee.late_drift_ok(sig, 4003.0, NOW)  # 0.3R
    ee.late_drift_ok({**sig, "available_at": NOW - 60}, 4050.0, NOW)  # in normal window: unchanged behavior

def test_prepare_cached_across_clients():
    from backend.app.exness_demo_client import ExnessDemoClient
    api = dummy_api()
    calls = []
    orig_cond = api.condition
    api.condition = lambda sym: (calls.append("condition"), orig_cond(sym))[1]
    orig_acc = api.account
    api.account = lambda: (calls.append("account"), orig_acc())[1]
    orig_lim = api.limits
    api.limits = lambda: (calls.append("limits"), orig_lim())[1]

    ExnessDemoClient(api).prepare(require_flat=False)
    assert calls == ["account", "limits", "condition"]
    calls.clear()
    ExnessDemoClient(api).prepare(require_flat=False)
    assert calls == []

def test_prepare_shares_account_checks_but_not_conditions_across_symbols(monkeypatch):
    from backend.app.exness_demo_client import PREPARE_TTL, ExnessDemoClient
    from backend.app.forex_symbols import SYMBOLS
    api = dummy_api()
    api.account = MagicMock(wraps=api.account)
    api.limits = MagicMock(wraps=api.limits)
    original = api.condition("XAUUSD")
    api.condition = MagicMock(side_effect=lambda symbol: {**original, "instrument": symbol})
    clock = [1000.0]
    monkeypatch.setattr(ee.time, "monotonic", lambda: clock[0])
    for symbol in SYMBOLS:
        assert ExnessDemoClient(api).prepare(symbol, require_flat=False)["instrument"] == symbol
    assert api.account.call_count == api.limits.call_count == 1
    assert api.condition.call_count == len(SYMBOLS)
    clock[0] += PREPARE_TTL
    ExnessDemoClient(api).prepare("EURJPY", require_flat=False)
    assert api.account.call_count == api.limits.call_count == 2
    assert api.condition.call_count == len(SYMBOLS) + 1

@pytest.mark.parametrize("attribute,value", [
    ("account_id", "456"), ("base_url", "https://other.exness.com"), ("api_key", "other"),
])
def test_prepare_account_cache_does_not_survive_identity_change(attribute, value):
    from backend.app.exness_demo_client import ExnessDemoClient
    api = dummy_api()
    ExnessDemoClient(api).prepare(require_flat=False)
    setattr(api, attribute, value)
    api.account = MagicMock(return_value={"is_demo": False})
    with pytest.raises(ExnessError, match="demo account"):
        ExnessDemoClient(api).prepare(require_flat=False)
    api.account.assert_called_once()

def test_rate_limit_before_submit_allows_retry(monkeypatch):
    api = dummy_api()
    seed_executor(api, monkeypatch)
    calls = {"n": 0}
    real = api.account
    def flaky():
        calls["n"] += 1
        if calls["n"] == 3:
            raise ExnessError("limit", 5, rate_limited=True)
        return real()
    api.account = flaky
    with pytest.raises(ExnessError):
        ee._execution_tick(api, now=NOW)
    api.client.request.assert_not_called()
    with Session() as s:
        assert s.get(ExnessExecutionTrade, (api.account_id, "XAUUSD", NOW - 900)).snapshot["retryable_pre_submit"]
    assert ee._execution_tick(api, now=NOW + 10) == "submitted"

def test_prepare_cache_expires_and_rejects_env_change(monkeypatch):
    from backend.app.exness_demo_client import PREPARE_TTL, ExnessDemoClient
    api = dummy_api()
    clock = [1000.0]
    monkeypatch.setattr(ee.time, "monotonic", lambda: clock[0])
    ExnessDemoClient(api).prepare(require_flat=False)
    monkeypatch.setenv("EXNESS_ENVIRONMENT", "real")
    with pytest.raises(ExnessError, match="Demo XAUUSD"):
        ExnessDemoClient(api).prepare(require_flat=False)
    monkeypatch.setenv("EXNESS_ENVIRONMENT", "demo")
    api.account = lambda: {"is_demo": False}
    clock[0] += PREPARE_TTL
    with pytest.raises(ExnessError, match="demo account"):
        ExnessDemoClient(api).prepare(require_flat=False)

def test_cached_prepare_still_requires_flat_and_returns_copy():
    from backend.app.exness_demo_client import ExnessDemoClient
    api = dummy_api(positions=[{"position_id": "1"}])
    condition = ExnessDemoClient(api).prepare(require_flat=False)
    condition["volume_min"] = "99"
    assert ExnessDemoClient(api).prepare(require_flat=False)["volume_min"] == "0.01"
    with pytest.raises(ExnessError, match="empty demo account"):
        ExnessDemoClient(api).prepare(require_flat=True)

def test_retry_expires_without_submission(monkeypatch):
    api = dummy_api()
    seed_executor(api, monkeypatch)
    api.account = MagicMock(side_effect=[dummy_api().account(), dummy_api().account(), ExnessError("limit", 5, rate_limited=True)])
    with pytest.raises(ExnessError):
        ee._execution_tick(api, now=NOW)
    api.account = dummy_api().account
    assert ee._execution_tick(api, now=NOW + ee.SIGNAL_WINDOW + 1) != "submitted"
    api.client.request.assert_not_called()
    with Session() as session:
        assert session.get(ExnessExecutionTrade, (api.account_id, "XAUUSD", NOW - 900)).snapshot["status"] == "skipped"

def test_late_retry_window_bounds():
    sig = {"available_at": NOW, "ref_price": 4000.0, "stop": 3990.0}
    ee.late_drift_ok(sig, 4002.5, NOW + 300)
    with pytest.raises(ExnessError, match="signal_expired"):
        ee.late_drift_ok(sig, 4000.0, NOW + 301)


def test_execution_summary_setups_view(monkeypatch):
    api = dummy_api()
    seed_executor(api, monkeypatch)
    with Session.begin() as session:
        session.add(ExnessExecutionTrade(
            account=api.account_id, symbol="XAUUSD", signal_time=NOW - 1800,
            snapshot={"signal": {"strategy": "hast4_split"}, "legs": [
                {"role": "tp1", "position_id": "801", "body": {"side": "buy"}},
                {"role": "runner", "position_id": "802", "body": {"side": "buy"}},
            ]}
        ))
        session.add(ExnessDeal(account=api.account_id, deal_id="d1", position_id="801", event_time=NOW - 1790,
                               snapshot={"type": "open", "instrument": "XAUUSD", "direction": "buy",
                                         "price": "4000.0", "volume": "0.05", "event_time": NOW - 1790}))
        session.add(ExnessDeal(account=api.account_id, deal_id="d2", position_id="802", event_time=NOW - 1790,
                               snapshot={"type": "open", "instrument": "XAUUSD", "direction": "buy",
                                         "price": "4000.0", "volume": "0.05", "event_time": NOW - 1790}))
        session.add(ExnessDeal(account=api.account_id, deal_id="d3", position_id="801", event_time=NOW - 1000,
                               snapshot={"type": "close", "instrument": "XAUUSD", "direction": "buy",
                                         "price": "4005.0", "volume": "0.05", "event_time": NOW - 1000,
                                         "profit": {"amount": "25.0"}}))
    with Session.begin() as session:
        state = session.get(ExnessExecutionState, api.account_id)
        state.snapshot = {**state.snapshot, "broker": {"positions": [
            {"instrument": "XAUUSD", "position_id": "802", "direction": "buy", "volume": "0.05",
             "open_price": "4000.0", "create_time": "2026-10-26T14:00:00Z"}
        ]}}
    summary = ee.execution_summary(offset=0, limit=50, symbol="XAUUSD", view="setups")
    assert summary["view"] == "setups"
    assert len(summary["groups"]) == 1
    group = summary["groups"][0]
    assert group["id"] == f"setup:{api.account_id}:XAUUSD:{NOW - 1800}"
    assert group["status"] == "open"
    assert group["open_count"] == 1
    assert group["ticket_count"] == 2
    assert group["initial_volume"] == 0.10
    assert group["remaining_volume"] == 0.05
    assert group["realized_pnl"] == 25.0
    assert len(summary["open_groups"]) == 1

def test_reconcile_labels_breakeven_stop_exit():
    api = dummy_api()
    leg = {"status": "submitted", "key": "rr-be", "recorded_at": NOW - 60, "ack": {"operation_id": "9"},
           "body": {"side": "sell", "stop_loss_price": "157.900", "take_profit_price": "157.000"},
           "broker_stop": "157.574"}
    with Session.begin() as session:
        session.add(ExnessExecutionTrade(account=api.account_id, signal_time=NOW - 60, snapshot=leg))
        session.add(ExnessDeal(account=api.account_id, deal_id="o", position_id="P", event_time=NOW - 50,
                               snapshot={"type": "open", "price": "157.574", "position_id": "P"}))
        session.add(ExnessDeal(account=api.account_id, deal_id="c", position_id="P", event_time=NOW - 10,
                               snapshot={"type": "close", "price": "157.574", "position_id": "P"}))
    def get(path, params=None):
        if "/operations/" in path:
            return {"status": "accepted"}
        if path.endswith("/orders"):
            return {"items": [{"order": {"comment": "rr-be", "instrument": "XAUUSD",
                                         "state": "filled", "position_id": "P", "order_id": "1"}}], "has_more": False}
        return {"items": [], "has_more": False}
    api.get = get
    ee.reconcile(api, {"positions": [], "orders": []}, NOW)
    with Session() as session:
        saved = session.get(ExnessExecutionTrade, (api.account_id, "XAUUSD", NOW - 60)).snapshot
    assert saved["status"] == "closed" and not saved["take_profit_filled"]
    assert saved["close_reason"] == "breakeven_stop"


@pytest.mark.parametrize("message", ["poor_rr", "price_drift: virtual entry moved beyond 0.25R", "new_check: " + "x" * 250])
def test_intent_records_preflight_reason_code_and_detail(monkeypatch, tmp_path, message):
    from backend.app.db import ExnessEntryIntent
    from backend.app.forex_entry_intents import execute_trigger
    api = dummy_api()
    identifier = seed_virtual_entry(api, monkeypatch, tmp_path)
    def reject(*args, **kwargs):
        raise ExnessError(message)
    from backend.app.exness_demo_client import ExnessDemoClient
    monkeypatch.setattr(ExnessDemoClient, "strategy_body", reject)
    execute_trigger(api, identifier, "XAUUSD")
    api.client.request.assert_not_called()
    with Session() as session:
        row = session.get(ExnessEntryIntent, identifier)
        assert row.status == "cancelled"
        assert row.snapshot["reason"] == "preflight_failed"
        assert row.snapshot["reason_code"] == message.split(":")[0]
        assert row.snapshot["reason_detail"] == message[:200]
        from backend.app.forex_entry_intents import summary
        result = summary(session, api.account_id, "XAUUSD")[0]
        assert result["reason_detail"] == message[:200]


@pytest.mark.parametrize("outcome", ["history_catching_up", "executor_active", "calibration_or_executor_active", "disabled"])
def test_trigger_cancel_records_executor_outcome(monkeypatch, tmp_path, outcome):
    from backend.app.db import ExnessEntryIntent
    from backend.app.forex_entry_intents import execute_trigger
    api = dummy_api()
    identifier = seed_virtual_entry(api, monkeypatch, tmp_path)
    monkeypatch.setattr(ee, "execution_tick", lambda *args, **kwargs: outcome)
    execute_trigger(api, identifier, "XAUUSD")
    with Session() as session:
        row = session.get(ExnessEntryIntent, identifier)
        assert row.status == "cancelled"
        assert row.snapshot["reason"] == "trigger_preflight_failed"
        assert row.snapshot["reason_code"] == outcome
        assert row.snapshot["reason_detail"] == outcome
    api.client.request.assert_not_called()


@pytest.mark.parametrize("status", ["triggered", "submitting"])
def test_trigger_error_keeps_uncertain_submission_and_reraises(monkeypatch, tmp_path, status):
    from backend.app.db import ExnessEntryIntent
    from backend.app.forex_entry_intents import execute_trigger
    api = dummy_api()
    identifier = seed_virtual_entry(api, monkeypatch, tmp_path)
    with Session.begin() as session:
        session.get(ExnessEntryIntent, identifier).status = status
    def fail(*args, **kwargs):
        raise RuntimeError("private exception detail")
    monkeypatch.setattr(ee, "execution_tick", fail)
    with pytest.raises(RuntimeError, match="private exception detail"):
        execute_trigger(api, identifier, "XAUUSD")
    with Session() as session:
        row = session.get(ExnessEntryIntent, identifier)
        assert row.status == ("cancelled" if status == "triggered" else "uncertain")
        if status == "triggered":
            assert row.snapshot["reason_code"] == "execution_error"
            assert row.snapshot["reason_detail"] == "execution_error: RuntimeError"
        else:
            assert not row.snapshot.get("reason_code")
    api.client.request.assert_not_called()

def test_virtual_entry_waits_for_main_loop_lock_then_submits_once(monkeypatch, tmp_path):
    import fcntl
    import hashlib
    import threading
    import time

    from backend.app.db import ExnessEntryIntent
    from backend.app.forex_entry_intents import execute_trigger
    monkeypatch.setenv("EXNESS_DEMO_MAX_POSITIONS", "6")
    monkeypatch.chdir(tmp_path)
    (tmp_path / "research-output").mkdir()
    api = dummy_api()
    identifier = seed_virtual_entry(api, monkeypatch, tmp_path)
    fingerprint = hashlib.sha256(api.account_id.encode()).hexdigest()[:16]
    lock = (tmp_path / "research-output" / f"exness-probe-{fingerprint}.lock").open("a")
    fcntl.flock(lock, fcntl.LOCK_EX)
    threading.Timer(0.3, lambda: (fcntl.flock(lock, fcntl.LOCK_UN), lock.close())).start()
    started = time.monotonic()
    execute_trigger(api, identifier, "XAUUSD")
    assert time.monotonic() - started >= 0.25
    assert api.client.request.call_count == 1
    with Session() as session:
        assert session.get(ExnessEntryIntent, identifier).status == "submitted"

def test_virtual_entry_lock_wait_is_bounded_and_never_submits(monkeypatch, tmp_path):
    import fcntl
    import hashlib

    from backend.app.db import ExnessEntryIntent
    from backend.app.forex_entry_intents import execute_trigger
    monkeypatch.chdir(tmp_path)
    (tmp_path / "research-output").mkdir()
    api = dummy_api()
    identifier = seed_virtual_entry(api, monkeypatch, tmp_path)
    clock = iter(x * 7 for x in range(100))
    monkeypatch.setattr(ee.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(ee.time, "sleep", lambda *_: None)
    fingerprint = hashlib.sha256(api.account_id.encode()).hexdigest()[:16]
    with (tmp_path / "research-output" / f"exness-probe-{fingerprint}.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        execute_trigger(api, identifier, "XAUUSD")
    with Session() as session:
        row = session.get(ExnessEntryIntent, identifier)
        assert row.status == "cancelled"
        assert row.snapshot["reason_code"] == "calibration_or_executor_active"
    api.client.request.assert_not_called()

def test_virtual_entry_fill_times_come_only_from_trade_and_deals(monkeypatch, tmp_path):
    from backend.app.db import ExnessEntryIntent
    from backend.app.forex_entry_intents import reconcile_intents
    api = dummy_api()
    identifier = seed_virtual_entry(api, monkeypatch, tmp_path)
    with Session.begin() as session:
        row = session.get(ExnessEntryIntent, identifier)
        candle = row.snapshot["signal"]["candle_time"]
        session.add(ExnessExecutionTrade(account=api.account_id, symbol="XAUUSD", signal_time=candle, snapshot={
            "signal": {"entry_intent_id": identifier},
            "legs": [{"status": "open", "position_id": "p1", "submitted_at": NOW + 1},
                     {"status": "open", "position_id": "p2"}]}))
        session.add(ExnessDeal(account=api.account_id, deal_id="d1", position_id="p1", event_time=NOW + 2,
                               snapshot={"type": "open", "price": "4000.1", "volume": "0.1", "event_time": NOW + 2}))
        session.add(ExnessDeal(account=api.account_id, deal_id="d2", position_id="p2", event_time=NOW + 3,
                               snapshot={"type": "open", "price": "4000.2", "volume": "0.1"}))
    reconcile_intents(api.account_id, NOW + 5, identifier=identifier)
    with Session() as session:
        fills = session.get(ExnessEntryIntent, identifier).snapshot["broker_fills"]
    assert fills[0]["submitted_at"] == NOW + 1 and fills[0]["filled_at"] == NOW + 2
    assert "submitted_at" not in fills[1] and "filled_at" not in fills[1]


def test_split_runner_shrinks_to_remaining_budget_instead_of_skipping(monkeypatch):
    monkeypatch.setenv("EXNESS_DEMO_MAX_POSITIONS", "6")
    api = dummy_api(equity="10000")
    seed_executor(api, monkeypatch)
    assert ee._execution_tick(api, now=NOW) == "submitted"
    with Session() as session:
        row = session.get(ExnessExecutionTrade, (api.account_id, "XAUUSD", NOW - 900))
    api.get = route((), (), "20000")
    monkeypatch.setattr(ee, "live_quote", lambda *_: {"bid": 4000.0, "ask": 4000.6, "time": NOW, "received_at": NOW})
    condition = {**api.condition("XAUUSD"), "account_currency": "USD", "observed_at": NOW,
                 "account_tag": account_tag(api.account_id), "commission_per_unit": 0}
    ee.submit_leg(ee.ExnessDemoClient(api), row, 1, row.snapshot["legs"][1], condition, NOW)
    sent = json.loads(api.client.request.call_args.kwargs["content"])
    # Ngân sách 100 USD, leg 0 đã dùng 49.5, còn 50.5 / 5.85 USD mỗi 0.01 lot => 0.08 (< 0.09 kế hoạch).
    assert sent["volume"] == "0.08"
    with Session() as session:
        leg = session.get(ExnessExecutionTrade, (api.account_id, "XAUUSD", NOW - 900)).snapshot["legs"][1]
    assert leg["volume_adjustment"] == {"planned": "0.09", "submitted": "0.08", "reason": "remaining_risk_budget"}
