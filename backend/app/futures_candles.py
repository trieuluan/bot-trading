"""Candlestick patterns + wedge as pure predicates. direction 1 = bullish, -1 = bearish.

ponytail: only patterns that don't need gaps (crypto trades 24/7, gaps ~never exist).
Add gap patterns (abandoned baby, kicking...) only for markets with real gaps.
"""

def body(row):
    return abs(row.close - row.open)

def span(row):
    return max(row.high - row.low, 1e-12)

def upper(row):
    return row.high - max(row.open, row.close)

def lower(row):
    return min(row.open, row.close) - row.low

def bull(row):
    return row.close > row.open

def prior_move(rows, skip, direction):
    """True when the 5 closes before the last `skip` candles moved against `direction`."""
    if len(rows) < skip + 5:
        return False
    return direction * (rows[-skip - 1].close - rows[-skip - 5].close) < 0

def doji_reversal(rows, direction):
    """Dragonfly (long) / gravestone (short) doji after a move against the signal."""
    last = rows[-1]
    long_wick, short_wick = (lower(last), upper(last)) if direction == 1 else (upper(last), lower(last))
    return (body(last) <= 0.1 * span(last) and long_wick >= 2 * short_wick
            and long_wick >= 0.5 * span(last) and prior_move(rows, 1, direction))

def inverted_pin(rows, direction):
    """Inverted hammer (long, after fall) / hanging man (short, after rise): wick on the other side."""
    last = rows[-1]
    wick, other = (upper(last), lower(last)) if direction == 1 else (lower(last), upper(last))
    return (wick >= 2 * max(body(last), 0.05 * span(last)) and other <= max(body(last), 0.15 * span(last))
            and prior_move(rows, 1, direction))

def marubozu(rows, direction):
    last = rows[-1]
    return direction * (last.close - last.open) > 0 and body(last) >= 0.9 * span(last)

def inside(first, second, ratio):
    return (max(second.open, second.close) <= max(first.open, first.close)
            and min(second.open, second.close) >= min(first.open, first.close)
            and body(second) <= ratio * body(first))

def harami(rows, direction):
    first, last = rows[-2:]
    return (direction * (first.close - first.open) < 0 and direction * (last.close - last.open) > 0
            and inside(first, last, 0.6) and prior_move(rows, 2, direction))

def harami_cross(rows, direction):
    first, last = rows[-2:]
    return (direction * (first.close - first.open) < 0 and body(last) <= 0.1 * span(last)
            and inside(first, last, 0.2) and prior_move(rows, 2, direction))

def piercing(rows, direction):
    """Piercing line (long) / dark cloud cover (short): close beyond the midpoint, not past the open."""
    first, last = rows[-2:]
    mid = (first.open + first.close) / 2
    return (direction * (first.close - first.open) < 0 and direction * (last.close - last.open) > 0
            and direction * (last.open - first.close) <= 0 and direction * (last.close - mid) > 0
            and direction * (last.close - first.open) < 0 and prior_move(rows, 2, direction))

def tweezer(rows, direction):
    first, last = rows[-2:]
    a, b = (first.low, last.low) if direction == 1 else (first.high, last.high)
    return (abs(a - b) <= 0.05 * max(span(first), span(last)) and direction * (first.close - first.open) < 0
            and direction * (last.close - last.open) > 0 and prior_move(rows, 2, direction))

def three_inside(rows, direction):
    first, middle, last = rows[-3:]
    return (harami(rows[:-1], direction) and direction * (last.close - last.open) > 0
            and direction * (last.close - first.open) > 0)

def three_outside(rows, direction):
    first, middle, last = rows[-3:]
    return (direction * (first.close - first.open) < 0 and direction * (middle.close - middle.open) > 0
            and body(middle) > body(first) and direction * (middle.close - first.open) >= 0
            and direction * (middle.open - first.close) <= 0 and direction * (last.close - middle.close) > 0
            and prior_move(rows, 3, direction))

def three_methods(rows, direction):
    """Rising/falling three methods: strong candle, three small counter candles inside it, strong breakout."""
    first, *middle, last = rows[-5:]
    return (direction * (first.close - first.open) > 0 and direction * (last.close - last.open) > 0
            and all(direction * (row.close - row.open) <= 0 and row.high <= first.high
                    and row.low >= first.low and body(row) < body(first) for row in middle)
            and direction * (last.close - first.close) > 0)

def fit(values):
    count = len(values)
    mean_x, mean_y = (count - 1) / 2, sum(values) / count
    slope = (sum((index - mean_x) * (value - mean_y) for index, value in enumerate(values))
             / sum((index - mean_x) ** 2 for index in range(count)))
    return slope, mean_y - slope * mean_x

def wedge(rows, direction, size, atr):
    """Falling wedge breaks up (long); rising wedge breaks down (short). Trendlines by least squares."""
    window, last = rows[-size - 1:-1], rows[-1]
    high_slope, high_base = fit([row.high for row in window])
    low_slope, low_base = fit([row.low for row in window])
    gap_start, gap_end = high_base - low_base, (high_base + high_slope * (size - 1)) - (low_base + low_slope * (size - 1))
    if gap_end <= 0 or gap_end > 0.8 * gap_start or min(abs(high_slope), abs(low_slope)) * size < 0.5 * atr:
        return False
    if direction == 1:
        return (high_slope < 0 and low_slope < 0 and high_slope < low_slope
                and last.close > high_base + high_slope * size and bull(last))
    return (high_slope > 0 and low_slope > 0 and low_slope > high_slope
            and last.close < low_base + low_slope * size and not bull(last))

PATTERNS = {
    "doji_reversal": doji_reversal, "inverted_pin": inverted_pin, "marubozu": marubozu,
    "harami": harami, "harami_cross": harami_cross, "piercing": piercing, "tweezer": tweezer,
    "three_inside": three_inside, "three_outside": three_outside, "three_methods": three_methods,
}
LABELS = {
    "doji_reversal": "Doji đảo chiều (Dragonfly/Gravestone)", "inverted_pin": "Inverted Hammer/Hanging Man",
    "marubozu": "Nến Marubozu (thân ≥ 90%)", "harami": "Harami (nến nằm trong thân nến trước)",
    "harami_cross": "Harami Cross (Doji trong thân nến trước)", "piercing": "Piercing Line/Dark Cloud Cover",
    "tweezer": "Tweezer Bottom/Top (hai nến chạm cùng đáy/đỉnh)", "three_inside": "Three Inside Up/Down",
    "three_outside": "Three Outside Up/Down", "three_methods": "Rising/Falling Three Methods",
    "wedge": "Nêm giảm phá lên / nêm tăng phá xuống", "ema_bounce": "Nến bật khỏi EMA21/EMA50 cùng xu hướng",
}
