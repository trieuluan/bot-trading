from backend.app.market import Candle
from backend.app.futures_rule_tree import matches, validate_rule, validate_htf_rule, nodes
from backend.app.futures_strategies import RULE_SEEDS, validate_genome

def c(t, o, h, l, cl, v=100.0):
    return Candle(t, o, h, l, cl, v)

def test_fib_retrace_predicate():
    rule = {"feature": "fib_retrace", "frame": "H1"}
    assert validate_rule(rule) == 1
    # Swing low at index 6 (t=6h, low=50), Swing high at index 14 (t=14h, high=100)
    # Range = 50. Fib 50% = 75, Fib 61.8% = 69.1. Last bar closes at 72 (between 69.1 and 75)
    bars = []
    for i in range(18):
        if i == 6:
            bars.append(c(i * 3600, 60, 62, 50, 61))
        elif i == 14:
            bars.append(c(i * 3600, 95, 100, 93, 98))
        elif i < 6:
            bars.append(c(i * 3600, 70 - i, 72 - i, 68 - i, 69 - i))
        elif i < 14:
            bars.append(c(i * 3600, 60 + (i - 6) * 4.5, 62 + (i - 6) * 4.5, 59 + (i - 6) * 4.5, 61 + (i - 6) * 4.5))
        elif i < 17:
            bars.append(c(i * 3600, 90 - (i - 14) * 5, 92 - (i - 14) * 5, 85 - (i - 14) * 5, 86 - (i - 14) * 5))
        else:
            bars.append(c(i * 3600, 75, 76, 71, 72))
    m15 = [c(b.time + s * 900, b.open, b.high, b.low, b.close, b.volume / 4) for b in bars for s in range(4)]
    assert matches(rule, m15, 1, 20)
    assert not matches(rule, m15, -1, 20)

def test_fvg_predicate():
    rule = {"feature": "fvg", "frame": "H1"}
    assert validate_rule(rule) == 1
    b1 = c(0, 95, 100, 94, 98)
    b2 = c(3600, 102, 110, 102, 109)
    b3 = c(7200, 106, 108, 104, 107)
    b4 = c(10800, 101, 105, 101, 103)
    m15 = [c(b.time + s * 900, b.open, b.high, b.low, b.close, b.volume / 4) for b in (b1, b2, b3, b4) for s in range(4)]
    assert matches(rule, m15, 1, 20)
    assert not matches(rule, m15, -1, 20)

def test_rsi_divergence_predicate():
    rule = {"feature": "rsi_divergence", "frame": "H1"}
    assert validate_rule(rule) == 1
    # 35 H1 bars with higher low prices but dropping RSI (bearish div) or lower low prices with rising RSI (bullish div)
    # Create bullish divergence:
    # Pivot 1 at index 18 (low=70, rsi lower)
    # Pivot 2 at index 28 (low=60, rsi higher)
    bars = [c(i * 3600, 100 - i * 0.8, 102 - i * 0.8, 98 - i * 0.8, 99 - i * 0.8) for i in range(18)]
    bars.append(c(18 * 3600, 75, 76, 70, 72))  # Pivot 1 low = 70
    # Bounce up to 85
    for i in range(19, 26):
        bars.append(c(i * 3600, 72 + (i - 18) * 2, 75 + (i - 18) * 2, 71 + (i - 18) * 2, 74 + (i - 18) * 2))
    # Slow drift down with smaller losses (RSI stays higher), low dips to 65
    for i in range(26, 29):
        bars.append(c(i * 3600, 78 - (i - 25) * 3, 79 - (i - 25) * 3, 76 - (i - 25) * 3, 77 - (i - 25) * 3))
    bars.append(c(29 * 3600, 68, 69, 65, 67))  # Pivot 2 low = 65 < 70
    # Confirm pivot with 2 bars up and last bar bullish
    bars.append(c(30 * 3600, 67, 72, 66, 71))
    bars.append(c(31 * 3600, 71, 75, 70, 74))
    m15 = [c(b.time + s * 900, b.open, b.high, b.low, b.close, b.volume / 4) for b in bars for s in range(4)]
    closes = [100 - i for i in range(19)] + [84, 86, 88, 90, 92] + [91, 89, 87, 85, 83, 81] + [83, 85]
    synthetic = [c(i * 3600, close - 0.5, close + 1, close - 1, close, 100)
                 for i, close in enumerate(closes)]
    synthetic_m15 = [c(b.time + s * 900, b.open, b.high, b.low, b.close, b.volume / 4)
                     for b in synthetic for s in range(4)]
    assert matches(rule, synthetic_m15, 1, 20)
    assert not matches(rule, synthetic_m15, -1, 20)

def test_bos_predicate():
    rule = {"feature": "bos", "frame": "H1"}
    assert validate_rule(rule) == 1
    bars = []
    for i in range(15):
        if i == 5:
            bars.append(c(i * 3600, 110, 120, 108, 115))  # Pivot high at 120
        elif i < 14:
            bars.append(c(i * 3600, 100, 105, 95, 100))
        else:
            bars.append(c(i * 3600, 118, 125, 117, 122))  # Breakout close > 120
    m15 = [c(b.time + s * 900, b.open, b.high, b.low, b.close, b.volume / 4) for b in bars for s in range(4)]
    assert matches(rule, m15, 1, 20)
    assert not matches(rule, m15, -1, 20)

def test_session_vwap_predicate():
    rule = {"feature": "session_vwap", "frame": "H1"}
    assert validate_rule(rule) == 1
    day = 86400
    b1 = c(day, 100, 105, 95, 98, 1000.0)      # Close below VWAP
    b2 = c(day + 3600, 98, 103, 97, 102, 1000.0) # Crosses and closes above VWAP
    m15 = [c(b.time + s * 900, b.open, b.high, b.low, b.close, b.volume / 4) for b in (b1, b2) for s in range(4)]
    assert matches(rule, m15, 1, 20)
    assert not matches(rule, m15, -1, 20)

def test_all_five_seeds_registered_and_valid():
    new_features = {"fib_retrace", "rsi_divergence", "fvg", "bos", "session_vwap"}
    found = {n["feature"] for s in RULE_SEEDS for _, n in nodes(s["rule"]) if n.get("feature") in new_features}
    assert found == new_features
    assert len(RULE_SEEDS) == 23
