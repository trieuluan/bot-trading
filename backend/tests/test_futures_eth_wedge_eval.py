from scripts.futures_eth_wedge_eval import meets_cost_filter


def test_meets_cost_filter():
    # FEE=8 bps, SLIP=2 bps -> round-trip is 2 * (0.0008 + 0.0002) = 0.0020 (20 bps)
    # entry 100, stop 98 -> risk = 2.0 -> cost = 100 * 0.0020 / 2.0 = 0.10R <= 0.15R -> True
    assert meets_cost_filter(100, 98, 0.15)
    # entry 100, stop 99 -> risk = 1.0 -> cost = 100 * 0.0020 / 1.0 = 0.20R > 0.15R -> False
    assert not meets_cost_filter(100, 99, 0.15)
    # zero risk edge case
    assert not meets_cost_filter(100, 100, 0.15)
