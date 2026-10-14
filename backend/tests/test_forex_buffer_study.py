from decimal import Decimal
from types import SimpleNamespace

import pytest

from backend.app.exness import ExnessError
from backend.app.forex_buffer_study import (DEFAULT_VARIANT, VARIANTS, advance_variant,
                                            build_buffer_model, exit_target, new_variants,
                                            select_buffer)

COND = {"instrument": "XAUUSD", "trade_mode": "enabled", "exec_mode": "market",
        "quote_currency": "USD", "volume_min": "0.01", "volume_max": "10.0",
        "volume_step": "0.01", "contract_size": "100", "point_digits": 3}
FEATURES = {"trend": "up", "utc_session": 1}
SIGNAL = {"strategy": "trend_pullback", "side": "long", "stop": 2590.0}

def bar(time, open_, high, low, close):
    return SimpleNamespace(time=time, open=open_, high=high, low=low, close=close)

def test_fixed_tp_modes_and_unknown_variant():
    long = {"side": "long", "exit_variant": "k0.25_1.5r"}
    assert exit_target(long, Decimal("2600"), Decimal("2590"), Decimal("0.001"))[0] == Decimal("2615.000")
    short = {"side": "short", "exit_variant": "k0.25_3r"}
    assert exit_target(short, Decimal("2600"), Decimal("2610"), Decimal("0.001"))[0] == Decimal("2570.000")
    with pytest.raises(ExnessError):
        exit_target({"side": "long", "exit_variant": "k9_2r"}, Decimal("2600"), Decimal("2590"), Decimal("0.001"))

def make_rows():
    base = 100 * 14400
    hours = [bar(base - 3600 * (30 - i), 2600, 2620, 2580, 2600) for i in range(30)]
    higher = [bar(base - 14400 * (30 - i), 2600, 2650, 2550, 2600) for i in range(30)]
    minutes = [bar(base - 900 * (25 - i), 2600, 2602, 2598, 2600) for i in range(25)]
    return minutes, hours, higher

def test_variants_enter_next_open_and_manage_partial(monkeypatch):
    monkeypatch.setenv("EXNESS_DEMO_MAX_LOT", "0.10")
    monkeypatch.setenv("EXNESS_DEMO_RISK_PER_TRADE_USD", "25")
    monkeypatch.setattr("backend.app.forex_buffer_study.session_closed_ahead", lambda *_: False)
    minutes, hours, higher = make_rows()
    rows = new_variants(SIGNAL, minutes, hours, higher, FEATURES, minutes[-1], COND)
    assert len(rows) == len(VARIANTS)
    row = next(item for item in rows if item["variant"] == "k0.5_2r")
    assert row["status"] == "pending"
    entry_bar = bar(minutes[-1].time + 900, 2600, 2601, 2599, 2600)
    advance_variant(row, entry_bar, entry_bar, minutes + [entry_bar], 0, 0)
    assert row["status"] == "open" and row["entry"] == 2600
    run = bar(entry_bar.time + 900, 2600, 2613, 2598, 2612)
    advance_variant(row, run, run, minutes + [entry_bar, run], 0, 0)
    assert any(not leg["open"] and leg["role"] == "tp1" for leg in row["legs"])
    runner = next(leg for leg in row["legs"] if leg["role"] == "runner")
    assert runner["open"] and runner["stop_loss_price"] == "2600.000"

def resolved(variant, stamp, value):
    return {"status": "resolved", "variant": variant, "context": "trend_pullback:long:up:1",
            "confirm_time": stamp, "resolved_at": stamp + 900, "net_r": value}

def paired_records(edge):
    cutoff = 100 * 900
    records = []
    for index in range(70):
        stamp = index * 900 if index < 35 else cutoff + (index - 34) * 900
        records += [resolved(DEFAULT_VARIANT, stamp, 0.1 + (index % 3) * 0.01),
                    resolved("k0.5_2r", stamp, 0.1 + edge + (index % 3) * 0.01)]
    return records, cutoff

def test_promotion_needs_paired_edge_validation_and_verified_costs():
    records, cutoff = paired_records(1.0)
    key = "trend_pullback:long:up:1"
    unverified = build_buffer_model(records, cutoff, 999999, costs_verified=False)
    assert unverified[key]["recommended"] == "k0.5_2r" and unverified[key]["selected"] == DEFAULT_VARIANT
    assert select_buffer(SIGNAL, FEATURES, unverified)["exit_variant"] == DEFAULT_VARIANT
    verified = build_buffer_model(records, cutoff, 999999, costs_verified=True)
    assert select_buffer(SIGNAL, FEATURES, verified)["exit_variant"] == "k0.5_2r"

def test_no_promotion_without_edge_over_default():
    records, cutoff = paired_records(0.0)
    model = build_buffer_model(records, cutoff, 999999, costs_verified=True)
    assert select_buffer(SIGNAL, FEATURES, model)["exit_variant"] == DEFAULT_VARIANT

def test_labels_not_available_yet_are_ignored():
    records, cutoff = paired_records(1.0)
    assert build_buffer_model(records, cutoff, 1, costs_verified=True) == {}


def test_validation_failure_keeps_default_and_does_not_pick_runner_up():
    records, cutoff = paired_records(1.0)
    for row in records:
        if row["variant"] == "k0.5_2r" and row["confirm_time"] >= cutoff:
            row["net_r"] = -1
    model = build_buffer_model(records, cutoff, 999999, costs_verified=True)
    assert select_buffer(SIGNAL, FEATURES, model)["exit_variant"] == DEFAULT_VARIANT
    assert model["trend_pullback:long:up:1"]["recommended"] == "k0.5_2r"


def test_train_boundary_uses_resolution_time_not_confirmation_time():
    records, cutoff = paired_records(1.0)
    for row in records:
        if row["confirm_time"] < cutoff:
            row["resolved_at"] = cutoff + 1
    model = build_buffer_model(records, cutoff, 999999, costs_verified=True)
    assert select_buffer(SIGNAL, FEATURES, model)["exit_variant"] == DEFAULT_VARIANT
    assert all(score["train_count"] == 0 for score in model["trend_pullback:long:up:1"]["scores"])


def test_contexts_are_not_pooled_across_strategies_or_directions():
    records, cutoff = paired_records(1.0)
    model = build_buffer_model(records, cutoff, 999999, costs_verified=True)
    assert select_buffer({**SIGNAL, "side": "short"}, FEATURES, model)["exit_variant"] == DEFAULT_VARIANT
    assert select_buffer({**SIGNAL, "strategy": "range_rejection"}, FEATURES, model)["exit_variant"] == DEFAULT_VARIANT


def test_short_stop_first_and_costs(monkeypatch):
    monkeypatch.setenv("EXNESS_DEMO_MAX_LOT", "0.10")
    monkeypatch.setenv("EXNESS_DEMO_RISK_PER_TRADE_USD", "25")
    monkeypatch.setattr("backend.app.forex_buffer_study.session_closed_ahead", lambda *_: False)
    minutes, hours, higher = make_rows()
    signal = {**SIGNAL, "side": "short", "stop": 2610}
    rows = new_variants(signal, minutes, hours, higher, FEATURES, minutes[-1], COND)
    row = next(item for item in rows if item["variant"] == "k0.25_2r")
    bid = bar(minutes[-1].time + 900, 2600, 2630, 2570, 2600)
    ask = bar(bid.time, 2600.4, 2630.4, 2570.4, 2600.4)
    advance_variant(row, bid, ask, minutes + [bid], 0, 0.1)
    assert row["status"] == "resolved" and row["net_r"] < -1
    assert all(leg["outcome"] == "stop" for leg in row["legs"])


def test_live_body_uses_selected_variant_and_checks_new_risk(monkeypatch):
    import time
    from unittest.mock import MagicMock
    from backend.app.exness_demo_client import ExnessDemoClient
    monkeypatch.setenv("EXNESS_DEMO_MAX_LOT", "0.10")
    monkeypatch.setenv("EXNESS_DEMO_RISK_PER_TRADE_USD", "25")
    quote = {"bid": 2600.0, "ask": 2600.4, "time": time.time(), "received_at": time.time()}
    signal = {**SIGNAL, "exit_variant": "k0.5_3r", "stop_buffer": 2,
              "target_context": {"barriers": [], "buffer": 0}}
    body = ExnessDemoClient(MagicMock()).strategy_body(signal, COND, quote, "test")
    entry, stop, target = Decimal("2600.4"), Decimal(body["stop_loss_price"]), Decimal(body["take_profit_price"])
    assert stop == Decimal("2588.000")
    assert target == entry + 3 * (entry - stop)
    assert signal["tp_reason"] == "fixed_3r"
    assert Decimal(body["volume"]) * Decimal("100") * (entry - stop) <= 25
