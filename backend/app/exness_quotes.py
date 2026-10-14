"""One read-only upstream per API process; ticks monitor local paper exits only."""

import asyncio
import json
import logging
import math
import os
import time
from collections import deque
from datetime import datetime

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidHandshake, InvalidStatus

from .exness import ExnessError, ExnessReadOnly, signed_headers
from .forex_paper import sync_paper_quote

TICKS_PATH = "/v1/server-events/accounts/{account_id}/ws/ticks"
CHART_FRAMES = {"15m": 900, "1h": 3600, "4h": 14400}
router = APIRouter()


def stream_config(api):
    api.use_access_point()
    limits = api.limits()["limits"]["websocket"]
    global_limits = limits["global_account_limits"]
    endpoint = next(item for item in limits["endpoints"] if item["path"] == TICKS_PATH)
    if global_limits["max_active_connections"] < 1:
        raise ValueError("No WebSocket capacity")
    rates = [
        global_limits["connection_rate"],
        endpoint["connection_rate"],
        global_limits["inbound_message_rate"],
        endpoint["inbound_message_rate"],
        endpoint["subscription_operation_rate"]["ticks"],
    ]
    intervals = []
    for rate in rates:
        limit, window = rate["limit"], rate["window_seconds"]
        if isinstance(limit, bool) or limit < 1 or not math.isfinite(window) or window <= 0:
            raise ValueError("Invalid WebSocket limits")
        intervals.append(window / limit)
    subscription = json.dumps(
        {"id": "chart-xauusd", "subscribe": {"event": "ticks", "instruments": ["XAUUSD"]}}
    )
    if len(subscription.encode()) > global_limits["max_inbound_message_bytes"]:
        raise ValueError("Subscription exceeds WebSocket limits")
    return max(5, *intervals), subscription


def parse_quote(message, now):
    if not isinstance(message, dict) or message.get("instrument") != "XAUUSD":
        return None
    try:
        if isinstance(message["bid"], bool) or isinstance(message["ask"], bool):
            return None
        bid, ask = float(message["bid"]), float(message["ask"])
        stamp = datetime.fromisoformat(message["timestamp"].replace("Z", "+00:00"))
        if (
            stamp.tzinfo is None
            or stamp.timestamp() > now + 5
            or not all(math.isfinite(value) and value > 0 for value in (bid, ask))
            or ask < bid
        ):
            return None
        return {"bid": bid, "ask": ask, "time": stamp.timestamp(), "received_at": now}
    except (KeyError, ValueError, TypeError, AttributeError, OverflowError):
        return None


class QuoteStream:
    def __init__(self):
        self.status = "disabled"
        self.quote = None
        self.task = None
        self.candles = {frame: deque(maxlen=32) for frame in CHART_FRAMES}

    def accept_quote(self, quote):
        previous = self.quote
        if previous and quote["time"] < previous["time"]:
            return
        gap = previous is None or quote["time"] - previous["time"] > 15
        for frame, seconds in CHART_FRAMES.items():
            stamp = int(quote["time"] // seconds) * seconds
            rows = self.candles[frame]
            if not rows or rows[-1]["time"] != stamp:
                rows.append(
                    {
                        "time": stamp,
                        "partial": gap,
                        **{
                            side: {name: quote[side] for name in ("open", "high", "low", "close")}
                            for side in ("bid", "ask")
                        },
                    }
                )
            else:
                rows[-1]["partial"] |= gap
                for side in ("bid", "ask"):
                    bar = rows[-1][side]
                    bar["high"] = max(bar["high"], quote[side])
                    bar["low"] = min(bar["low"], quote[side])
                    bar["close"] = quote[side]
        self.quote = quote

    def snapshot(self):
        age = None if self.quote is None else max(0, time.time() - self.quote["time"])
        status = self.status
        if status == "connected":
            status = "waiting" if age is None else "live" if age <= 15 else "stale"
        return {
            "status": status,
            "symbol": "XAUUSD",
            "quote": self.quote,
            "age_seconds": age,
            "candles": {frame: list(rows) for frame, rows in self.candles.items()},
        }

    def start(self):
        if os.getenv("EXNESS_DATA_ENABLED", "false").lower() == "true":
            self.status = "connecting"
            self.task = asyncio.create_task(self.run())

    async def stop(self):
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
            self.task = None
        self.status = "disabled"

    async def run(self):
        api = None
        try:
            if (
                os.getenv("EXNESS_ENVIRONMENT", "demo") != "demo"
                or os.getenv("EXNESS_SYMBOL", "XAUUSD") != "XAUUSD"
            ):
                raise ValueError("Demo XAUUSD only")
            api = ExnessReadOnly.from_env()
            setup_delay = 60
            while True:
                try:
                    minimum_delay, subscription = await asyncio.to_thread(stream_config, api)
                    break
                except ExnessError as exc:
                    if not exc.rate_limited:
                        raise
                    self.status = "rate_limited"
                    await asyncio.sleep(max(setup_delay, exc.retry_after))
                    setup_delay = min(setup_delay * 2, 300)
            delay = minimum_delay
            path = TICKS_PATH.format(account_id=api.account_id)
            while True:
                self.status = "connecting"
                self.quote = None
                paper_gap = True
                for rows in self.candles.values():
                    rows.clear()
                try:
                    async with connect(
                        api.base_url.replace("https://", "wss://", 1) + path,
                        additional_headers=signed_headers(api.api_key, api.key, path),
                        subprotocols=["exness-ws-protocol"],
                        compression=None,
                        proxy=None,
                        max_size=65536,
                        max_queue=16,
                        open_timeout=10,
                        ping_interval=20,
                        ping_timeout=20,
                    ) as socket:
                        await socket.send(subscription)
                        self.status = "connected"
                        async for raw in socket:
                            message = json.loads(raw)
                            if isinstance(message, dict) and message.get("error"):
                                error = message["error"]
                                code = error.get("code") if isinstance(error, dict) else error
                                if code == "REQUEST_RATE_LIMIT":
                                    raise ExnessError("Exness rate limit; waiting before retry", max(60, delay), rate_limited=True)
                                self.status = "rejected"
                                return
                            quote = parse_quote(message, time.time())
                            if quote and (
                                self.quote is None or quote["time"] >= self.quote["time"]
                            ):
                                self.accept_quote(quote)
                                try:
                                    await asyncio.to_thread(sync_paper_quote, quote, paper_gap)
                                    paper_gap = False
                                except Exception:
                                    paper_gap = True
                                    logging.exception("Exness paper tick failed; defer to candle recovery")
                                delay = minimum_delay
                except InvalidStatus as exc:
                    if exc.response.status_code < 500:
                        if exc.response.status_code == 429:
                            self.status = "rate_limited"
                            try:
                                retry_after = float(exc.response.headers.get("Retry-After", "60"))
                                if not math.isfinite(retry_after):
                                    raise ValueError
                            except (ValueError, TypeError):
                                retry_after = 60
                            delay = max(delay, 60, min(retry_after, 3600))
                        else:
                            self.status = "rejected"
                            return
                except ConnectionClosed as exc:
                    if exc.rcvd and exc.rcvd.code not in (1000, 1001, 1006, 1011, 1012, 1013):
                        self.status = "rejected"
                        return
                except (OSError, TimeoutError):
                    pass
                except ExnessError as exc:
                    if not exc.rate_limited:
                        raise
                    self.status = "rate_limited"
                    delay = max(delay, exc.retry_after)
                if self.status != "rate_limited":
                    self.status = "reconnecting"
                await asyncio.sleep(delay)
                delay = max(minimum_delay, min(delay * 2, 300))
        except ExnessError as exc:
            self.status = "rate_limited" if "rate limit" in str(exc) else "unavailable"
        except (ValueError, KeyError, TypeError, StopIteration, InvalidHandshake):
            self.status = "unavailable"
        finally:
            if api:
                api.client.close()


quotes = QuoteStream()


@router.get("/api/workspaces/exness_forex/quotes/stream")
async def quote_events():
    async def events():
        while True:
            yield "data: " + json.dumps(quotes.snapshot()) + "\n\n"
            await asyncio.sleep(1)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )
