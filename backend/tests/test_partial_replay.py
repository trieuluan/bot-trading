from types import SimpleNamespace

import pytest

from scripts.exness_partial_replay import advance_partial, summarize, trend_at

COSTS = {"slippage_bps": 0, "commission_per_unit": 0}
CONFIG = {"partial_r": 1.0, "weight": 0.5, "target_r": 3.0}


def bar(stamp=60, opening=100, high=112, low=95, close=105):
    return SimpleNamespace(time=stamp, open=opening, high=high, low=low, close=close)


def sample(side="long"):
    return {"side": side, "stop": 90 if side == "long" else 110,
            "status": "pending", "last_candle": 0, "confirm_time": 0}


def test_partial_and_be_on_same_bar():
    trade = sample()
    advance_partial(trade, bar(), bar(), CONFIG, COSTS)
    assert trade["status"] == "resolved"
    assert trade["net_r_ex_swap"] == pytest.approx(0.5)
    assert trade["ambiguous_bar"]


def test_initial_stop_first_when_both_targets_touched():
    trade = sample()
    advance_partial(trade, bar(low=85), bar(low=85), CONFIG, COSTS)
    assert trade["net_r_ex_swap"] == -1
    assert not trade["partial_hit"]


def test_timeout_without_partial():
    trade = sample()
    advance_partial(trade, bar(high=105), bar(high=105), CONFIG, COSTS, horizon=1)
    assert trade["outcome"] == "timeout"
    assert trade["net_r_ex_swap"] == pytest.approx(0.5)


def test_stop_gap_worse_than_minus_one_r():
    trade = sample()
    advance_partial(trade, bar(high=105), bar(high=105), CONFIG, COSTS)
    advance_partial(trade, bar(120, opening=80, high=85, low=75, close=82),
                    bar(120, opening=80), CONFIG, COSTS)
    assert trade["net_r_ex_swap"] == -2


def test_short_spread_and_weighted_commission():
    trade = sample("short")
    advance_partial(trade, bar(high=101, low=87), bar(high=102, low=88),
                    CONFIG, {**COSTS, "commission_per_unit": 2})
    assert trade["net_r_ex_swap"] == pytest.approx(0.3)


def test_open_above_partial_does_not_assume_original_stop_first():
    trade = sample()
    advance_partial(trade, bar(high=105), bar(high=105), CONFIG, COSTS)
    advance_partial(trade, bar(120, opening=115, high=116, low=85),
                    bar(120, opening=115), CONFIG, COSTS)
    assert trade["net_r_ex_swap"] == pytest.approx(0.5)


def test_runner_target_after_partial():
    trade = sample()
    advance_partial(trade, bar(high=105), bar(high=105), CONFIG, COSTS)
    advance_partial(trade, bar(120, opening=105, high=115, low=101),
                    bar(120, opening=105), CONFIG, COSTS)
    assert trade["status"] == "open"
    advance_partial(trade, bar(180, opening=115, high=135, low=105),
                    bar(180, opening=115), CONFIG, COSTS)
    assert trade["net_r_ex_swap"] == 2


def test_entry_stop_is_not_net_breakeven_with_slippage():
    trade = sample()
    costs = {**COSTS, "slippage_bps": 1}
    advance_partial(trade, bar(), bar(), CONFIG, costs)
    expected = (0.5 * (trade["entry"] + trade["risk"]) * 0.9999
                + 0.5 * trade["entry"] * 0.9999 - trade["entry"]) / trade["risk"]
    assert trade["net_r_ex_swap"] == pytest.approx(expected)
    assert trade["net_r_ex_swap"] < 0.5


def test_runner_gap_fills_below_entry():
    trade = sample()
    advance_partial(trade, bar(high=105), bar(high=105), CONFIG, COSTS)
    advance_partial(trade, bar(120, opening=105, high=115, low=101),
                    bar(120, opening=105), CONFIG, COSTS)
    advance_partial(trade, bar(180, opening=95, high=99, low=94),
                    bar(180, opening=95), CONFIG, COSTS)
    assert trade["outcome"] == "be_gap"
    assert trade["net_r_ex_swap"] == pytest.approx(0.25)


def test_free_runner_ignores_large_profit_until_timeout():
    trade = sample()
    free = {**CONFIG, "target_r": None}
    advance_partial(trade, bar(high=105), bar(high=105), free, COSTS)
    advance_partial(trade, bar(120, opening=105, high=150, low=101, close=140),
                    bar(120, opening=105), free, COSTS, horizon=2)
    assert trade["outcome"] == "timeout"
    assert trade["net_r_ex_swap"] == pytest.approx(2.5)


def test_boundary_purges_labels_not_available_on_train():
    trade = {**sample(), "status": "resolved", "net_r_ex_swap": 2, "last_candle": 120,
             "label_available_at": 180}
    result = summarize([trade], 150)
    assert result["train"]["resolved"] == 0
    assert result["validation"]["resolved"] == 0
    assert result["boundary_excluded"] == 1


def test_higher_timeframe_uses_closed_and_fresh_candles_only():
    rows = [bar(stamp=index * 900, close=100 + index) for index in range(51)]
    closes_at = [row.time + 900 for row in rows]
    available = 50 * 900
    before = trend_at(rows, closes_at, available, 900)
    rows[-1].close = -10000
    assert trend_at(rows, closes_at, available, 900) == before
    assert trend_at(rows, closes_at, 53 * 900, 900) is None
