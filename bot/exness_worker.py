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
from backend.app.exness_gaps import recheck_gap
from backend.app.exness_observation import observation_tick
from backend.app.forex_costs import sync_specs
from backend.app.forex_learner import forward_tick, learning_tick
from backend.app.forex_paper import paper_tick
from backend.app.forex_scalping import scalp_tick
from backend.app.settings import validate_mode


def m1_sync_allowed(now):
    return 330 <= now % 900 < 840


def main():
    validate_mode()
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    api = None
    try:
        while not stop.is_set():
            enabled = os.getenv("EXNESS_DATA_ENABLED", "false").lower() == "true"
            delay = 15
            try:
                worker_heartbeat(enabled)
                if enabled:
                    cooldown = feed_retry_delay()
                    if cooldown > 0:
                        stop.wait(min(cooldown, 15))
                        continue
                    if api is None:
                        api = connect_exness()
                    sync_specs(api)
                    for frame in FRAMES:
                        if stop.is_set():
                            break
                        try:
                            sync_page(api, frame)
                        except ExnessError as exc:
                            logging.warning("Exness candle sync paused for %s; persisted retry", frame)
                            delay = exc.retry_after
                            break
                    try:
                        observation_tick()
                        paper_tick()
                    except Exception:
                        logging.exception("Exness observation/paper unavailable; no broker orders sent")
                    try:
                        forward_tick()
                    except Exception:
                        logging.exception("Forward shadow unavailable; persisted samples unchanged")
                    try:
                        execution_tick(api)
                    except Exception as exc:
                        record_error(api.account_id, exc)
                        logging.exception("Exness demo execution tick failed; no duplicate orders")
                        if isinstance(exc, ExnessError):
                            stop.wait(exc.retry_after)
                    try:
                        learning_tick()
                    except Exception:
                        logging.exception("Exness learning paused; executor unchanged")
                    try:
                        if m1_sync_allowed(time.time()) and api.rate_limit_until <= time.monotonic():
                            recheck_gap(api)
                            synced = sync_page(api, "1m", days=7)
                            if synced["status"] == "current" and m1_sync_allowed(time.time()):
                                sync_page(api, "1m", days=30, older=True)
                    except ExnessError as exc:
                        logging.warning("Exness M1 sync paused; persisted retry")
                        delay = max(delay, exc.retry_after)
                    try:
                        scalp_tick()
                    except Exception:
                        logging.exception("Scalping shadow unavailable; persisted samples unchanged")
                else:
                    delay = 60
            except Exception as exc:
                logging.warning("Exness feed unavailable; retrying without changing trading state")
                delay = exc.retry_after if isinstance(exc, ExnessError) else 60
                try:
                    record_connection_failure(delay)
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
