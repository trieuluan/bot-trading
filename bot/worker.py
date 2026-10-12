"""Single-worker synthetic replay; generates signals, never sends orders."""

import logging
import signal
import threading

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from backend.app.db import BotState, Event, Session, Signal, utcnow
from backend.app.market import DemoAdapter
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
            exists = session.scalar(select(Signal.id).where(Signal.candle_time == candles[-1].time))
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


def main():
    validate_mode()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    logging.info("Paper signal worker ready; live order execution unavailable")
    while not stop.is_set():
        try:
            tick()
        except SQLAlchemyError:
            logging.exception("Database unavailable; retrying without advancing replay")
        stop.wait(5)


if __name__ == "__main__":
    main()
