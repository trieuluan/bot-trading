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
from backend.app.market import DemoAdapter
from backend.app.mexc import MarketDataError
from backend.app.observation import observation_tick
from backend.app.paper import sync_paper_funding, sync_paper_quote
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


def market_tick():
    try:
        sync_market()
    except MarketDataError:
        logging.exception("Market sync paused; will retry after cooldown")


def run_loop(name, callback, interval):
    # Independent I/O loops: a slow history/funding request must not delay stops.
    while not stop.is_set():
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
        stop.wait(max(0.1, interval - (time.monotonic() - started)))


def main():
    validate_mode()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    # Baseline before starting any auto loop. Fail/retry instead of trading history.
    while not stop.is_set():
        try:
            baseline_auto_on_startup()
            break
        except SQLAlchemyError:
            logging.exception("Database/migration unavailable; auto startup is waiting")
            stop.wait(5)
    if stop.is_set():
        return
    logging.info("Paper worker ready; live exchange order execution unavailable")
    jobs = [
        ("heartbeat", tick, 5),
        ("quotes-and-stops", sync_paper_quote, 5),
        ("market-history", market_tick, 15),
        ("mtf-observation", observation_tick, 15),
        ("funding", sync_paper_funding, 60),
        ("auto-paper", auto_tick, 5),
    ]
    threads = [threading.Thread(target=run_loop, args=job, name=job[0]) for job in jobs]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()


if __name__ == "__main__":
    main()
