import pytest

from backend.app.market import Candle
from scripts.sltp_geometry_replay import calculate_arm_geometry, recent_h1_swing


def candles():
    return [Candle(index * 900, 100, 101, 99, 100, 1) for index in range(100)]


@pytest.mark.parametrize("side,direction", [("long", 1), ("short", -1)])
def test_fixed_target_really_disables_adaptive_exits(side, direction):
    sample = dict(side=side, stop=100 - direction * 2, target=100 + direction * 4,
                  exit_policy="adaptive", exit_params=None)
    stop, target, policy, params = calculate_arm_geometry("fixed_tp_3.0r", sample, candles())
    assert (stop, target, policy, params) == (100 - direction * 2, 100 + direction * 6, "fixed", None)
    assert sample["exit_policy"] == "adaptive"


def test_swing_ignores_incomplete_hour_and_uses_closed_history():
    rows = candles()
    rows.append(Candle(100 * 900, 100, 150, 50, 100, 1))
    assert recent_h1_swing(rows, "long") == 99
    assert recent_h1_swing(rows, "short") == 101
    sample = dict(side="long", stop=98, target=104, exit_policy="adaptive")
    stop, target, policy, _ = calculate_arm_geometry("swing_h1_atr025_tp2r", sample, rows[:-1])
    assert (stop, target, policy) == (98.5, 103, "adaptive")


def test_missing_structural_target_does_not_fall_back_to_fixed_target(monkeypatch):
    monkeypatch.setattr("scripts.sltp_geometry_replay.exits.structural_target", lambda *args: None)
    sample = dict(side="long", stop=98, target=104, exit_policy="adaptive")
    for arm in ("tp_structural", "swing_h1_tp_structural"):
        assert calculate_arm_geometry(arm, sample, candles()) is None


def test_invalid_stop_is_rejected():
    sample = dict(side="long", stop=101, target=104, exit_policy="adaptive")
    assert calculate_arm_geometry("fixed_tp_2.0r", sample, candles()) is None
