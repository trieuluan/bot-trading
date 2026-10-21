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
        row = session.get(ExnessExecutionTrade, (api.account_id, "XAUUSD", NOW - 900))
        assert [leg["status"] for leg in row.snapshot["legs"]] == ["submitted", "planned"]

@pytest.mark.parametrize("symbol_count,total_count", [(5, 5), (0, 65)])
def test_entire_split_plan_must_fit_account_and_symbol_limits(monkeypatch, symbol_count, total_count):
    monkeypatch.setenv("EXNESS_DEMO_MAX_POSITIONS", "66")
    monkeypatch.setenv("EXNESS_DEMO_MAX_POSITIONS_PER_SYMBOL", "6")
    monkeypatch.setenv("EXNESS_DEMO_MAX_LOT", "0.10")
    positions = [{"position_id": str(index), "instrument": "XAUUSD" if index < symbol_count else "EURUSD"}
                 for index in range(total_count)]
    api = dummy_api(positions=positions)
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
    api = dummy_api()
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
    assert calls == ["account", "condition", "limits"]
    calls.clear()
    ExnessDemoClient(api).prepare(require_flat=False)
    assert calls == []

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
