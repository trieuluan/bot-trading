"""Independent read-only forex feed process; no MEXC worker or trading imports."""

import logging
import os
import signal
import threading
import time

from backend.app.exness import ExnessError
from backend.app.exness_execution import execution_tick, record_error
from backend.app.exness_feed import (
    FRAMES,
    connect_exness,
    feed_retry_delay,
    record_connection_failure,
    sync_page,
    worker_heartbeat,
)
from backend.app.exness_observation import observation_tick
from backend.app.forex_costs import sync_specs
from backend.app.forex_htf_v2 import safe_htf_ast3_tick
from backend.app.forex_symbols import configured_symbols
from backend.app.settings import validate_mode


def throttle_delay(retry_after, streak):
    return max(retry_after, min(max(60, retry_after) * 2 ** min(max(streak - 1, 0), 4), 900))

def main():
    validate_mode()
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    api = None
    retry_until = 0
    history_index = 0
    throttle_streak = 0
    last_throttle = 0.0
    symbol_index = 0
    history_after = 0.0
    try:
        while not stop.is_set():
            enabled = os.getenv("EXNESS_DATA_ENABLED", "false").lower() == "true"
            delay = 15
            try:
                worker_heartbeat(enabled)
                if enabled:
                    if time.monotonic() < retry_until:
                        stop.wait(15)
                        continue
                    if all(feed_retry_delay(symbol) > 0 for symbol in configured_symbols()):
                        stop.wait(15)
                        continue
                    if api is None:
                        api = connect_exness()
                    throttled = False
                    symbols = configured_symbols()
                    for symbol in symbols[symbol_index:] + symbols[:symbol_index]:
                        try:
                            worker_heartbeat(enabled)
                            if symbol not in api.available_instruments or feed_retry_delay(symbol) > 0:
                                continue
                            sync_specs(api, symbol=symbol)
                            for frame in FRAMES:
                                if stop.is_set():
                                    break
                                sync_page(api, frame, symbol=symbol, live_first=True)
                            observation_tick(symbol=symbol)
                            safe_htf_ast3_tick(symbol=symbol)
                        except ExnessError as exc:
                            logging.warning("Exness sync paused for %s: %s", symbol, exc)
                            if exc.rate_limited or exc.account_blocked:
                                throttle_streak += 1
                                last_throttle = time.monotonic()
                                wait = throttle_delay(exc.retry_after, throttle_streak)
                                retry_until = time.monotonic() + wait
                                record_connection_failure(wait, rate_limited=exc.rate_limited)
                                symbol_index = (symbols.index(symbol) + 1) % len(symbols)
                                throttled = True
                                break
                        except Exception:
                            logging.exception("Exness symbol unavailable: %s", symbol)
                    if throttled:
                        stop.wait(15)
                        continue
                    if time.monotonic() - last_throttle > 600:
                        throttle_streak = 0
                    try:
                        from backend.app.forex_paper import legacy_paper_tick
                        legacy_paper_tick()
                    except Exception:
                        logging.exception("Legacy Paper management unavailable; no new entries")
                    try:
                        execution_tick(api)
                    except Exception as exc:
                        record_error(api.account_id, exc)
                        logging.exception("Exness demo execution tick failed; no duplicate orders")
                        if isinstance(exc, ExnessError):
                            retry_until = time.monotonic() + exc.retry_after
                    if (time.monotonic() - last_throttle > 600
                            and time.monotonic() >= max(retry_until, history_after) and not stop.is_set()):
                        history_after = time.monotonic() + 60
                        histories = [(symbol, frame) for symbol in configured_symbols() for frame in FRAMES]
                        symbol, frame = histories[history_index % len(histories)]
                        history_index += 1
                        try:
                            if symbol in api.available_instruments and not feed_retry_delay(symbol):
                                sync_page(api, frame, symbol=symbol, older=True)
                        except ExnessError as exc:
                            logging.warning("Exness history paused for %s: %s", symbol, exc)
                            if exc.rate_limited or exc.account_blocked:
                                throttle_streak += 1
                                last_throttle = time.monotonic()
                                wait = throttle_delay(exc.retry_after, throttle_streak)
                                retry_until = last_throttle + wait
                                record_connection_failure(wait, rate_limited=exc.rate_limited)
                else:
                    delay = 60
            except Exception as exc:
                logging.warning("Exness feed unavailable; retrying without changing trading state")
                if isinstance(exc, ExnessError) and (exc.rate_limited or exc.account_blocked):
                    throttle_streak += 1
                    last_throttle = time.monotonic()
                    delay = throttle_delay(exc.retry_after, throttle_streak)
                else:
                    delay = exc.retry_after if isinstance(exc, ExnessError) else 60
                retry_until = time.monotonic() + delay
                try:
                    record_connection_failure(delay, rate_limited=getattr(exc, "rate_limited", False))
                except Exception:
                    logging.warning("Cannot persist Exness failure; database unavailable")
                if api:
                    api.client.close()
                    api = None
            stop.wait(min(delay, 15))
    finally:
        if api:
            api.client.close()
        try:
            worker_heartbeat(False)
        except Exception:
            logging.warning("Cannot persist Exness shutdown heartbeat")


if __name__ == "__main__":
    main()
