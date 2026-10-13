"""Independent read-only forex feed process; no MEXC worker or trading imports."""

import logging
import os
import signal
import threading

from backend.app.exness import ExnessError
from backend.app.exness_feed import (
    FRAMES,
    connect_exness,
    record_connection_failure,
    sync_page,
    worker_heartbeat,
)
from backend.app.settings import validate_mode
from backend.app.exness_observation import observation_tick


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
                    if api is None:
                        api = connect_exness()
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
                    except Exception:
                        logging.exception("Exness observation unavailable; no trading state changed")
                else:
                    delay = 60
            except Exception:
                logging.warning("Exness feed unavailable; retrying without changing trading state")
                try:
                    record_connection_failure()
                except Exception:
                    logging.warning("Cannot persist Exness failure; database unavailable")
                delay = 60
                if api:
                    api.client.close()
                    api = None
            stop.wait(delay)
    finally:
        if api:
            api.client.close()
        try:
            worker_heartbeat(False)
        except Exception:
            logging.warning("Cannot persist Exness shutdown heartbeat")


if __name__ == "__main__":
    main()
