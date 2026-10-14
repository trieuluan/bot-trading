from decimal import Decimal
from types import SimpleNamespace
import pytest
from backend.app.exness import ExnessError
from backend.app.exness_targets import structural_target, target_context

def candle(stamp, opening, high, low, close):
    return SimpleNamespace(time=stamp, open=opening, high=high, low=low, close=close)

def test_far_barrier_allows_target_beyond_2r():
    context = {"barriers": [{"side": "long", "price": 2640.0, "reason": "H4 Donchian20"}], "buffer": 0.5}
    target, reason = structural_target("long", Decimal("2600"), Decimal("2590"), context, Decimal("0.001"))
    assert target == Decimal("2639.500")
    assert reason == "H4 Donchian20 @ 2640.0"

def test_no_barrier_falls_back_to_2r():
    context = {"barriers": [{"side": "short", "price": 2500.0, "reason": "H1 swing low"}], "buffer": 0.5}
    target, reason = structural_target("long", Decimal("2600"), Decimal("2590"), context, Decimal("0.001"))
    assert target == Decimal("2620.000")
    assert reason == "fixed_2r"

def test_structural_target_pulls_back_to_barrier_minus_buffer():
    context = {"barriers": [{"side": "long", "price": 2615.0, "reason": "H1 swing high"}], "buffer": 0.5}
    target, reason = structural_target("long", Decimal("2600"), Decimal("2590"), context, Decimal("0.001"))
    assert target == Decimal("2614.500")
    assert reason == "H1 swing high @ 2615.0"

def test_structural_target_skips_when_barrier_below_1r():
    context = {"barriers": [{"side": "long", "price": 2608.0, "reason": "H1 swing high"}], "buffer": 0.5}
    with pytest.raises(ExnessError, match="skipped_poor_rr"):
        structural_target("long", Decimal("2600"), Decimal("2590"), context, Decimal("0.001"))

def test_target_context_extracts_h1_and_h4():
    m15 = candle(100000, 2600, 2605, 2595, 2602)
    h1 = [candle(100000 - 3600 * (25 - index), 2600, 2600 + index, 2590, 2600) for index in range(25)]
    h4 = [candle(100000 - 14400 * (25 - index), 2600, 2610 + index, 2580, 2600) for index in range(25)]
    ctx = target_context(m15, h1, h4)
    assert ctx["buffer"] == pytest.approx(2.75)
    assert any("H1 Donchian20" in barrier["reason"] for barrier in ctx["barriers"])
    assert any("H4 Donchian20" in barrier["reason"] for barrier in ctx["barriers"])

def test_short_uses_nearest_support_and_ignores_opposite_side():
    context = {"barriers": [
        {"side": "short", "price": 2582, "reason": "H4 swing low"},
        {"side": "long", "price": 2599, "reason": "H1 swing high"},
        {"side": "short", "price": 2585, "reason": "H1 swing low"},
    ], "buffer": 0.5}
    target, reason = structural_target("short", Decimal("2600"), Decimal("2610"), context, Decimal("0.001"))
    assert target == Decimal("2585.500")
    assert reason == "H1 swing low @ 2585"

def test_context_ignores_unclosed_candles():
    signal = candle(100000, 2600, 2605, 2595, 2602)
    hours = [candle(100000 - 3600 * (25 - index), 2600, 2610, 2590, 2600) for index in range(25)]
    higher = [candle(100000 - 14400 * (25 - index), 2600, 2620, 2580, 2600) for index in range(25)]
    expected = target_context(signal, hours, higher)
    future = candle(100000, 2600, 9999, 1, 2600)
    assert target_context(signal, hours + [future], higher + [future]) == expected

def test_context_requires_closed_history():
    with pytest.raises(ExnessError, match="20 closed H1"):
        target_context(candle(100000, 2600, 2605, 2595, 2602), [], [])
