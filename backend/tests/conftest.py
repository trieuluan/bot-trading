import os
import tempfile
from pathlib import Path

import pytest

# Isolated database: never touches user's development data.
_test_dir = tempfile.TemporaryDirectory()
os.environ["DATABASE_URL"] = "sqlite:///" + str(Path(_test_dir.name) / "test.db")
os.environ["TRADING_MODE"] = "paper"


@pytest.fixture(autouse=True)
def database():
    from alembic import command
    from alembic.config import Config

    from backend.app.db import (
        AutoPaperDecision,
        AutoPaperState,
        BacktestRun,
        BotState,
        Event,
        MarketCandle,
        MarketState,
        MtfObservationEvent,
        MtfObservationState,
        MtfResearchSample,
        PaperCommand,
        PaperFunding,
        PaperTrade,
        PaperWallet,
        Session,
        Signal,
        WorkspaceCandle,
        WorkspaceFeedState,
        ForexObservation,
        ForexPaperTrade,
        ForexPaperAccount,
        ForexInstrumentState,
    )

    command.upgrade(Config("alembic.ini"), "head")
    with Session.begin() as session:
        for model in (
            ForexPaperTrade,
            ForexPaperAccount,
            ForexInstrumentState,
            ForexObservation,
            WorkspaceCandle,
            MtfResearchSample,
            MtfObservationEvent,
            AutoPaperDecision,
            PaperFunding,
            PaperCommand,
            PaperTrade,
            Signal,
            Event,
            MarketCandle,
            BacktestRun,
        ):
            session.query(model).delete()
        observer = session.get(MtfObservationState, 1)
        observer.started_at = observer.last_candle = observer.snapshot = None
        observer.error = observer.retry_at = None
        state = session.get(BotState, 1)
        state.enabled = False
        state.last_bar = 60
        state.heartbeat = None
        market = session.get(MarketState, 1)
        market.source = "synthetic"
        market.last_processed = None
        market.last_sync = None
        market.error = None
        market.retry_at = None

        for feed in session.query(WorkspaceFeedState).all():
            feed.started_at = feed.checked_until = feed.last_sync = feed.worker_heartbeat = None
            feed.error = feed.retry_at = None
            feed.enabled = False

        wallet = session.get(PaperWallet, 1)
        wallet.initial_balance = wallet.balance = 10000
        wallet.fee_bps = 5
        wallet.slippage_bps = 2
        wallet.quote = wallet.contract = None
        wallet.error = wallet.retry_at = wallet.funding_error = None
        wallet.funding_cursor = 0
        wallet.funding_synced_at = 0

        auto = session.get(AutoPaperState, 1)
        auto.enabled = False
        auto.risk_percent = 1
        auto.max_daily_trades = 5
        auto.daily_loss_percent = 3
        auto.last_candle = None
        auto.day = ""
        auto.day_start_equity = 10000
        auto.status = "Auto Paper đang tắt"
