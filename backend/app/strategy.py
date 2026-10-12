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
