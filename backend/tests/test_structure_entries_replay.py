import pytest

from backend.app.market import Candle
from scripts.structure_entries_replay import (
    custom_entry_level, custom_wait, settle_at_boundary, simulate_policy,
    summarize_arm, support_pivot, wait_confirm,
)


def candle(stamp, opening=100, high=102, low=98, close=101):
    return Candle(stamp, opening, high, low, close, 1)


def pending(policy="body50_confirm", side="long"):
    return dict(status="open", entry=None, entry_policy=policy, side=side,
                level=100, stop=90 if side == "long" else 110,
                target=120 if side == "long" else 80,
                first_entry_time=900, entry_time=900)


@pytest.mark.parametrize("side", ["long", "short"])
def test_confirm_waits_until_next_open_and_has_stop_priority(side):
    sample = pending(side=side)
    bar = candle(900) if side == "long" else candle(900, 100, 102, 98, 99)
    wait_confirm(sample, bar)
    assert sample["confirmed"] and sample["entry_time"] == 1800
    sample = pending(side=side)
    bar = candle(900, low=89) if side == "long" else candle(900, 100, 111, 98, 99)
    wait_confirm(sample, bar)
    assert sample["exit_reason"] == "stop_before_confirmation"
    assert not sample.get("confirmed")


def test_exact_touch_is_not_confirmation_and_close_through_cancels():
    sample = pending()
    wait_confirm(sample, candle(900, 101, 103, 100, 102))
    assert not sample.get("confirmed")
    wait_confirm(sample, candle(1800, close=99))
    assert sample["exit_reason"] == "failed_retest"


def test_confirmation_expiry_does_not_reset_after_touch():
    sample = pending()
    custom_wait(sample, candle(4500), [], [])
    assert sample["exit_reason"] == "expired"


def test_counter_body_only_sweep_confirms_and_target_cancels():
    bar = candle(900, 103, 104, 99, 101)
    retest, sweep = pending(), pending("sweep_m15")
    wait_confirm(retest, bar)
    wait_confirm(sweep, bar, require_body_align=False)
    assert not retest.get("confirmed") and sweep["confirmed"]
    sample = pending()
    wait_confirm(sample, candle(900, high=121))
    assert sample["exit_reason"] == "target_before_confirmation"


def test_support_pivot_needs_two_right_bars_and_cannot_already_be_swept():
    lows = [98, 97, 95, 97, 98, 98]
    rows = [candle(index * 900, low=low) for index, low in enumerate(lows)]
    assert support_pivot(rows[:4], 1) is None
    assert support_pivot(rows[:5], 1) == 95
    assert support_pivot(rows + [candle(5400, low=94)], 1) is None
    mirrored = [Candle(row.time, 200 - row.open, 200 - row.low,
                       200 - row.high, 200 - row.close, 1) for row in rows]
    assert support_pivot(mirrored, -1) == 105


def test_h1_pivot_needs_closed_hour_and_no_lookahead():
    highs = [102] * 16 + [102, 103, 105, 103, 102, 107]
    rows = [candle(hour * 3600 + part * 900, 100, high, 99,
                   106 if hour == 21 else 101)
            for hour, high in enumerate(highs) for part in range(4)]
    assert custom_entry_level("pivot_retest_h1", rows[:-1], "long", 90) is None
    assert custom_entry_level("pivot_retest_h1", rows, "long", 90) == 105


def test_micro_bos_uses_frozen_confirmed_pivot_not_single_candle_high():
    highs = [102, 103, 106, 104, 103, 104]
    rows = [candle(index * 900, 101, high, 100.5, 101.5)
            for index, high in enumerate(highs)]
    touch = candle(5400, 101, 104, 99, 102)
    sample = pending("micro_bos_m15")
    sample.update(first_entry_time=5400, entry_time=5400)
    custom_wait(sample, touch, [], rows + [touch])
    assert sample["bos_trigger"] == 106 and not sample.get("confirmed")
    next_bar = candle(6300, 102, 105, 101, 104.5)
    custom_wait(sample, next_bar, [], rows + [touch, next_bar])
    assert not sample.get("confirmed") and sample["bos_trigger"] == 106
    break_bar = candle(7200, 104.5, 108, 104, 107)
    custom_wait(sample, break_bar, [], rows + [touch, next_bar, break_bar])
    assert sample["confirmed"] and sample["entry_time"] == 8100


def test_confirmed_entry_gets_next_open_not_level_and_keeps_costs():
    rows = [candle(0, 100, 111, 99, 110), candle(900, 106, 109, 104, 108),
            candle(1800, 109, 110, 108, 109), candle(2700, 109, 111, 108, 110)]
    base = dict(status="open", entry=None, side="long", stop=90, target=130,
                exit_policy="fixed", reference_risk=16)
    result = simulate_policy(base, "body50_confirm", rows, rows, [], [], 0, 3600,
                             lambda index: rows[:index + 1])
    assert result["entry_time"] == 1800
    assert result["entry"] > 109
    assert result["exit_reason"] == "boundary_mark" and result["filled"]
    assert result["ref_r"] == pytest.approx(result["net_r"] * result["risk"] / 16)
    assert result["net_r"] < (110 - 109) / 19


def test_next_open_invalid_geometry_scores_zero_not_hidden():
    rows = [candle(0, 100, 111, 99, 110), candle(900, 106, 109, 104, 108),
            candle(1800, 135, 137, 134, 136)]
    base = dict(status="open", entry=None, side="long", stop=90, target=130,
                exit_policy="fixed", reference_risk=16)
    result = simulate_policy(base, "body50_confirm", rows, rows, [], [], 0, 2700,
                             lambda index: rows[:index + 1])
    assert result["status"] == "resolved" and not result["filled"]
    assert result["net_r"] == result["ref_r"] == 0
    assert result["exit_reason"] == "invalid_entry_geometry"


def test_boundary_does_not_use_future_candle():
    rows = [candle(0), candle(900, close=101), candle(1800, 150, 152, 148, 151)]
    sample = dict(entry=100, risk=10, entry_time=0, side="long", funding_cost=0)
    settle_at_boundary(sample, rows, 1800)
    assert sample["label_available_at"] == 1800
    assert sample["net_r"] < 0.1
    pending_sample = pending()
    settle_at_boundary(pending_sample, rows, 1800)
    assert not pending_sample["filled"] and pending_sample["net_r"] == 0


def test_summary_includes_zero_fills_and_normalizes_r():
    base = [dict(key=index, time=900, net_r=-1, ref_r=-1) for index in range(2)]
    rows = [dict(key=0, time=900, net_r=1, ref_r=0.5, filled=True, exit_reason="target"),
            dict(key=1, time=900, net_r=0, ref_r=0, filled=False, exit_reason="expired")]
    stats = summarize_arm(rows, base)
    assert stats["n_signals"] == 2 and stats["n_filled"] == 1
    assert stats["mean_r_per_signal"] == 0.5 and stats["mean_ref_r_per_signal"] == 0.25
    assert stats["mean_r_when_filled"] == 1
    assert stats["delta_ref_r_vs_reference_ci95"] == [1.25, 1.25]
