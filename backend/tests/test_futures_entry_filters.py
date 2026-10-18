from scripts.futures_entry_filters import allowed

def test_cost_and_trend_filters():
    # cost = 100 * 2 * (0.0005 + 0.0002) / risk -> risk 0.4 gives 0.35R, risk 1.0 gives 0.14R
    assert allowed(None, False, "long", "range", 100, 99)
    assert not allowed(0.25, False, "long", "range", 100, 99.6)
    assert allowed(0.25, False, "long", "range", 100, 99)
    assert allowed(None, True, "long", "trend_up", 100, 99)
    assert allowed(None, True, "short", "trend_down", 100, 101)
    for side, context in (("long", "range"), ("long", "trend_down"), ("short", "trend_up"), ("short", "range")):
        assert not allowed(None, True, side, context, 100, 99)
