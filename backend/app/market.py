import math
import random
from dataclasses import asdict, dataclass
from typing import Protocol


@dataclass(frozen=True)
class Candle:
    time: int
    open: float
    high: float
    low: float
    close: float
    volume: float


class MarketDataAdapter(Protocol):
    def candles(self, symbol: str, limit: int) -> list[Candle]: ...


class DemoAdapter:
    """Deterministic synthetic replay. No exchange or network connection."""

    def candles(self, symbol: str = "BTC/USDT", limit: int = 120, end: int = 120) -> list[Candle]:
        if symbol != "BTC/USDT":
            raise ValueError("Demo adapter only supports BTC/USDT")
        rng = random.Random(42)
        price = 62500.0
        result = []
        for i in range(end):
            opening = price
            price += math.sin(i / 8) * 85 + rng.uniform(-150, 180)
            result.append(
                Candle(
                    1791763200 + i * 900,
                    opening,
                    max(opening, price) + rng.uniform(40, 160),
                    min(opening, price) - rng.uniform(40, 160),
                    price,
                    rng.uniform(60, 320),
                )
            )
        return result[-limit:]


def serialize(candles):
    return [asdict(c) for c in candles]
