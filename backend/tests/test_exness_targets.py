from decimal import Decimal
from types import SimpleNamespace
import pytest
from backend.app.exness import ExnessError
from backend.app.exness_targets import structural_target, target_context

def candle(t, o, h, l, c):
    return SimpleNamespace(time=t, open=o, high=h, low=l, close=c)

def test_structural_target_caps_at_2r():
    context = {"barriers": [{"side": "long", "price": 2700.0, "reason": "H1 Donchian20"}], "buffer": 0.5}
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
    h1 = [candle(100000 - 3600 * (25 - i), 2600, 2600 + i, 2590, 2600) for i in range(25)]
    h4 = [candle(100000 - 14400 * (25 - i), 2600, 2610 + i, 2580, 2600) for i in range(25)]
    ctx = target_context(m15, h1, h4)
    assert ctx["buffer"] == pytest.approx(1.0)
    assert any("H1 Donchian20" in b["reason"] for b in ctx["barriers"])
    assert any("H4 Donchian20" in b["reason"] for b in ctx["barriers"])
