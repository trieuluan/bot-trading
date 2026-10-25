"""Transactional Futures wakeups; polling and persisted state remain authoritative."""

import logging
import threading

import psycopg
from sqlalchemy import text

from .db import engine
from .futures_symbols import SYMBOLS, current_symbol

CHANNEL = "futures_pipeline"
WAKEUPS = {(job, symbol): threading.Event()
           for job in ("futures-learning", "auto-paper") for symbol in SYMBOLS}

def notify(session, job):
    if session.get_bind().dialect.name == "postgresql":
        session.execute(text("SELECT pg_notify(:channel, :payload)"),
                        {"channel": CHANNEL, "payload": f"{job}:{current_symbol()}"})

def dispatch(payload):
    job, _, symbol = payload.partition(":")
    wakeup = WAKEUPS.get((job, symbol))
    if wakeup is not None:
        wakeup.set()

def listen(stop):
    if engine.dialect.name != "postgresql":
        return
    url = engine.url.set(drivername="postgresql").render_as_string(hide_password=False)
    while not stop.is_set():
        try:
            with psycopg.connect(url, autocommit=True, connect_timeout=5) as connection:
                connection.execute(f"LISTEN {CHANNEL}")
                while not stop.is_set():
                    for notification in connection.notifies(timeout=1, stop_after=1):
                        dispatch(notification.payload)
        except psycopg.Error:
            logging.warning("Futures wakeup listener unavailable; polling remains active")
            stop.wait(5)
