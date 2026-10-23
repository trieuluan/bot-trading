from types import SimpleNamespace

import pytest

from scripts.forex_candle_study import default_groups, rates, simulate, stats, valid_series


def bar(time, open=100, high=101, low=99.5, close=100):
    return SimpleNamespace(time=time, open=open, high=high, low=low, close=close)


def signal(side="long"):
    return {
        "side": side,
        "symbol": "EURUSD",
        "stop": 99 if side == "long" else 101,
        "target": 102 if side == "long" else 98,
        "htf_structure": {"atr": 1},
    }


def test_market_uses_next_open_not_trigger_close_and_charges_both_fees():
    bids = [bar(36000, close=100.8), bar(36900, high=102.5)]
    result = simulate(signal(), 0, bids, bids, "market", "2R", 0, 0.1)
    assert result["entry"] == 100
    assert result["entry_time"] == 36900
    assert result["net_r"] == pytest.approx(1.8)


@pytest.mark.parametrize("side", ["long", "short"])
def test_stop_wins_when_both_barriers_hit(side):
    bids = [bar(36000), bar(36900, high=103, low=97)]
    result = simulate(signal(side), 0, bids, bids, "market", "2R", 0, 0)
    assert result["reason"] == "both_hit_stop_first"
    assert result["net_r"] == -1


def test_long_entry_uses_ask_short_exit_uses_ask():
    bids = [bar(36000), bar(36900, high=103, low=99.5)]
    asks = [
        bar(36000, open=100.2, high=101.2, low=99.7, close=100.2),
        bar(36900, open=100.2, high=103.2, low=99.7, close=100.2),
    ]
    result = simulate(signal(), 0, bids, asks, "market", "2R", 0, 0)
    assert result["entry"] == 100.2
    short = simulate(signal("short"), 0, bids, asks, "market", "2R", 0, 0)
    assert short["exit"] == 101
    assert short["reason"] == "stop"


def test_missing_bar_never_forward_filled():
    bids = [bar(36000), bar(37800)]
    assert simulate(signal(), 0, bids, bids, "market", "2R", 0, 0)["status"] == "gap_before_fill"


def test_breakout_ambiguous_invalidation_not_assumed_filled():
    bids = [bar(36000), bar(36900, high=102, low=98)]
    result = simulate(signal(), 0, bids, bids, "break_2bars", "2R", 0, 0)
    assert result["status"] == "ambiguous_or_invalidated_pending"


def test_breakout_fill_bar_does_not_claim_prior_target_touch():
    bids = [bar(36000, high=100.2), bar(36900, high=104), bar(37800, high=101)]
    result = simulate(signal(), 0, bids, bids, "break_2bars", "2R", 0, 0)
    assert result["status"] == "open_end"


def test_conversion_never_uses_unclosed_bar():
    bids = [bar(36000, close=150), bar(36900, close=200)]
    asks = [bar(36000, close=151), bar(36900, close=201)]
    assert rates("JPY", {"USDJPY": (bids, asks, [36000, 36900])}, 36900) == (1 / 150, 1 / 151)


def test_duplicate_or_invalid_candles_fail_closed():
    with pytest.raises(ValueError):
        valid_series([bar(36000), bar(36000)], 900)
    with pytest.raises(ValueError):
        valid_series([bar(36000, low=102)], 900)


def test_open_samples_not_counted_as_wins_or_losses():
    result = stats([{"status": "open_end"}])
    assert result["n"] == 0
    assert result["mean_r"] is None


@pytest.mark.parametrize("symbol", ["XAUUSD", "EURUSD", "USDJPY"])
def test_research_groups_are_valid_live_trees(symbol):
    groups = default_groups(symbol)
    assert len(groups) == 12
    assert groups["sweep"]["triggers"]
    assert all(
        group["tree"]["root"]["children"][3]["policy"] == "fixed_structural"
        for group in groups.values()
    )


def test_adverse_slippage_on_both_sides():
    bids = [bar(36000), bar(36900, high=103)]
    result = simulate(signal(), 0, bids, bids, "market", "2R", 1, 0)
    assert result["entry"] == pytest.approx(100.01)
    assert result["exit"] == pytest.approx(result["target"] * 0.9999)
    assert result["net_r"] < 2
