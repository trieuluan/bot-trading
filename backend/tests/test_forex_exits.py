from decimal import Decimal
from types import SimpleNamespace
import pytest
from backend.app.forex_exits import (
    DEFAULTS, BOUNDS, parameters, closed_rows, barrier_levels,
    barrier_runner, plan_legs, action,
)
from backend.app.exness import ExnessError

COND = {"instrument": "XAUUSD", "trade_mode": "enabled", "exec_mode": "market",
        "quote_currency": "USD", "volume_min": "0.01", "volume_max": "10.0",
        "volume_step": "0.01", "contract_size": "100", "point_digits": 3}

def bar(t, o, h, l, c):
    return SimpleNamespace(time=t, open=o, high=h, low=l, close=c, price_type="bid")

def test_parameters_validates_defaults_and_bounds():
    assert parameters("evidence_h1")["fraction"] == 0.3
    assert parameters("runner_h1")["atr_multiple"] == 2.0
    with pytest.raises(ExnessError):
        parameters("unknown_policy")
    with pytest.raises(ExnessError):
        parameters("ladder_h1", {"fraction": 0.5, "step_r": 1.0, "trail_r": 1.5})
    valid = parameters("ladder_h1", {"fraction": 0.3, "step_r": 1.5, "trail_r": 2.0})
    assert valid["step_r"] == 1.5

def test_plan_legs_runner_only_and_splits():
    body = {"side": "buy", "volume": "0.10", "stop_loss_price": "99.000", "take_profit_price": "105.000"}
    sig_runner = {"exit_policy": "runner_h1", "side": "long"}
    assert len(plan_legs(sig_runner, body, COND, Decimal("100"))) == 1

    sig_evidence = {"exit_policy": "evidence_h1", "side": "long"}
    legs_ev = plan_legs(sig_evidence, body, COND, Decimal("100"))
    assert len(legs_ev) == 2
    assert legs_ev[0]["role"] == "partial1" and legs_ev[0]["volume"] == "0.03"
    assert legs_ev[1]["role"] == "runner" and legs_ev[1]["volume"] == "0.07"

    sig_ladder = {"exit_policy": "ladder_h1", "side": "long"}
    legs_lad = plan_legs(sig_ladder, body, COND, Decimal("100"))
    assert len(legs_lad) == 3
    assert [l["volume"] for l in legs_lad] == ["0.03", "0.03", "0.04"]

def test_plan_legs_falls_back_when_insufficient_volume():
    body = {"side": "buy", "volume": "0.01", "stop_loss_price": "99.000", "take_profit_price": "105.000"}
    sig = {"exit_policy": "evidence_h1", "side": "long"}
    legs = plan_legs(sig, body, COND, Decimal("100"))
    assert len(legs) == 1 and legs[0]["role"] == "runner"
    assert sig["partial_skip_reason"] == "insufficient_split_lot"

def test_action_evidence_exits_on_weakness_not_strength():
    sig = {"exit_policy": "evidence_h1", "management_frame": "H1", "side": "long",
           "target_context": {"buffer": 0.1, "barriers": []}}
    rows = [bar(i * 3600, 100, 102, 99.8, 101.5) for i in range(25)]
    # Strong bull continuation bar: do not exit
    rows.append(bar(25 * 3600, 101.5, 103.5, 101.4, 103.4))
    act = action(sig, rows, 100.0, 1.0, 0, 99.0, COND, 103.4)
    assert act is not None and not act["close_roles"]
    # Reversal bar after profit >= 1R: exit partial1
    rows.append(bar(26 * 3600, 103.4, 103.5, 101.8, 102.0))
    act2 = action(sig, rows, 100.0, 1.0, 0, 99.0, COND, 102.0)
    assert act2 is not None and act2["close_roles"] == ["partial1"]
