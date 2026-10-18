from datetime import datetime, timezone
from backend.app.macro_calendar import is_macro_safe
from backend.app.futures_rule_tree import matches, validate_rule
from backend.app.market import Candle

def candle(t, o, h, l, c, v=1.0):
    return Candle(t, o, h, l, c, v)

def test_macro_calendar_blocks_release_window():
    # 2026-10-14 08:30 EDT = 12:30 UTC = 1791981000
    dt = datetime(2026, 10, 14, 12, 30, tzinfo=timezone.utc)
    ts = int(dt.timestamp())
    assert not is_macro_safe(ts)
    assert not is_macro_safe(ts - 2 * 3600)  # 2 hours before
    assert not is_macro_safe(ts + 2 * 3600)  # 2 hours after
    assert is_macro_safe(ts - 5 * 3600)      # 5 hours before: safe
    assert is_macro_safe(ts + 5 * 3600)      # 5 hours after: safe

def test_macro_safe_rule_predicate():
    rule = {"feature": "macro_safe", "frame": "H1"}
    assert validate_rule(rule) == 1
    # Inside CPI window
    ts = int(datetime(2026, 10, 14, 12, 30, tzinfo=timezone.utc).timestamp())
    candles_danger = [candle(ts - (20 - i) * 900, 100, 101, 99, 100) for i in range(21)]
    assert not matches(rule, candles_danger, 1, 20)
    # Outside window
    candles_safe = [candle(ts - 5 * 3600 - (20 - i) * 900, 100, 101, 99, 100) for i in range(21)]
    assert matches(rule, candles_safe, 1, 20)

def test_relative_strength_rule_predicate():
    rule = {"feature": "relative_strength", "frame": "H1"}
    assert validate_rule(rule) == 1
    # Altcoin rises over 100 M15 bars (= 25 H1 bars)
    alt = [candle(i * 900, 100, 100 + i * 0.1, 99, 100 + i * 0.1) for i in range(100)]
    # BTC stays flat
    btc = [candle(i * 900, 60000, 60100, 59900, 60000) for i in range(100)]
    assert matches(rule, alt, 1, 20, context={"btc_candles": btc})
    assert not matches(rule, alt, -1, 20, context={"btc_candles": btc})

def test_funding_aligned_rule_predicate():
    rule = {"feature": "funding_aligned", "frame": "H1"}
    assert validate_rule(rule) == 1
    now = 1700000000
    candles = [candle(now - (20 - i) * 900, 100, 101, 99, 100) for i in range(21)]
    # Negative funding: short pays long -> long is favored
    funding = [{"time": now, "rate": -0.0002}]
    assert matches(rule, candles, 1, 20, context={"funding": funding})
    assert not matches(rule, candles, -1, 20, context={"funding": funding})
    # Positive funding: long pays short -> short is favored
    funding = [{"time": now, "rate": 0.0005}]
    assert not matches(rule, candles, 1, 20, context={"funding": funding})
    assert matches(rule, candles, -1, 20, context={"funding": funding})
