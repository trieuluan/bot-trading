"""Closed native-frame execution settings for D1/H4 research seeds."""

from .strategy import aggregate

NATIVE_ENTRY = "htf_close"
NATIVE_FRAMES = {"D1": 86400, "H4": 14400}
NATIVE_WARMUP = 6000
NATIVE_FEATURES = ("native_squeeze", "native_sweep", "native_trend", "daily_bias")


def entry_session_allowed(genome, timestamp):
    return genome.get("entry_session") != "utc_12_20" or 12 * 3600 <= timestamp % 86400 < 20 * 3600


def native_atr(rows):
    value = rows[0].high - rows[0].low
    previous = rows[0].close
    for bar in rows[1:]:
        value += (max(bar.high - bar.low, abs(bar.high - previous), abs(bar.low - previous)) - value) / 14
        previous = bar.close
    return value


def hold_seconds(frame=None):
    return {"D1": 15 * 86400, "H4": 5 * 86400}.get(frame, 192 * 900)


def native_levels(candles, frame, direction, target_r):
    rows = aggregate(candles, NATIVE_FRAMES[frame])
    if len(rows) < 15:
        return None
    distance = native_atr(rows)
    swing = min(bar.low for bar in rows[-5:]) if direction == 1 else max(bar.high for bar in rows[-5:])
    risk = max(direction * (rows[-1].close - swing) + 0.25 * distance, distance)
    entry = candles[-1].close
    return entry - direction * risk, entry + direction * target_r * risk
