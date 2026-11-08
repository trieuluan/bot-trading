import pytest

from backend.app.futures_symbols import SYMBOLS
from backend.app.market import Candle
from scripts.replay_recent_vps_signals import merge_candles, snapshot_geometry


def test_snapshot_geometry_uses_immutable_audit_and_rejects_missing():
    decision = {
        "setup": {"side": "long", "stop": 99.0},
        "audit": {"genome": {"rule": {"id": 1}}, "initial_stop": 95.0, "initial_target": 110.0},
    }
    assert snapshot_geometry(decision) == ({"rule": {"id": 1}}, "long", 95.0, 110.0)
    with pytest.raises(ValueError):
        snapshot_geometry({"setup": {"side": "long"}, "audit": {"initial_stop": 95.0, "initial_target": 110.0}})
    with pytest.raises(ValueError):
        snapshot_geometry({"setup": {"side": "long"}, "audit": {"genome": {"rule": 1}, "initial_stop": 0, "initial_target": 110.0}})


def test_merge_candles_verifies_vps_overlap_and_rejects_mismatch():
    public = {
        sym: (0, 2700, [Candle(t, 100, 102, 99, 101, 10) for t in (0, 900, 1800)],
              [Candle(t, 100, 102, 99, 101, 0) for t in (0, 900, 1800)],
              [{"time": 900, "rate": 0.0001}, {"time": 2700, "rate": 0.0002}])
        for sym in SYMBOLS
    }
    snapshot = {
        "closed_before": 2700,
        "candles": [
            {"symbol": sym.replace("_", "/") + ":USDT", "source": "mexc_futures", "timeframe": "15m",
             "time": t, "open": 100, "high": 102, "low": 99, "close": 101, "volume": 10}
            for sym in SYMBOLS for t in (900, 1800)
        ],
    }
    merged, overlap = merge_candles(public, snapshot)
    assert overlap["BTC_USDT"] == 2 and len(merged["BTC_USDT"][4]) == 1
    snapshot["candles"][0]["close"] = 999
    with pytest.raises(ValueError):
        merge_candles(public, snapshot)
