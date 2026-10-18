from scripts.futures_eth_wedge_eval import meets_cost_filter

def test_meets_cost_filter():
    # Fee is 5 bps, slip 2 bps -> round-trip is 2 * (0.0005 + 0.0002) = 0.0014 (14 bps)
    # entry 100, stop 99 -> risk = 1.0 -> cost = 100 * 0.0014 / 1.0 = 0.14R <= 0.15R -> True
    assert meets_cost_filter(100, 99, 0.15)
    # entry 100, stop 99.5 -> risk = 0.5 -> cost = 100 * 0.0014 / 0.5 = 0.28R > 0.15R -> False
    assert not meets_cost_filter(100, 99.5, 0.15)
    # zero risk edge case
    assert not meets_cost_filter(100, 100, 0.15)
