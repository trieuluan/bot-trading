from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from scripts.forex_mtf_investigation import (
    MODES,
    STEP,
    atr,
    closed_index,
    fill,
    liquid,
    paired_comparisons,
    partition,
    rollover,
    signals,
    simulate,
    stats,
)


def bar(stamp, opening=100, high=101, low=99.5, close=100):
    return SimpleNamespace(time=stamp, open=opening, high=high, low=low, close=close)


def signal(direction=1, available=36000):
    return dict(symbol="EURUSD", direction=direction, available=available,
                stop=99 if direction == 1 else 101, atr=1, close=100,
                open=99.8 if direction == 1 else 100.2, high=100.5, low=99.5)


def execution(direction=1, intrabar=False):
    return dict(index=0, stop=99 if direction == 1 else 101, ideal=100,
                risk_atr=1, intrabar=intrabar)


SPEC = {"quote_currency": "USD", "commission_account_per_unit": 0.1}


@pytest.mark.parametrize("direction", [1, -1])
def test_collision_stop_first_and_both_side_commission(direction):
    rows = [bar(36000, high=103, low=97)]
    result = simulate(signal(direction), execution(direction), rows, rows, SPEC, {}, 2, 0)
    assert result["net_r"] == pytest.approx(-1.2)
    assert result["ambiguous"]


def test_no_entry_at_known_trigger_close():
    rows = [bar(35100, close=100.4), bar(36000, opening=100.2)]
    result = fill(signal(), "h1_direct", rows, rows, [row.time for row in rows], atr(rows))
    assert result["index"] == 1
    assert result["ideal"] == 100.2


def test_gap_censors_open_trade_not_synthetic_exit():
    rows = [bar(36000), bar(37800)]
    result = simulate(signal(), execution(), rows, rows, SPEC, {}, 2, 0)
    assert result["status"] == "gap_open"
    assert "net_r" not in result


def test_tail_is_open_not_marked_as_time_exit():
    rows = [bar(36000)]
    assert simulate(signal(), execution(), rows, rows, SPEC, {}, 2, 0)["status"] == "open_end"


def test_stop_gap_executes_worse_than_stop():
    rows = [bar(36000), bar(36900, opening=98, high=98.5, low=97, close=98)]
    result = simulate(signal(), execution(), rows, rows, SPEC, {}, 2, 0)
    assert result["reason"] == "stop_gap"
    assert result["net_r"] == pytest.approx(-2.2)


def test_intrabar_pending_never_earns_fill_bar_target():
    rows = [bar(36000, high=103)]
    result = simulate(signal(), execution(intrabar=True), rows, rows, SPEC, {}, 2, 0)
    assert result["status"] == "open_end"


def test_pending_stop_cannot_revive_invalidated_setup():
    rows = [bar(36000, high=100.1, low=98.8), bar(36900, high=103)]
    result = fill(signal(), "stop_h1_break", rows, rows, [row.time for row in rows], atr(rows))
    assert result["status"] == "invalidated"


def test_bid_ask_is_used_and_spread_not_deducted_twice():
    bids = [bar(36000, high=103)]
    asks = [bar(36000, opening=100.2, high=103.2, low=99.7, close=100.2)]
    result = fill(signal(), "h1_direct", bids, asks, [36000], [1])
    trade = simulate(signal(), result, bids, asks, SPEC, {}, 2, 0)
    assert trade["entry"] == 100.2
    assert trade["net_r"] == pytest.approx((102 - 100.2 - 0.2) / 1.2)
    assert trade["stress_r"] < trade["net_r"]


@pytest.mark.parametrize("mode", MODES)
def test_missing_entry_bar_fails_closed(mode):
    rows = [bar(36900)]
    assert fill(signal(), mode, rows, rows, [36900], [1])["status"] == "gap_before_fill"


def test_only_closed_h4_can_influence_setup():
    assert closed_index([0, 14400, 28800], 18000, 14400) == 0
    assert closed_index([0, 14400, 28800], 28800, 14400) == 1


def test_future_candles_do_not_change_past_signals():
    hours = [bar(index * 3600, opening=100 + index * 0.02, high=101 + index * 0.02,
                 low=99 + index * 0.02, close=100.5 + index * 0.02) for index in range(400)]
    higher = [bar(index * 14400, opening=100 + index * 0.08, high=102 + index * 0.08,
                  low=99 + index * 0.08, close=101 + index * 0.08) for index in range(100)]
    frames = {("1h", "bid"): hours, ("4h", "bid"): higher}
    cutoff = 360 * 3600
    prefix = {key: [row for row in value if row.time + (3600 if key[0] == "1h" else 14400) <= cutoff]
              for key, value in frames.items()}
    assert list(signals("EURUSD", prefix)) == [item for item in signals("EURUSD", frames) if item["available"] <= cutoff]


@pytest.mark.parametrize("date", ["2026-08-03", "2026-11-09"])
def test_rollover_and_london_gate_obey_dst(date):
    stamp = int(datetime.fromisoformat(date + "T16:30:00").replace(tzinfo=ZoneInfo("America/New_York")).timestamp())
    assert rollover(stamp)
    assert not rollover(stamp - STEP)
    assert not rollover(stamp + 5400)
    london_open = int(datetime.fromisoformat(date + "T08:00:00").replace(tzinfo=ZoneInfo("Europe/London")).timestamp())
    assert liquid(london_open)


def test_rollover_exit_avoids_unpriced_swap():
    stamp = int(datetime(2026, 11, 9, 16, 15, tzinfo=ZoneInfo("America/New_York")).timestamp())
    rows = [bar(stamp)]
    result = simulate(signal(available=stamp), execution(), rows, rows, SPEC, {}, 2, 0)
    assert result["reason"] == "pre_rollover"
    assert result["exit_time"] == stamp + STEP


def test_embargo_and_unresolved_stats():
    assert partition(9999, 10000, 200000) == "discovery"
    assert partition(10000, 10000, 200000) == "embargo"
    assert partition(10000 + 100 * STEP, 10000, 200000) == "validation"
    assert stats([{"status": "gap_open"}])["n"] == 0


def test_unfilled_alternative_is_zero_in_paired_opportunity_comparison():
    common = dict(symbol="EURUSD", setup_id="one", available=36000, fold="discovery")
    records = [{**common, "config": "squeeze|h1_direct|2.0R|all", "status": "resolved", "net_r": -1},
               {**common, "config": "squeeze|m15_aligned|2.0R|all", "status": "unfilled"}]
    report = paired_comparisons(records)["squeeze|m15_aligned|2.0R|all"]["all"]
    assert report["mean_delta_r"] == 1
    assert report["mean_alternative_per_opportunity_r"] == 0
