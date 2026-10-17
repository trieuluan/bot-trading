import pytest

from scripts.exness_new_family_backtest import stats


def test_backtest_statistics_ignore_unresolved_and_measure_net_returns():
    rows = [{"status": "resolved", "net_r": value} for value in (-1.07, 0.43, 1.93)]
    result = stats(rows + [{"status": "open"}, {"status": "excluded"}])
    assert result["n"] == 3
    assert result["mean_r"] == pytest.approx(0.43)
    assert result["win_rate"] == pytest.approx(2 / 3)
    assert result["lower_r"] < result["mean_r"]
    assert stats([])["mean_r"] is None
    assert stats(rows[:1])["lower_r"] is None
