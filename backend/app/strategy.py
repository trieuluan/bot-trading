from dataclasses import dataclass

from .market import Candle


@dataclass(frozen=True)
class Setup:
    side: str
    entry: float
    stop: float
    target: float
    reason: str


def breakout(candles: list[Candle], lookback: int = 20, allow_short: bool = False) -> Setup | None:
    """Evaluate the latest CLOSED candle against previous closed candles only."""
    if lookback < 2:
        raise ValueError("lookback must be at least 2")
    if len(candles) < lookback + 1:
        return None
    current = candles[-1]
    history = candles[-lookback - 1 : -1]
    level = max(c.high for c in history)
    if current.close > level:
        stop = min(c.low for c in history[-5:])
        risk = current.close - stop
        return (
            Setup(
                "long",
                current.close,
                stop,
                current.close + 2 * risk,
                f"Nến đóng vượt đỉnh {lookback} nến; SL đáy 5 nến; mục tiêu 2R.",
            )
            if risk > 0
            else None
        )
    if allow_short and current.close < min(c.low for c in history):
        stop = max(c.high for c in history[-5:])
        risk = stop - current.close
        target = current.close - 2 * risk
        return (
            Setup(
                "short",
                current.close,
                stop,
                target,
                f"Nến đóng phá đáy {lookback} nến; SL đỉnh 5 nến; mục tiêu 2R.",
            )
            if risk > 0 and target > 0
            else None
        )
    return None

def retest(
    candles: list[Candle], lookback: int = 20, wait: int = 10, allow_short: bool = False
) -> Setup | None:
    """Breakout close, retest of broken level, confirming close. Fires only on the latest closed candle."""
    last = len(candles) - 1
    for b in range(last - 1, max(lookback - 1, last - wait - 1), -1):
        hist = candles[b - lookback : b]
        for d in (1, -1) if allow_short else (1,):
            level = max(c.high for c in hist) if d == 1 else min(c.low for c in hist)
            if d * (candles[b].close - level) <= 0:
                continue
            touched = []
            for j in range(b + 1, min(last, b + wait) + 1):  # expires after `wait` candles
                c = candles[j]
                if d * (c.close - level) < 0:
                    break  # closed back through the zone: setup cancelled
                if touched and d * (c.close - c.open) > 0 and d * (c.close - level) > 0:
                    if j == last:
                        touched.append(c)
                        stop = min(x.low for x in touched) if d == 1 else max(x.high for x in touched)
                        risk = d * (c.close - stop)
                        target = c.close + d * 2 * risk
                        if risk > 0 and target > 0:
                            return Setup(
                                "long" if d == 1 else "short",
                                c.close,
                                stop,
                                target,
                                f"Breakout {lookback} nến, retest vùng {level:g}, nến xác nhận giữ vùng; SL theo retest; mục tiêu 2R.",
                            )
                    break  # confirmed earlier (already signalled) or invalid
                if d * (level - (c.low if d == 1 else c.high)) >= 0 or touched:
                    touched.append(c)
    return None
