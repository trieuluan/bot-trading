"""Run directly against local PostgreSQL; creates/drops its own temporary database.

python -m backend.tests.check_auto_concurrency
Never replaces market data or wallet records in the application's database.
"""

import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import create_engine, func, select, text

from backend.app.db import engine


def verify_in_isolated_database():
    from alembic import command
    from alembic.config import Config

    from backend.app.auto_paper import auto_tick
    from backend.app.db import (
        AutoPaperDecision,
        AutoPaperState,
        MarketState,
        PaperTrade,
        PaperWallet,
        Session,
        Signal,
    )
    from backend.app.feed import save_candles, save_market_signal
    from backend.app.market import Candle
    from backend.app.paper import CloseOrder, close_order
    from backend.app.strategy import breakout

    command.upgrade(Config("alembic.ini"), "head")
    now = int(time.time()) // 900 * 900 + 5
    time.time = lambda: now
    last = now // 900 * 900 - 900
    candles = [Candle(last - (20 - i) * 900, 95, 100, 90, 95, 1) for i in range(20)]
    candles.append(Candle(last, 95, 106, 94, 105, 1))
    with Session.begin() as session:
        save_candles(session, candles)
        wallet = session.get(PaperWallet, 1)
        wallet.quote = dict(
            lastPrice=105, bid1=104.9, ask1=105.1, fairPrice=105, time=now, received_at=now
        )
        wallet.contract = dict(contractSize=0.001, volUnit=1, minVol=1, maxVol=1000000)
        market = session.get(MarketState, 1)
        market.source = "mexc_futures"
        market.last_sync = datetime.fromtimestamp(now, timezone.utc)
        state = session.get(AutoPaperState, 1)
        state.enabled = True
        state.last_candle = last - 900

    def write_signal():
        with Session.begin() as session:
            save_market_signal(session, last, breakout(candles, allow_short=True))

    with ThreadPoolExecutor(max_workers=10) as pool:
        jobs = [pool.submit(auto_tick) for _ in range(8)]
        jobs += [pool.submit(write_signal) for _ in range(2)]
        for job in jobs:
            job.result()
    with Session() as session:
        assert session.scalar(select(func.count()).select_from(PaperTrade)) == 1
        assert session.scalar(select(func.count()).select_from(AutoPaperDecision)) == 1
        assert session.scalar(select(func.count()).select_from(Signal)) == 1
        trade = session.scalar(select(PaperTrade))
        trade_id = trade.id
        wallet = session.get(PaperWallet, 1)
        assert abs(wallet.balance - (10000 - trade.entry_fee)) < 1e-7
    with ThreadPoolExecutor(max_workers=8) as pool:
        jobs = [
            pool.submit(close_order, trade_id, CloseOrder(request_id=uuid4())) for _ in range(8)
        ]
        for job in jobs:
            job.result()
    with Session() as session:
        trade = session.get(PaperTrade, trade_id)
        wallet = session.get(PaperWallet, 1)
        assert abs(wallet.balance - (10000 + trade.net_pnl)) < 1e-7
        assert trade.exit_reason == "manual"
    engine.dispose()
    print(
        "PostgreSQL OK: 8 concurrent auto ticks + 2 signal writers = 1 trade; 8 closes charged once"
    )


def main():
    if "--child" in sys.argv:
        verify_in_isolated_database()
        return
    if engine.dialect.name != "postgresql":
        raise RuntimeError("Run with the local PostgreSQL DATABASE_URL")
    name = "auto_paper_verify_" + uuid4().hex[:12]
    admin = create_engine(engine.url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{name}"'))
    try:
        env = {
            **os.environ,
            "DATABASE_URL": engine.url.set(database=name).render_as_string(hide_password=False),
        }
        subprocess.run(
            [sys.executable, "-m", "backend.tests.check_auto_concurrency", "--child"],
            env=env,
            check=True,
        )
    finally:
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
        admin.dispose()


if __name__ == "__main__":
    main()
