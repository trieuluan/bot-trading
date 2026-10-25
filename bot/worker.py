"""Independent heartbeat, quotes/stops, research and auto-paper loops; no live orders."""

import logging
import signal
import threading
import time

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from backend.app.auto_paper import auto_tick, baseline_auto_on_startup, report_auto_failure
from backend.app.db import BotState, Event, MarketState, Session, Signal, utcnow
from backend.app.feed import sync_market
from backend.app.futures_symbols import SYMBOLS, current_symbol, symbol_scope
from backend.app.futures_wakeup import WAKEUPS, listen
from backend.app.market import DemoAdapter
from backend.app.mexc import MarketDataError
from backend.app.mexc_stream import stream_paper_quotes
from backend.app.observation import observation_tick
from backend.app.paper import sync_paper_funding, sync_paper_quote, sync_paper_stops
from backend.app.settings import validate_mode
from backend.app.strategy import breakout

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
stop = threading.Event()


def tick():
    with Session.begin() as session:
        state = session.scalar(select(BotState).where(BotState.id == 1).with_for_update())
        if state is None:
            raise RuntimeError("Run alembic upgrade head before starting worker")
        state.heartbeat = utcnow()
        market = session.get(MarketState, 1)
        if market.source != "synthetic":
            return
        if not state.enabled:
            return
        if state.last_bar >= 2000:
            state.enabled = False
            session.add(Event(kind="replay", message="Đã hoàn tất dữ liệu replay mẫu"))
            return
        state.last_bar += 1
        candles = DemoAdapter().candles(end=state.last_bar)
        setup = breakout(candles)
        if setup:
            exists = session.scalar(
                select(Signal.id).where(
                    Signal.source == "synthetic", Signal.candle_time == candles[-1].time
                )
            )
            if exists is None:
                session.add(
                    Signal(candle_time=candles[-1].time, symbol="BTC/USDT", **setup.__dict__)
                )
                session.add(
                    Event(
                        kind="signal",
                        message="Phát hiện breakout LONG trên dữ liệu tổng hợp; không gửi lệnh",
                    )
                )


def each_symbol(callback):
    """One failing pair must not starve the others; BTC errors keep their normal handling."""
    def run():
        failed = None
        for symbol in SYMBOLS:
            try:
                with symbol_scope(symbol):
                    callback()
            except Exception as exc:
                logging.exception("%s failed for %s", callback.__name__, symbol)
                failed = failed or exc
        if failed and isinstance(failed, SQLAlchemyError):
            raise failed
    run.__name__ = callback.__name__
    return run

def market_tick():
    try:
        sync_market()
    except MarketDataError:
        logging.exception("Market sync paused; will retry after cooldown")


def loop_delay(name, interval, elapsed, now):
    delay = max(0.1, interval - elapsed)
    if name == "market-history":
        next_sync = now // 900 * 900 + 2 + SYMBOLS.index(current_symbol()) * 0.2
        if next_sync <= now:
            next_sync += 900
        delay = min(delay, next_sync - now)
    return delay

def wait_for_wakeup(wakeup, delay):
    deadline = time.monotonic() + delay
    while not stop.is_set():
        remaining = deadline - time.monotonic()
        if remaining <= 0 or wakeup.wait(min(0.5, remaining)):
            return

def run_loop(name, callback, interval):
    # Independent I/O loops: a slow history/funding request must not delay stops.
    wakeup = WAKEUPS.get((name, current_symbol()))
    while not stop.is_set():
        if wakeup is not None:
            wakeup.clear()
        started = time.monotonic()
        try:
            callback()
        except SQLAlchemyError:
            logging.exception("%s database unavailable; retrying persisted state", name)
        except Exception:
            # Transactions roll back on errors; one loop must not terminate the worker.
            logging.exception("%s failed", name)
            if name == "auto-paper":
                try:
                    report_auto_failure()
                except SQLAlchemyError:
                    logging.exception("Cannot persist auto failure while database is unavailable")
        delay = loop_delay(name, interval, time.monotonic() - started, time.time())
        if wakeup is None:
            stop.wait(delay)
        else:
            wait_for_wakeup(wakeup, delay)

def run_symbol_loop(symbol, name, callback, interval, start_delay=0):
    if stop.wait(start_delay):
        return
    with symbol_scope(symbol):
        run_loop(name, callback, interval)


def main():
    validate_mode()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    # Baseline before starting any auto loop. Fail/retry instead of trading history.
    while not stop.is_set():
        try:
            each_symbol(baseline_auto_on_startup)()
            break
        except SQLAlchemyError:
            logging.exception("Database/migration unavailable; auto startup is waiting")
            stop.wait(5)
    if stop.is_set():
        return
    logging.info("Paper worker ready; live exchange order execution unavailable")
    jobs = [
        ("heartbeat", tick, 5),
        ("mtf-observation", observation_tick, 15),
    ]
    threads = [threading.Thread(target=run_loop, args=job, name=job[0]) for job in jobs]
    threads.append(threading.Thread(target=listen, args=(stop,), name="futures-wakeup"))
    threads.extend(threading.Thread(target=stream_paper_quotes, args=(symbol, stop),
                                    name=f"{symbol}-websocket") for symbol in SYMBOLS)
    pair_jobs = [
        ("historical-stops", sync_paper_stops, 5),
        ("quotes-and-stops", sync_paper_quote, 5),
        ("market-history", market_tick, 15),
        ("funding", sync_paper_funding, 60),
        ("auto-paper", auto_tick, 5),
    ]
    threads.extend(
        threading.Thread(target=run_symbol_loop, args=(symbol, *job, index * 0.4), name=f"{symbol}-{job[0]}")
        for index, symbol in enumerate(SYMBOLS) for job in pair_jobs
    )
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()


if __name__ == "__main__":
    main()
