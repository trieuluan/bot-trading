"""Loss-sensitive SSE consumers, independent of candle sync and browser tabs."""

import json
import logging
import threading
import time
from urllib.request import urlopen

from sqlalchemy import select

from .db import ExnessEntryIntent, Session
from .forex_entry_intents import cancel_waiting, consume_quote, reconcile_intents


class EntryConsumers:
    def __init__(self, account, stop, on_trigger):
        self.account, self.stop, self.on_trigger = account, stop, on_trigger
        self.threads = {}
        self.ready = threading.Event()
        self.failed = threading.Event()

    def waiting(self, symbol=None):
        with Session() as session:
            query = select(ExnessEntryIntent.symbol).where(ExnessEntryIntent.account == self.account,
                                                         ExnessEntryIntent.status.in_(("waiting", "triggered", "submitting")))
            if symbol:
                query = query.where(ExnessEntryIntent.symbol == symbol)
            return set(session.scalars(query.distinct()).all())

    def run(self):
        try:
            reconcile_intents(self.account, time.time(), restart=True)
            with Session.begin() as session:
                cancel_waiting(session, self.account, "worker_restart", time.time())
            self.ready.set()
            while not self.stop.wait(0.25):
                for symbol in self.waiting():
                    thread = self.threads.get(symbol)
                    if thread is None or not thread.is_alive():
                        thread = threading.Thread(target=self.stream, args=(symbol,), daemon=True, name=f"entry-{symbol}")
                        self.threads[symbol] = thread
                        thread.start()
        except Exception:
            logging.exception("Virtual entry supervisor failed; no new entries")
        finally:
            self.failed.set()
            try:
                with Session.begin() as session:
                    cancel_waiting(session, self.account, "worker_stopped", time.time())
            except Exception:
                logging.exception("Cannot cancel virtual entries; execution requires fresh continuous quotes")

    def cancel(self, symbol, reason):
        with Session.begin() as session:
            cancel_waiting(session, self.account, reason, time.time(), symbol)

    def stream(self, symbol):
        from .app_auth import quote_request
        last = time.time()
        try:
            with urlopen(quote_request(symbol, entry=True), timeout=2) as stream:
                while not self.stop.is_set() and self.waiting(symbol):
                    line = stream.readline()
                    now = time.time()
                    if not line or now - last > 2:
                        self.cancel(symbol, "stream_disconnected" if not line else "quote_gap")
                        return
                    if line.startswith(b"data: "):
                        event = json.loads(line[6:])
                        if event.get("symbol") != symbol or event.get("status") != "live":
                            self.cancel(symbol, "stream_disconnected")
                            return
                        with Session.begin() as session:
                            triggered = consume_quote(session, self.account, symbol, event.get("quote"), now)
                        last = now
                        for identifier in triggered:
                            threading.Thread(target=self.trigger, args=(identifier, symbol), daemon=True,
                                             name=f"submit-{identifier}").start()
        except Exception:
            logging.exception("Virtual entry stream lost: %s", symbol)
            self.cancel(symbol, "stream_disconnected")

    def trigger(self, identifier, symbol):
        try:
            self.on_trigger(identifier, symbol)
        except Exception:
            logging.exception("Virtual entry submission failed; reconcile required: %s", identifier)
