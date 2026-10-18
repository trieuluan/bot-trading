"""Public MEXC USDT perpetual market data. No credentials or order endpoints."""

import math
import time
from collections.abc import Callable

import httpx

from .market import Candle
from .futures_symbols import SYMBOLS, current_symbol

INTERVAL_SECONDS = 900
SOURCE = "mexc_futures"
SYMBOL = "BTC/USDT:USDT"
TIMEFRAME = "15m"
BASE_URL = "https://api.mexc.com"


class MarketDataError(RuntimeError):
    def __init__(self, message: str, retry_after: float = 15):
        super().__init__(message)
        self.retry_after = retry_after


def validate_candles(candles: list[Candle], step: int = INTERVAL_SECONDS) -> None:
    for index, candle in enumerate(candles):
        prices = (candle.open, candle.high, candle.low, candle.close)
        if (
            not all(math.isfinite(value) and value > 0 for value in prices)
            or not math.isfinite(candle.volume)
            or candle.volume < 0
            or candle.low > min(candle.open, candle.close)
            or candle.high < max(candle.open, candle.close)
            or candle.time % step != 0
        ):
            raise MarketDataError("Invalid OHLCV candle returned by exchange")
        if index and candle.time - candles[index - 1].time != step:
            raise MarketDataError("Missing or duplicate candles; refusing to invent market data")


class MexcFuturesAdapter:
    def __init__(self, client: httpx.Client | None = None, sleep: Callable = time.sleep, symbol: str | None = None):
        self.symbol = symbol or current_symbol()
        if self.symbol not in SYMBOLS:
            raise ValueError("Unsupported Futures symbol")
        self.client = client or httpx.Client(base_url=BASE_URL, timeout=10)
        self.owns_client = client is None
        self.sleep = sleep

    def close(self):
        if self.owns_client:
            self.client.close()

    def _get(self, path: str, params: dict | None = None):
        for attempt in range(3):
            try:
                response = self.client.get(path, params=params)
                if response.status_code in (418, 429):
                    try:
                        cooldown = max(60, float(response.headers.get("Retry-After", "60")))
                        if not math.isfinite(cooldown):
                            cooldown = 60
                    except ValueError:
                        cooldown = 60
                    raise MarketDataError("MEXC rate limit; wait before retrying", cooldown)
                if response.status_code >= 500:
                    raise httpx.TransportError("MEXC temporarily unavailable")
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict) or payload.get("success") is not True:
                    raise MarketDataError("MEXC returned an unsuccessful response")
                return payload["data"]
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                if attempt == 2:
                    raise MarketDataError("Cannot reach MEXC after 3 attempts") from exc
                self.sleep(0.5 * 2**attempt)
            except (httpx.HTTPStatusError, ValueError, KeyError) as exc:
                raise MarketDataError("MEXC rejected the request or returned invalid JSON") from exc

    def ticker(self) -> dict:
        data = self._get("/api/v1/contract/ticker", {"symbol": self.symbol})
        try:
            quote = {key: float(data[key]) for key in ("lastPrice", "bid1", "ask1", "fairPrice")}
            timestamp = int(data["timestamp"]) / 1000
            if (
                data["symbol"] != self.symbol
                or any(not math.isfinite(value) or value <= 0 for value in quote.values())
                or quote["bid1"] > quote["ask1"]
                or abs(time.time() - timestamp) > 30
            ):
                raise ValueError("Stale or invalid quote")
            try:
                hold = float(data.get("holdVol", 0))
            except (TypeError, ValueError, OverflowError):
                hold = 0
            return {**quote, "time": timestamp, "received_at": time.time(),
                    **({"holdVol": hold} if hold and math.isfinite(hold) and hold > 0 else {})}
        except (KeyError, TypeError, ValueError) as exc:
            raise MarketDataError("Invalid or stale MEXC ticker") from exc

    def server_time(self) -> int:
        try:
            return int(self._get("/api/v1/contract/ping")) // 1000
        except (KeyError, TypeError, ValueError) as exc:
            raise MarketDataError("Invalid MEXC server time") from exc

    def history(
        self, start: int, end: int, server_time: int | None = None, fair: bool = False,
        interval: str = "Min15",
    ) -> list[Candle]:
        """Fetch [start, end), UTC seconds; exclude the still-open candle."""
        if interval not in ("Min1", "Min15"):
            raise MarketDataError("Unsupported history interval")
        step = 60 if interval == "Min1" else INTERVAL_SECONDS
        now = self.server_time() if server_time is None else server_time
        start = ((start + step - 1) // step) * step
        end = min(end // step * step, now // step * step)
        if end <= start or (end - start) // step > 10_000:
            raise MarketDataError("Choose a nonempty range of at most 10,000 closed candles")
        result: dict[int, Candle] = {}
        cursor = start
        while cursor < end:
            page_end = min(end, cursor + 2000 * step)
            path = "/api/v1/contract/kline/" + ("fair_price/" if fair else "") + self.symbol
            data = self._get(path, {"interval": interval, "start": cursor, "end": page_end - 1})
            try:
                keys = ("time", "open", "high", "low", "close", "vol")
                if len({len(data[key]) for key in keys}) != 1:
                    raise ValueError("Unequal OHLCV arrays")
                for row in zip(*(data[key] for key in keys)):
                    candle = Candle(int(row[0]), *(float(value) for value in row[1:]))
                    if cursor <= candle.time < page_end and candle.time + step <= now:
                        if candle.time in result and result[candle.time] != candle:
                            raise MarketDataError("Conflicting duplicate candles")
                        result[candle.time] = candle
            except (KeyError, TypeError, ValueError) as exc:
                raise MarketDataError("Malformed MEXC kline") from exc
            cursor = page_end
            if cursor < end:
                self.sleep(0.1)
        candles = sorted(result.values(), key=lambda candle: candle.time)
        validate_candles(candles, step=step)
        if not candles or candles[0].time != start or candles[-1].time != end - step:
            raise MarketDataError("MEXC history is incomplete; retry to backfill missing candles")
        return candles

    def contract_info(self) -> dict:
        data = self._get("/api/v1/contract/detail/country", {"symbol": self.symbol})
        try:
            if (
                data["symbol"] != self.symbol
                or data["settleCoin"] != "USDT"
                or data["futureType"] != 1
            ):
                raise ValueError("Unexpected contract")
            for key in ("contractSize", "volUnit", "minVol", "maxVol", "priceUnit"):
                if not math.isfinite(float(data[key])) or float(data[key]) <= 0:
                    raise ValueError("Invalid contract sizing")
            return data
        except (KeyError, TypeError, ValueError) as exc:
            raise MarketDataError("Invalid MEXC contract metadata") from exc

    def funding_history(self, start: int, end: int) -> list[dict]:
        records = {}
        covered = False
        for page in range(1, 101):
            data = self._get(
                "/api/v1/contract/funding_rate/history",
                {
                    "symbol": self.symbol,
                    "page_num": page,
                    "page_size": 100,
                },
            )
            try:
                rows = data["resultList"]
                if not rows:
                    break
                for row in rows:
                    timestamp = int(row["settleTime"]) // 1000
                    rate = float(row["fundingRate"])
                    cycle = int(row.get("collectCycle", 8))
                    if not math.isfinite(rate) or timestamp % 900 or cycle <= 0:
                        raise ValueError("Invalid funding record")
                    records[timestamp] = {"time": timestamp, "rate": rate, "cycle_hours": cycle}
                if min(records) <= start:
                    covered = True
                    break
            except (KeyError, TypeError, ValueError) as exc:
                raise MarketDataError("Malformed funding history") from exc
            self.sleep(0.1)
        if not covered:
            raise MarketDataError("Funding history does not cover requested period")
        ordered = sorted(records.values(), key=lambda record: record["time"])
        relevant = [record for record in ordered if start <= record["time"] < end]
        # Validate each interval against the cycle reported by its ending settlement.
        for previous, current in zip(ordered, ordered[1:]):
            if (
                start <= current["time"] < end
                and current["time"] - previous["time"] != current["cycle_hours"] * 3600
            ):
                raise MarketDataError("Funding history has a gap or cycle change; review data")
        if not relevant or end - relevant[-1]["time"] > relevant[-1]["cycle_hours"] * 3600:
            raise MarketDataError("Latest funding settlement is missing")
        return relevant
