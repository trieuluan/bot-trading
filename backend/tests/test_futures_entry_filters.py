from scripts.futures_entry_filters import allowed

def test_cost_and_trend_filters():
    # FEE=8 bps, SLIP=2 bps -> 2 * (0.0008 + 0.0002) = 0.0020 (20 bps round-trip)
    # entry 100, stop 99 -> risk 1.0 -> cost = 100 * 0.0020 / 1.0 = 0.20R <= 0.25R -> True
    # entry 100, stop 99.3 -> risk 0.7 -> cost = 100 * 0.0020 / 0.7 = 0.286R > 0.25R -> False
    assert allowed(None, False, "long", "range", 100, 99)
    assert not allowed(0.25, False, "long", "range", 100, 99.3)
    assert allowed(0.25, False, "long", "range", 100, 99)
    assert allowed(None, True, "long", "trend_up", 100, 99)
    assert allowed(None, True, "short", "trend_down", 100, 101)
    for side, context in (("long", "range"), ("long", "trend_down"), ("short", "trend_up"), ("short", "range")):
        assert not allowed(None, True, side, context, 100, 99)
