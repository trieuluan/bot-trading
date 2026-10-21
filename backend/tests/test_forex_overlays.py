from types import SimpleNamespace

import pytest

from backend.app.forex_htf_v2 import g0_trees
from backend.app.forex_indicators import ema
from backend.app.forex_overlays import _pivots, _vwap, chart_overlays, tree_overlays


def bar(time, close=100.0, high=None, low=None, volume=100):
    return SimpleNamespace(time=time, open=close, close=close, high=close + 1 if high is None else high,
                           low=close - 1 if low is None else low, tick_volume=volume)


def test_vwap_skips_missing_volume_and_bands_enclose_center():
    rows = [bar(900 * i, 100 + i % 3) for i in range(40)]
    points = _vwap(rows)
    assert points and all(p["lower"] <= p["center"] <= p["upper"] for p in points)
    assert _vwap([bar(900 * i, volume=0) for i in range(40)]) == []


def test_pivots_need_two_confirming_bars_each_side():
    lows = [5, 4, 3, 1, 3, 4, 5, 6]
    rows = [bar(3600 * i, 10, high=11, low=v) for i, v in enumerate(lows)]
    assert _pivots(rows)["low"] == [{"time": 3 * 3600, "price": 1}]
    assert _pivots(rows[:5])["low"] == []


def test_missing_data_returns_none_blocks_not_filler():
    candle = bar(86400 * 10 + 900 * 4)
    out = chart_overlays(candle, [candle], [], [], "EURUSD")
    assert out["h4_trend"] is None and out["day"] is None and out["asia"] is None
    assert out["adr"] is None and out["vwap"] is None and out["round"]["step"] == 0.005


def test_h4_curve_uses_same_ema_seed_and_ignores_future_candles():
    higher = [bar(14400 * index, 100 + index / 10) for index in range(80)]
    candle = bar(14400 * 80 - 900, 110)
    base = chart_overlays(candle, [candle], [], higher, "EURUSD")
    changed = chart_overlays(candle, [candle], [], higher + [bar(14400 * 80, 9999)], "EURUSD")
    assert changed == base
    assert base["h4_trend"]["ema50"][-1]["value"] == pytest.approx(ema([row.close for row in higher], 50))
    assert max(point["time"] for point in base["h4_trend"]["ema50"]) <= candle.time + 900
    stale = chart_overlays(bar(candle.time + 14400), [], [], higher, "EURUSD")
    assert stale["h4_trend"] is None


def test_asia_requires_all_six_closed_bars():
    from datetime import datetime, timezone

    day = int(datetime(2026, 10, 21, tzinfo=timezone.utc).timestamp())
    hours = [bar(day + index * 3600) for index in range(6)]
    before_close = chart_overlays(bar(day + 5 * 3600), [], hours, [], "EURUSD")
    assert before_close["asia"] is None
    complete = chart_overlays(bar(day + 6 * 3600), [], hours, [], "EURUSD")
    assert complete["asia"]["high"] == 101
    missing = chart_overlays(bar(day + 6 * 3600), [], hours[1:], [], "EURUSD")
    assert missing["asia"] is None


def test_vwap_does_not_bridge_unverified_gap_or_accept_nan_volume():
    rows = [bar(900 * index) for index in range(30)]
    assert _vwap(rows)
    rows[-1].time += 900
    assert _vwap(rows) == []
    rows[-1].time -= 900
    rows[-1].tick_volume = float("nan")
    assert _vwap(rows) == []

def test_each_g0_owns_closed_indicator_lines():
    from datetime import datetime, timezone

    end = int(datetime(2026, 10, 21, 12, tzinfo=timezone.utc).timestamp())
    minutes = [bar(end - 900 * (200 - index), 100 + index / 100) for index in range(200)]
    hours = [bar(end - 3600 * (60 - index), 100 + index / 100) for index in range(60)]
    higher = [bar(end - 14400 * (60 - index), 100 + index / 100) for index in range(60)]
    candle = minutes[-1]
    shared = chart_overlays(candle, minutes, hours, higher, "EURUSD")
    cache = {}
    for tree in g0_trees("EURUSD"):
        overlays = tree_overlays(tree, candle, minutes, hours, higher, "EURUSD", shared, cache)
        assert overlays["lines"][0]["label"].startswith("EMA50 H4")
        assert all(point["time"] <= end for line in overlays["lines"] for point in line["points"])
        if tree["root"]["children"][2]["family"] == "pullback":
            labels = [line["label"] for line in overlays["lines"]]
            assert any("H1" in label and "EMA" in label for label in labels)
            assert any("M15" in label and "EMA" in label for label in labels)
