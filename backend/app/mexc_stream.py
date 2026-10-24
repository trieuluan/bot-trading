"""Public ticker WebSocket for Paper stops; REST/history remain independent fallbacks."""

import json
import logging
import math
import threading
import time

from websockets.sync.client import connect

from .futures_symbols import symbol_scope
from .paper import persist_paper_quote

STREAM_URL = "wss://contract.mexc.com/edge"
logger = logging.getLogger(__name__)
_health = {}
_health_lock = threading.Lock()

def clear_stream_health(symbol):
    with _health_lock:
        _health.pop(symbol, None)

def note_stream_quote(symbol, quote):
    now = time.monotonic()
    with _health_lock:
        previous = _health.get(symbol)
        if previous and quote["time"] <= previous["quote_time"]:
            return
        continuous = previous is not None and 0 <= now - previous["last"] < 5
        _health[symbol] = {
            "count": previous["count"] + 1 if continuous else 1,
            "since": previous["since"] if continuous else now,
            "last": now, "quote_time": quote["time"], "received_at": quote["received_at"],
        }

def stream_ready(symbol):
    now, wall = time.monotonic(), time.time()
    with _health_lock:
        health = _health.get(symbol)
        return bool(health and health["count"] >= 3 and now - health["since"] >= 2
                    and 0 <= now - health["last"] < 5
                    and 0 <= wall - health["received_at"] < 5
                    and -1 <= wall - health["quote_time"] < 5)


def parse_ticker(raw, symbol, now=None):
    now = time.time() if now is None else now
    try:
        message = json.loads(raw)
        data = message["data"]
        if (message.get("channel") != "push.ticker" or message.get("symbol") != symbol
                or not isinstance(data, dict) or data.get("symbol") != symbol):
            return None
        timestamp = float(data["timestamp"]) / 1000
        envelope_time = float(message["ts"]) / 1000
        if not (-1 <= now - timestamp <= 5 and -1 <= now - envelope_time <= 5):
            return None
        prices = {key: float(data[key]) for key in ("lastPrice", "bid1", "ask1", "fairPrice")}
        if (any(isinstance(data[key], bool) for key in prices)
                or not all(math.isfinite(price) and price > 0 for price in prices.values())
                or prices["bid1"] > prices["ask1"]):
            return None
        quote = {**prices, "time": timestamp, "received_at": now}
        for key in ("fundingRate", "holdVol"):
            try:
                value = float(data[key])
                if math.isfinite(value) and (key != "holdVol" or value > 0):
                    quote[key] = value
            except (KeyError, TypeError, ValueError, OverflowError):
                pass
        return quote
    except (KeyError, TypeError, ValueError, OverflowError):
        return None


def stream_paper_quotes(symbol, stop):
    delay = 1
    with symbol_scope(symbol):
        while not stop.is_set():
            clear_stream_health(symbol)
            try:
                with connect(STREAM_URL, open_timeout=10, close_timeout=2,
                             ping_interval=None, max_size=65536, max_queue=8) as socket:
                    socket.send(json.dumps({"method": "sub.ticker", "param": {"symbol": symbol}}))
                    last_tick = last_ping = time.monotonic()
                    while not stop.is_set():
                        now = time.monotonic()
                        if now - last_tick > 10:
                            raise TimeoutError("MEXC ticker silent for 10 seconds")
                        if now - last_ping >= 5:
                            socket.send(json.dumps({"method": "ping"}))
                            last_ping = now
                        try:
                            raw = socket.recv(timeout=1)
                        except TimeoutError:
                            continue
                        quote = parse_ticker(raw, symbol)
                        if quote is None:
                            continue
                        if persist_paper_quote(quote, realtime=True):
                            note_stream_quote(symbol, quote)
                        last_tick, delay = time.monotonic(), 1
            except Exception:
                clear_stream_health(symbol)
                logger.exception("MEXC ticker disconnected for %s; REST fallback remains active", symbol)
                stop.wait(delay)
                delay = min(delay * 2, 30)
        clear_stream_health(symbol)
