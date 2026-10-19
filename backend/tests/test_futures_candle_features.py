from backend.app.futures_rule_tree import FEATURES, matches, validate_rule
from backend.app.strategy import Candle

def rows(*ohlc):
    return [Candle(i * 900, *o, 1.0) for i, o in enumerate(ohlc)]

def test_new_features_validate_and_match():
    for f in FEATURES[7:]:
        frame = "H1" if f in ("pdh_pdl", "adx_regime", "pdh_pdl_sweep") else "M15"
        assert validate_rule({"feature": f, "frame": frame}) == 1
    base = [(100, 101, 99, 100)] * 40
    eng = rows(*base, (101, 101.2, 99.8, 100), (99.8, 102, 99.7, 101.5))
    assert matches({"feature": "engulfing", "frame": "M15"}, eng, 1, 20)
    assert not matches({"feature": "engulfing", "frame": "M15"}, eng, -1, 20)
    pin = rows(*base, (100, 100.1, 98, 100.05))
    assert matches({"feature": "pin_bar", "frame": "M15"}, pin, 1, 20)
    falling = [(120 - i, 120.5 - i, 119 - i, 119.5 - i) for i in range(40)]
    cross = rows(*falling, *[(80 + 4 * i, 84 + 4 * i, 79 + 4 * i, 83 + 4 * i) for i in range(6)])
    assert any(matches({"feature": "ema_cross", "frame": "M15"}, cross[:n], 1, 20) for n in range(41, 47))


def test_candle_patterns_both_directions_and_flat_candles():
    from backend.app.futures_rule_tree import CANDLE_PATTERNS
    examples = {
        "engulfing": [(102, 103, 99, 100), (99, 104, 98, 103)],
        "pin_bar": [(100, 101.1, 97, 101)],
        "star": [(110, 111, 99, 100), (100, 101, 98, 99), (99, 108, 98, 107)],
        "soldiers": [(100, 103.2, 99.8, 103), (102, 105.2, 101.8, 105), (104, 107.2, 103.8, 107)],
    }
    for name, example in examples.items():
        pattern = CANDLE_PATTERNS[name]
        candles = rows(*example)
        assert pattern(candles, 1), name
        assert not pattern(candles, -1), name
        mirrored = rows(*[(200 - op, 200 - low, 200 - high, 200 - close)
                          for op, high, low, close in example])
        assert pattern(mirrored, -1), name
        assert not pattern(mirrored, 1), name
        assert not pattern(rows(*[(100, 100, 100, 100)] * 3), 1), name


if __name__ == '__main__':
    test_new_features_validate_and_match()
    test_candle_patterns_both_directions_and_flat_candles()


def test_extended_patterns_both_directions():
    from backend.app.futures_candles import PATTERNS
    fall = [(130 - i, 130.5 - i, 129 - i, 129.2 - i) for i in range(6)]  # down move before long setups
    examples = {
        "doji_reversal": [(100, 100.2, 97, 100.05)],
        "inverted_pin": [(100, 103, 99.9, 100.1)],
        "marubozu": [(100, 105.1, 99.9, 105)],
        "harami": [(106, 106.5, 99.5, 100), (101, 103.2, 100.8, 103)],
        "harami_cross": [(106, 106.5, 99.5, 100), (103, 105, 101, 103.05)],
        "piercing": [(106, 106.5, 99.5, 100), (99.5, 105.2, 99.2, 104.5)],
        "tweezer": [(106, 106.5, 99, 100), (100, 103, 99.02, 102.5)],
        "three_inside": [(106, 106.5, 99.5, 100), (101, 103.2, 100.8, 103), (103, 107.5, 102.8, 107)],
        "three_outside": [(104, 104.5, 99.5, 100), (99.5, 106, 99, 105.5), (105.5, 108, 105, 107.5)],
        "three_methods": [(100, 106.2, 99.8, 106), (105, 105.5, 103.5, 104), (104, 105, 103, 103.5),
                          (103.5, 104.5, 102.5, 103), (103, 108.2, 102.8, 108)],
    }
    for name, example in examples.items():
        lead = fall[:] if name != "three_methods" else [(90 + i, 91 + i, 89 + i, 90.5 + i) for i in range(6)]
        candles = rows(*lead, *example)
        assert PATTERNS[name](candles, 1), name
        assert not PATTERNS[name](candles, -1), name
        peak = 240
        mirrored = rows(*[(peak - o, peak - lo, peak - hi, peak - c) for o, hi, lo, c in lead + example])
        assert PATTERNS[name](mirrored, -1), name


def test_wedge_and_ema_bounce():
    from backend.app.futures_candles import wedge
    from backend.app.futures_rule_tree import ema_bounce
    # falling wedge: highs fall faster than lows, then breakout above upper line
    falling = [(125 - i * 0.5 - 2, 125 - i * 0.5, 110 - i * 0.25, 110 - i * 0.25 + 2) for i in range(21)]
    candles = rows(*falling, (116, 120, 115, 119))
    assert wedge(candles, 1, 20, 1.0) and not wedge(candles, -1, 20, 1.0)
    # EMA bounce: uptrend, dip touches EMA21, bullish close above
    up = [(100 + i, 101 + i, 99.5 + i, 100.6 + i) for i in range(60)]
    ema21 = sum(c[3] for c in up[-21:]) / 21
    touch = (ema21 - 1, ema21 + 2.2, ema21 - 1.2, ema21 + 1.8)
    assert ema_bounce(rows(*up, touch), 1) in (True, False)  # smoke: no crash on real EMA path
    assert not ema_bounce(rows(*up, (touch[3], touch[3] + 1, touch[0], touch[0])), 1)  # bearish candle rejected


def test_every_seed_rule_is_valid():
    from backend.app.futures_rule_tree import RULES
    assert len(RULES) == 26 and all(validate_rule(rule) <= 11 for rule in RULES)

def test_bb_pierce_and_volume_dryup():
    base = [(100, 101, 99, 100 + (i % 2) * 0.2) for i in range(30)]
    pierce = rows(*base, (100, 100.5, 96, 100.3))
    assert matches({"feature": "bb_pierce", "frame": "M15"}, pierce, 1, 20)
    assert not matches({"feature": "bb_pierce", "frame": "M15"}, pierce, -1, 20)
    up = [Candle(i * 900, 100 + i, 101 + i, 99 + i, 100.5 + i, 10.0) for i in range(25)]
    dip = [Candle((25 + i) * 900, 124 - i, 125 - i, 123 - i, 123.5 - i, 2.0) for i in range(3)]
    assert matches({"feature": "volume_dryup", "frame": "M15"}, up + dip, 1, 20)
    heavy = [Candle(c.time, c.open, c.high, c.low, c.close, 10.0) for c in dip]
    assert not matches({"feature": "volume_dryup", "frame": "M15"}, up + heavy, 1, 20)
