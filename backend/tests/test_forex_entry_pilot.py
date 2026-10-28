"""Tests for Forex Entry Policy (Limit/Stop ảo) lifecycle, validation and execution barriers."""

import copy
import json
import random
from types import SimpleNamespace

import pytest

from backend.app.db import ExnessEntryIntent, Session
from backend.app.forex_entry_intents import (
    cancel_waiting,
    create_intent,
)
from backend.app.forex_entry_pilot import initialize, read_ledger
from backend.app.forex_entry_policy import (
    SCHEMA,
    entry_node,
    levels,
    observe,
    replay_touch,
    tree_id,
    validate_tree,
    with_entry,
)
from backend.app.forex_htf_v2 import g0_trees


def waiting(side="long"):
    return {"status": "waiting", "side": side, "limit_price": 99.0 if side == "long" else 102.0,
            "stop_price": 102.0 if side == "long" else 99.0, "available_at": 1000, "expires_at": 2000}


def quote(stamp, bid=100.0, ask=100.5):
    return {"time": stamp, "received_at": stamp, "bid": bid, "ask": ask}


@pytest.mark.parametrize("side,branch,bid,ask", [
    ("long", "limit", 98.8, 99), ("long", "stop", 101.5, 102),
    ("short", "limit", 102, 102.5), ("short", "stop", 99, 99.5),
])
def test_bid_ask_edges_and_winner_never_changes(side, branch, bid, ask):
    original = waiting(side)
    armed = observe(original, quote(1005), 1005)
    triggered = observe(armed, quote(1006, bid, ask), 1006)
    assert triggered["status"] == "triggered" and triggered["winner"] == branch
    assert observe(triggered, quote(1007, 50, 150), 1007) == triggered
    assert original == waiting(side)


@pytest.mark.parametrize("change,reason", [
    ({"connected": False}, "stream_disconnected"), ({"allowed": False}, "control_disabled"),
    ({"invalidated": True}, "setup_invalidated"),
])
def test_quote_gate_cancels(change, reason):
    assert observe(waiting(), quote(1005), 1005, **change)["reason"] == reason


def test_duplicate_backward_gap_future_and_expiry():
    armed = observe(waiting(), quote(1005), 1005)
    assert observe(armed, quote(1005, 98, 99), 1005) == armed
    assert observe(armed, quote(1004), 1005)["reason"] == "quote_out_of_order"
    assert observe(armed, quote(1008), 1008)["status"] == "waiting"  # sparse Asian-session ticks
    assert observe(armed, quote(1011), 1011)["status"] == "waiting"  # short gap, level not touched: keep waiting
    assert observe(armed, quote(1011, 98, 98.5), 1011)["reason"] == "level_crossed_during_gap"
    assert observe(armed, quote(1070), 1070)["reason"] == "quote_gap"
    assert observe(armed, quote(1007), 1006)["reason"] == "quote_stale"
    assert observe(armed, quote(2000), 2000)["status"] == "expired"


@pytest.mark.parametrize("bad", [[], {}, None, True, "unknown"])
def test_invalid_policy_fails_with_value_error(bad):
    with pytest.raises(ValueError):
        entry_node(bad)


def test_v3_unchanged_v4_role_validation_and_crossover():
    from backend.app.forex_htf_v2 import crossover, mutate
    from backend.app.forex_htf_v2 import tree_id as identifier
    original = g0_trees()[0]
    frozen = copy.deepcopy(original)
    before = identifier(original)
    child = with_entry(original)
    assert original == frozen and identifier(original) == before
    for seed in range(30):
        child = mutate(child, random.Random(seed))
        validate_tree(child)
        crossed = crossover(child, g0_trees()[1], random.Random(seed))
        validate_tree(crossed)
    child["root"]["children"][2:4] = reversed(child["root"]["children"][2:4])
    with pytest.raises(ValueError):
        validate_tree(child)


def test_all_symbols_fixed_cohort_ids_and_corruption(tmp_path, monkeypatch):
    from backend.app.forex_symbols import SYMBOLS
    for symbol in SYMBOLS:
        path = tmp_path / f"{symbol}.json"
        monkeypatch.setattr("backend.app.forex_entry_pilot.ledger_path", lambda *_: path)
        initialize(path, "123", symbol, g0_trees(symbol), 1000)
        first = read_ledger("123", symbol)
        assert len({tree_id(tree) for tree in first["population"]}) == 56
        assert read_ledger("123", symbol) == first
        assert initialize(path, "123", symbol, [], 2000) == first
        broken = copy.deepcopy(first)
        broken["manifest"]["source_sha256"] = "wrong"
        path.write_text(json.dumps(broken))
        with pytest.raises(ValueError, match="manifest"):
            read_ledger("123", symbol)
        assert json.loads(path.read_text()) == broken


def replay_signal():
    return {"symbol": "XAUUSD", "strategy": "hast4_fixture", "side": "long", "stop": 95.0, "target": 115.0,
            "candle_time": 900, "available_at": 1800, "htf_ast_v2": True, "shadow_only": True,
            "generated": True, "exit_policy": "fixed_structural", "management_frame": "H1",
            "htf_structure": {"atr": 2.0, "invalidation": 95.5, "target_reason": "H1 swing", "setup_time": 0},
            "entry_policy": {"policy": "limit_or_stop", "limit_price": 99.0, "stop_price": 102.0,
                             "available_at": 1800, "expires_at": 5400}}


def test_replay_no_preentry_tp_or_future_candles(monkeypatch):
    from backend.app.forex_entry_replay import run
    monkeypatch.setattr("backend.app.forex_buffer_study.session_closed_ahead", lambda *_: False)
    monkeypatch.setenv("EXNESS_DEMO_RISK_PER_TRADE_USD", "25")
    condition = {"contract_size": "100", "volume_min": "0.01", "volume_step": "0.01", "volume_max": "10",
                 "point_digits": 3, "instrument": "XAUUSD"}
    costs = {"commission_per_unit": 0, "slippage_bps": 0}
    def bar(stamp, low, high):
        return SimpleNamespace(time=stamp, open=100, close=100, low=low, high=high)
    bars = [bar(1800, 98, 116)]
    unknown = run(replay_signal(), bars, bars, [], [], condition, costs, 1860)
    assert unknown["status"] == "ambiguous" and unknown["reason"] == "entry_order_unknown"
    assert run(replay_signal(), bars, bars, [], [], condition, costs, 1859)["status"] == "pending"
    signal = replay_signal()
    signal["entry_policy"]["stop_price"] = None
    unknown = run(signal, bars, bars, [], [], condition, costs, 1860)
    assert unknown["status"] == "ambiguous" and unknown["reason"] == "entry_exit_order_unknown"
    assert unknown["lower_r"] < 0 < unknown["upper_r"]
    bars[0].high = 101
    entered = run(signal, bars, bars, [], [], condition, costs, 1860)
    assert entered["status"] == "open" and entered["entry"] == 99
    assert bars[0].open == 100
    assert run(signal, [bar(1860, 99, 101)], [bar(1860, 99, 101)], [], [], condition, costs, 1920)["reason"] == "replay_data_gap"


def test_m15_fallback_market_waits_for_first_closed_bar(monkeypatch):
    from backend.app.forex_entry_replay import run
    monkeypatch.setattr("backend.app.forex_buffer_study.session_closed_ahead", lambda *_: False)
    monkeypatch.setenv("EXNESS_DEMO_RISK_PER_TRADE_USD", "25")
    condition = {"contract_size": "100", "volume_min": "0.01", "volume_step": "0.01", "volume_max": "10",
                 "point_digits": 3, "instrument": "XAUUSD"}
    costs = {"commission_per_unit": 0, "slippage_bps": 0}
    signal = replay_signal()
    signal["entry_policy"] = {"policy": "market", "limit_price": None, "stop_price": None,
                              "available_at": 1800, "expires_at": 1920}
    bar = SimpleNamespace(time=1800, open=100, close=100, low=99.5, high=100.5)
    # Market window (120s) is shorter than an M15 bar: no verdict until the bar closes.
    assert run(signal, [], [], [], [], condition, costs, 2000, duration=900)["status"] == "pending"
    entered = run(signal, [bar], [bar], [], [], condition, costs, 2700, duration=900)
    assert entered["status"] == "open" and entered["execution"]["entry"] == 100
    # Limit that never touches still expires once evaluated bars pass its deadline.
    limit = replay_signal()
    limit["entry_policy"]["stop_price"] = None
    bars = [SimpleNamespace(time=stamp, open=100, close=100, low=99.5, high=100.5) for stamp in range(1800, 5400, 900)]
    assert run(limit, bars, bars, [], [], condition, costs, 6300, duration=900)["status"] == "expired"

def test_v4_tree_schema_hash_and_mutation():
    v3 = g0_trees()[0]
    v4 = with_entry(v3, entry_node("limit_or_stop"))
    assert v4["schema"] == SCHEMA
    assert validate_tree(v4) == len(v3["root"]["children"]) + 1
    identifier = tree_id(v4)
    assert identifier.startswith("hast4_")
    assert identifier != tree_id(with_entry(v3, entry_node("limit_setup_mid")))


def test_levels_and_validation():
    candle = SimpleNamespace(time=1000, open=100.0, high=102.0, low=98.0, close=100.0)
    signal = {
        "side": "long", "stop": 95.0, "target": 110.0, "available_at": 1900,
        "htf_structure": {"level": 98.0, "atr": 2.0, "setup_time": 1000}
    }
    res = levels(signal, candle, entry_node("limit_or_stop"))
    assert res["limit_price"] == 99.0
    assert res["stop_price"] == 102.1
    assert res["expires_at"] == 1900 + 4 * 900


def test_observe_first_touch_and_cancel_conditions():
    payload = {
        "status": "waiting", "side": "long", "limit_price": 99.0, "stop_price": 102.0,
        "available_at": 1000, "expires_at": 2000
    }
    now = 1005
    # level crossed before tracking begins -> cancelled
    bad = observe(payload, {"time": now, "received_at": now, "bid": 98.5, "ask": 98.8}, now)
    assert bad["status"] == "cancelled"
    assert bad["reason"] == "level_already_crossed"

    # normal start within range
    mid = observe(payload, {"time": now, "received_at": now, "bid": 100.0, "ask": 100.5}, now)
    assert mid["status"] == "waiting"

    # stale quote (>2s) -> cancelled
    stale = observe(mid, {"time": now, "received_at": now + 3, "bid": 100.0, "ask": 100.5}, now + 3)
    assert stale["status"] == "cancelled"
    assert stale["reason"] == "quote_stale"

    # first branch hit: Limit ask <= 99.0
    hit = observe(mid, {"time": now + 1, "received_at": now + 1, "bid": 98.8, "ask": 99.0}, now + 1)
    assert hit["status"] == "triggered"
    assert hit["winner"] == "limit"


def test_replay_touch_intrabar_and_ambiguous():
    entry = {"available_at": 1000, "expires_at": 2000, "limit_price": 99.0, "stop_price": 102.0}
    bid = SimpleNamespace(time=1000, open=100.0, high=100.5, low=99.5, close=100.0)
    ask = SimpleNamespace(time=1000, open=100.2, high=100.7, low=99.8, close=100.2)
    assert replay_touch(entry, "long", bid, ask)["status"] == "pending"

    # Limit touched
    ask_lim = SimpleNamespace(time=1000, open=100.0, high=100.5, low=98.8, close=99.2)
    res = replay_touch(entry, "long", bid, ask_lim)
    assert res["status"] == "filled"
    assert res["winner"] == "limit"

    # Ambiguous: single bar touches both Limit and Stop
    ask_both = SimpleNamespace(time=1000, open=100.0, high=103.0, low=98.5, close=100.0)
    amb = replay_touch(entry, "long", bid, ask_both)
    assert amb["status"] == "ambiguous"


def test_intent_lifecycle_atomic_and_capacity(tmp_path, monkeypatch):
    monkeypatch.setenv("FOREX_ENTRY_PILOT_ENABLED", "true")
    account, symbol = "123456", "EURUSD"
    trees = g0_trees(symbol)
    pilot_path = tmp_path / "pilot.json"
    monkeypatch.setattr("backend.app.forex_entry_pilot.ledger_path", lambda *_: pilot_path)
    initialize(pilot_path, account, symbol, trees, 1000)
    assert len(read_ledger(account, symbol)["population"]) == 56

    tree = with_entry(trees[0])
    signal = {
        "symbol": symbol, "side": "long", "strategy": tree_id(tree), "setup_id": "setup-1",
        "stop": 95.0, "target": 110.0, "available_at": 1000,
        "entry_policy": {"policy": "limit_or_stop", "limit_price": 99.0, "stop_price": 102.0,
                         "available_at": 1000, "expires_at": 2000},
        "htf_structure": {"atr": 2.0, "level": 98.0, "target_reason": "swing", "setup_time": 900}
    }
    with Session.begin() as session:
        intent = create_intent(session, account, signal, tree, 1000, 2)
        identifier = intent.id
        assert intent.status == "waiting"
        # idempotent by account/symbol/setup/side
        duplicate = create_intent(session, account, signal, tree, 1000, 2)
        assert duplicate.id == intent.id

    with Session.begin() as session:
        assert cancel_waiting(session, account, "manual_test", 1010, symbol) == 1
        row = session.get(ExnessEntryIntent, identifier)
        assert row.status == "cancelled"
@pytest.mark.parametrize("status", ["overflow", "disconnected"])
def test_entry_sse_terminal_event_cleans_subscription(monkeypatch, status):
    import asyncio

    from backend.app import exness_quotes
    monkeypatch.setattr(exness_quotes.quotes, "status", "connected")
    async def read_events():
        response = await exness_quotes.quote_events("XAUUSD", "entry")
        stream = response.body_iterator
        assert json.loads((await anext(stream))[6:])["symbol"] == "XAUUSD"
        queue = next(iter(exness_quotes.entry_subscribers["XAUUSD"]))
        assert queue.maxsize == 256
        queue.put_nowait({"status": status, "symbol": "XAUUSD"})
        assert json.loads((await anext(stream))[6:])["status"] == status
        with pytest.raises(StopAsyncIteration):
            await anext(stream)
        assert not exness_quotes.entry_subscribers["XAUUSD"]
    asyncio.run(read_events())

def test_main_v4_entry_mutation_opt_in_and_crossover(monkeypatch):
    from backend.app.forex_htf_v2 import crossover, mutate, validate_htf2_tree
    monkeypatch.setenv("FOREX_ENTRY_PILOT_ENABLED", "true")
    original = g0_trees()[0]
    children = [mutate(original, random.Random(seed)) for seed in range(50)]
    assert any(child["schema"] == SCHEMA for child in children)
    for child in children:
        validate_htf2_tree(child)
    monkeypatch.setenv("FOREX_ENTRY_PILOT_ENABLED", "false")
    assert all(mutate(original, random.Random(seed))["schema"] == original["schema"] for seed in range(50))
    tree = with_entry(original)
    crossed = crossover(tree, with_entry(g0_trees()[1]), random.Random(42))
    validate_tree(crossed)

def test_replay_reports_every_signal_and_policy(monkeypatch):
    from backend.app.forex_entry_replay import compare
    monkeypatch.setattr("backend.app.forex_buffer_study.session_closed_ahead", lambda *_: False)
    signal = replay_signal()
    signal["htf_structure"]["level"] = 98.0
    condition = {"contract_size": "100", "volume_min": "0.01", "volume_step": "0.01", "volume_max": "10",
                 "point_digits": 3, "instrument": "XAUUSD"}
    candle = {"time": 900, "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0}
    minute = {**candle, "time": 1800, "high": 101.0, "low": 99.5}
    bundle = {"as_of": 1860, "signals": [{"signal": signal, "condition": condition,
                "costs": {"commission_per_unit": 0, "slippage_bps": 0}}],
              "candles": {"XAUUSD": {"15m": {"bid": [candle], "ask": [candle]},
                                      "1m": {"bid": [minute], "ask": [minute]}}}}
    result = compare(bundle)
    assert result["source"] == "offline_replay_not_forward"
    assert len(result["results"]) == 4
    assert result["counts"]["market"] == {"open": 1}
    assert result["counts"]["limit_or_stop"] == {"pending": 1}
    assert minute["open"] == 100.0
    bundle["signals"][0]["condition"]["conversion"] = {"verified": True, "observed_at": 1801}
    assert compare(bundle)["counts"]["market"] == {"excluded": 1}

def test_internal_quotes_authenticate_without_bypassing_password_gate(monkeypatch):
    from starlette.requests import Request

    from backend.app.app_auth import quote_request
    from backend.app.main import authenticated
    monkeypatch.setenv("APP_PASSWORD", "test-only-password")
    request = quote_request("EURUSD", entry=True)
    cookie = request.get_header("Cookie")
    assert cookie and "test-only-password" not in cookie
    assert request.full_url.endswith("symbol=EURUSD&purpose=entry")
    assert authenticated(Request({"type": "http", "headers": [(b"cookie", cookie.encode())]}))
    assert not authenticated(Request({"type": "http", "headers": []}))
    monkeypatch.setenv("EXNESS_QUOTE_BASE_URL", "https://example.com")
    with pytest.raises(ValueError, match="internal API"):
        quote_request("EURUSD")

def test_submit_client_lifecycle_is_independent_of_feed(monkeypatch):
    from unittest.mock import Mock

    from bot import exness_worker
    client = SimpleNamespace(account_id="123", client=Mock())
    monkeypatch.setattr(exness_worker, "connect_exness", lambda: client)
    submit = Mock(side_effect=RuntimeError("submission failed"))
    monkeypatch.setattr("backend.app.forex_entry_intents.execute_trigger", submit)
    with pytest.raises(RuntimeError, match="submission failed"):
        exness_worker.execute_entry("123", "intent", "EURUSD")
    submit.assert_called_once_with(client, "intent", "EURUSD")
    client.client.close.assert_called_once()

def test_entry_sse_overflow_is_terminal_not_silent_drop(monkeypatch):
    import asyncio

    from backend.app import exness_quotes
    queue = asyncio.Queue(maxsize=256)
    monkeypatch.setattr(exness_quotes, "entry_subscribers", {"EURUSD": {queue}})
    for index in range(257):
        exness_quotes.publish_entry({"status": "live", "symbol": "EURUSD", "quote": quote(index)})
    assert queue.qsize() == 1
    assert queue.get_nowait() == {"status": "overflow", "symbol": "EURUSD"}
    exness_quotes.publish_entry({"status": "disconnected", "symbol": "EURUSD"})
    assert queue.get_nowait()["status"] == "disconnected"

def test_pilot_control_never_falls_back_to_market_when_hybrid_rejected(monkeypatch):
    from backend.app import forex_htf_v2 as main
    tree = g0_trees()[0]
    identifier = main.tree_id(tree)
    sample = {"strategy": identifier, "strategy_version": main.logic_version(), "confirm_time": 900,
              "provenance": main.PROVENANCE, "costs": {"verified": True}, "status": "pending", "tree": tree,
              "signal": {**replay_signal(), "strategy": identifier, "side": "long",
                             "htf_structure": {"setup_time": 800, "level": 100.0, "invalidation": 98.0}}}
    ledger = {"population": [tree], "samples": [sample], "last_capture": {"status": "captured", "time": 900}}
    monkeypatch.setattr(main, "read_ledger", lambda *_: ledger)
    monkeypatch.setattr(main, "qualified_trees", lambda *_: [(identifier, {})])
    monkeypatch.setattr("backend.app.forex_entry_pilot.read_ledger", lambda *_: {
        "members": {"counterpart": {"control_tree_id": identifier}}, "samples": []})
    monkeypatch.setenv("FOREX_ENTRY_PILOT_ENABLED", "false")
    assert len(main.execution_candidates("123", 1800, "XAUUSD")) == 1
    monkeypatch.setenv("FOREX_ENTRY_PILOT_ENABLED", "true")
    assert main.execution_candidates("123", 1800, "XAUUSD") == []


@pytest.fixture
def pilot_tick_state(monkeypatch, tmp_path):
    from backend.app import forex_entry_pilot as pilot
    from backend.app.db import ForexInstrumentState
    from backend.app.exness_feed import WORKSPACE
    from backend.app.forex_costs import account_tag

    path = tmp_path / "pilot.json"
    path.write_text("{}")
    ledger = {"population": [], "samples": [{"status": "open", "strategy": "existing",
                                            "setup_id": "existing"}], "since": 2700,
              "last_capture": None}
    frames = {"15m": [SimpleNamespace(time=1800, price_type=side)
                       for side in ("bid", "ask")], "1h": [], "4h": []}
    with Session.begin() as session:
        session.add(ForexInstrumentState(
            workspace_id=WORKSPACE, symbol="USDJPY", checked_at=2700, retry_at=0,
            snapshot={"instrument": "USDJPY", "account_tag": account_tag("123"),
                      "observed_at": 2700, "account_currency": "USD",
                      "quote_currency": "JPY", "commission_per_unit": 0}))
    monkeypatch.setattr(pilot, "ledger_path", lambda *_: path)
    monkeypatch.setattr(pilot, "read_ledger", lambda *_: ledger)
    monkeypatch.setattr("backend.app.exness_observation.load_frames", lambda *_args, **_kwargs: frames)
    monkeypatch.setattr("backend.app.exness_observation.audit_frames", lambda *_: {"status": "checked"})
    monkeypatch.setattr("backend.app.forex_htf_v2.reference_market", lambda *_: {})
    monkeypatch.setattr("backend.app.forex_entry_replay.advance_forward",
                        lambda samples, *_: samples[0].update(status="resolved"))
    return pilot, ledger, frames, path


def conversion_candle(stamp, side):
    from backend.app.db import WorkspaceCandle
    from backend.app.exness_feed import WORKSPACE

    price = 150 if side == "bid" else 150.02
    return WorkspaceCandle(workspace_id=WORKSPACE, symbol="USDJPY", timeframe="15m",
                           price_type=side, time=stamp, open=price, high=price,
                           low=price, close=price)


@pytest.mark.parametrize("conversion_state", ["missing", "stale", "missing_ask"])
def test_pilot_blocks_bad_conversion_and_retries_same_bar(pilot_tick_state, conversion_state):
    pilot, ledger, _, path = pilot_tick_state
    with Session.begin() as session:
        if conversion_state == "stale":
            session.add_all([conversion_candle(-3600, side) for side in ("bid", "ask")])
        elif conversion_state == "missing_ask":
            session.add(conversion_candle(1800, "bid"))

    pilot.tick("123", "USDJPY", now=2710)
    saved = json.loads(path.read_text())
    assert saved["last_capture"]["status"] == "blocked_costs"
    assert "conversion" in saved["last_capture"]["reason"].lower()
    assert len(saved["samples"]) == 1
    assert saved["samples"][0]["status"] == "resolved"

    with Session.begin() as session:
        for side in ("bid", "ask"):
            session.merge(conversion_candle(1800, side))
    pilot.tick("123", "USDJPY", now=2720)
    assert ledger["last_capture"]["status"] == "captured"
    assert json.loads(path.read_text())["last_capture"]["time"] == 1800


@pytest.mark.parametrize("capture_status", [None, "captured"])
def test_pilot_does_not_require_new_costs_without_new_signal(pilot_tick_state, monkeypatch, capture_status):
    pilot, ledger, frames, _ = pilot_tick_state
    if capture_status is None:
        frames["15m"] = []
    else:
        ledger["last_capture"] = {"time": 1800, "status": capture_status}

    def unexpected_cost_check(*_args, **_kwargs):
        pytest.fail("No fresh signal should need conversion costs")

    monkeypatch.setattr("backend.app.forex_conversion.cost_context", unexpected_cost_check)
    pilot.tick("123", "USDJPY", now=2710)
    assert ledger["last_capture"] == (None if capture_status is None else
                                      {"time": 1800, "status": "captured"})
