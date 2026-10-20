import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from backend.app.forex_detectors import H1_DETECTORS, M15_TRIGGERS, family_context

CASES = json.loads(Path(__file__).with_name("fixtures").joinpath("forex_m15_recognition.json").read_text())


def bar(t, open_=100.0, close=100.0, high=101.0, low=99.0, volume=100):
    row = SimpleNamespace(time=t, open=open_, close=close, high=high, low=low)
    row.tick_volume = volume
    return row


def make_context():
    base = 100 * 14400
    higher = [bar(base - 14400 * (60 - i), close=2000 + i * 2, high=2005 + i * 2, low=1995 + i * 2) for i in range(60)]
    hours = [bar(base - 3600 * (60 - i), close=2100 + i, high=2102 + i, low=2098 + i) for i in range(60)]
    minutes = [bar(base - 900 * (60 - i), close=2155, high=2156, low=2154) for i in range(60)]
    candle = bar(base, open_=2155, close=2156, high=2157, low=2154)
    return candle, hours, minutes, higher


def test_m15_all_14_families_agree_with_frozen_legacy_recognition():
    candle, hours, minutes, higher = make_context()
    assert {case["family"] for case in CASES} == set(M15_TRIGGERS)
    for case in CASES:
        family, params = case["family"], case["params"]
        ctx = family_context(family, params, candle, hours, higher)
        for side in ("long", "short"):
            result = M15_TRIGGERS[family](candle, minutes, side, {}, params, ctx)
            assert result == (case["side"] == side), f"{family} {side}"


def test_orb_dst_and_timezone_handling():
    zone = ZoneInfo("Europe/London")
    dt = datetime(2026, 6, 15, 8, 30, tzinfo=zone)
    stamp = int(dt.timestamp())
    opening = int(dt.replace(minute=0, second=0).timestamp())
    history = [bar(opening + i * 900, high=101, low=99) for i in range(-22, 2)]
    candle = bar(stamp, open_=100, close=103, high=104, low=100)
    params = {"session": "london", "range_bars": 2, "trade_bars": 8, "trend": False}
    assert M15_TRIGGERS["orb"](candle, history, "long", {}, params, {})
    assert not M15_TRIGGERS["orb"](candle, history, "short", {}, params, {})


def test_vsa_and_vwap_reject_missing_or_invalid_tick_volume():
    candle, hours, minutes, higher = make_context()
    for row in minutes:
        row.tick_volume = None
    vsa_params = {"lookback": 20, "vol_mult": 1.5, "wick_ratio": 0.6, "trend": False}
    vwap_params = {"lookback": 30, "dev_mult": 2.5, "trend": False}
    assert not M15_TRIGGERS["vsa"](candle, minutes, "long", {}, vsa_params, {})
    assert not M15_TRIGGERS["vwap"](candle, minutes, "long", {}, vwap_params, {})


def test_h1_setups_produce_structured_invalidation():
    candle, hours, minutes, higher = make_context()
    for name in ("channel_breakout", "sweep", "pullback", "fvg", "squeeze", "mss", "retest"):
        res = H1_DETECTORS[name](hours, "long")
        if res is not None:
            assert "level" in res and "invalidation" in res and res["atr"] > 0
