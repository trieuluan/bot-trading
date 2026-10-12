from dataclasses import dataclass

from .market import Candle


@dataclass(frozen=True)
class Setup:
    side: str
    entry: float
    stop: float
    target: float
    reason: str


def breakout(candles: list[Candle], lookback: int = 20) -> Setup | None:
    """Evaluate the latest CLOSED candle against previous closed candles only."""
    if lookback < 2:
        raise ValueError("lookback must be at least 2")
    if len(candles) < lookback + 1:
        return None
    current = candles[-1]
    history = candles[-lookback - 1 : -1]
    level = max(c.high for c in history)
    if current.close <= level:
        return None
    stop = min(c.low for c in history[-5:])
    risk = current.close - stop
    if risk <= 0:
        return None
    return Setup(
        "long",
        current.close,
        stop,
        current.close + 2 * risk,
        "Nến đóng cửa vượt đỉnh 20 nến trước; SL dưới đáy 5 nến; mục tiêu 2R.",
    )
