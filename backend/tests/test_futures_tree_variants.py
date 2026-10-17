import copy

import pytest

from backend.app import futures_rule_tree
from backend.app.market import DemoAdapter
from scripts import futures_probe_tree_variants as probe


def test_variants_preserve_original_and_fixed_exits():
    population, labels = probe.candidates()
    assert len(population) == len(labels) == 8
    assert labels["rule_tree:dcea29b41697"] == "H4_w3_lb20"
    for candidate in population.values():
        genome = copy.deepcopy(candidate["genome"])
        genome["lookback"] = 20
        genome["rule"]["within"] = 3
        genome["rule"]["children"][0]["children"][1]["frame"] = "H4"
        assert genome == population["rule_tree:dcea29b41697"]["genome"]


def test_frame_cache_preserves_matches_and_restores_original():
    candles = DemoAdapter().candles(limit=1700, end=1700)
    original = futures_rule_tree.frame_rows
    population, _ = probe.candidates()
    windows = [candles[offset:1600 + offset] for offset in range(6)]
    expected = [futures_rule_tree.matches(candidate["genome"]["rule"], window,
                direction, candidate["genome"]["lookback"])
                for window in windows for candidate in population.values()
                for direction in (1, -1)]
    with probe.cached_frames():
        for window in windows:
            for frame in ("M15", "H1", "H4"):
                assert futures_rule_tree.frame_rows(window, frame) == original(window, frame)
        actual = [futures_rule_tree.matches(candidate["genome"]["rule"], window,
                  direction, candidate["genome"]["lookback"])
                  for window in windows for candidate in population.values()
                  for direction in (1, -1)]
    assert actual == expected
    assert futures_rule_tree.frame_rows is original


def test_pooled_stats_use_trade_weights_not_average_profit_factors():
    rows = [{"resolved": 10, "gross_gain_r": 8, "gross_loss_r": 2},
            {"resolved": 2, "gross_gain_r": 1, "gross_loss_r": 3}]
    result = probe.pooled(rows)
    assert result["resolved"] == 12
    assert result["mean_r"] == pytest.approx(4 / 12)
    assert result["profit_factor"] == pytest.approx(9 / 5)
    assert probe.pooled([]) == {"resolved": 0, "mean_r": None, "profit_factor": None}
