"""Market selection and reproducible backtest endpoints."""

import threading
from dataclasses import asdict
from datetime import timedelta
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from .backtest import BacktestConfig, run_backtest
from .db import (
    AutoPaperState,
    BacktestRun,
    BotState,
    Event,
    MarketState,
    PaperWallet,
    Session,
    utcnow,
)
from .futures_symbols import current_symbol, market_symbol, state_id
from .feed import save_candles
from .mexc import SOURCE, MarketDataError, MexcFuturesAdapter

router = APIRouter(prefix="/api")
# One download/research request at a time per local API process.
research_lock = threading.Lock()


class MarketSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: Literal["synthetic", "mexc_futures"]


class ResearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    days: int = Field(default=30, ge=2, le=90)
    initial_capital: float = Field(default=10_000, gt=0, le=100_000_000)
    risk_percent: float = Field(default=1, gt=0, le=5)
    fee_bps: float = Field(default=8, ge=0, le=100)
    slippage_bps: float = Field(default=2, ge=0, le=100)


def check_cooldown():
    with Session() as session:
        state = session.get(MarketState, state_id())
        if state.retry_at:
            remaining = (state.retry_at.replace(tzinfo=utcnow().tzinfo) - utcnow()).total_seconds()
            if remaining > 0:
                raise HTTPException(
                    429, state.error or "MEXC cooldown", {"Retry-After": str(int(remaining) + 1)}
                )


def market_failure(exc: MarketDataError):
    with Session.begin() as session:
        state = session.scalar(select(MarketState).where(MarketState.id == state_id()).with_for_update())
        state.error = str(exc)
        state.retry_at = utcnow() + timedelta(seconds=exc.retry_after)
    return HTTPException(502, str(exc))


@router.post("/market/config")
def configure_market(payload: MarketSelection):
    if not research_lock.acquire(blocking=False):
        raise HTTPException(409, "Market download already running; retry when finished")
    adapter = None
    try:
        candles = []
        if payload.source == SOURCE:
            check_cooldown()
            adapter = MexcFuturesAdapter()
            now = adapter.server_time()
            end = now // 900 * 900
            candles = adapter.history(end - 120 * 900, end, now)
        with Session.begin() as session:
            bot = session.scalar(select(BotState).where(BotState.id == 1).with_for_update())
            market = session.scalar(
                select(MarketState).where(MarketState.id == state_id()).with_for_update()
            )
            session.scalar(select(PaperWallet).where(PaperWallet.id == state_id()).with_for_update())
            auto = session.get(AutoPaperState, state_id())
            if auto and auto.enabled and payload.source != SOURCE:
                raise HTTPException(409, "Dừng Auto Paper trước khi chuyển sang dữ liệu tổng hợp")
            changed = market.source != payload.source
            if changed:
                bot.enabled = False
            market.source = payload.source
            if candles:
                save_candles(session, candles)
                if changed or market.last_processed is None:
                    market.last_processed = candles[-1].time
                market.last_sync = utcnow()
            market.error = None
            market.retry_at = None
            session.add(
                Event(kind="market", message=f"Nguồn dữ liệu: {payload.source}; không gửi lệnh")
            )
        return {"source": payload.source, "closed_candles": len(candles)}
    except MarketDataError as exc:
        raise market_failure(exc) from exc
    finally:
        if adapter:
            adapter.close()
        research_lock.release()


def serialize_run(run):
    return {"id": run.id, "created_at": run.created_at, "source": run.source, "result": run.result}


@router.get("/backtests/latest")
def latest_backtest():
    with Session() as session:
        run = session.scalar(select(BacktestRun).order_by(BacktestRun.id.desc()).limit(1))
        return serialize_run(run) if run else None


@router.get("/backtests/{run_id}")
def get_backtest(run_id: int):
    with Session() as session:
        run = session.get(BacktestRun, run_id)
        if run is None:
            raise HTTPException(404, "Backtest not found")
        return serialize_run(run)


@router.post("/backtests")
def create_backtest(payload: ResearchRequest):
    if not research_lock.acquire(blocking=False):
        raise HTTPException(409, "Market download already running; retry when finished")
    adapter = None
    try:
        check_cooldown()
        adapter = MexcFuturesAdapter()
        now = adapter.server_time()
        end = now // 900 * 900
        candles = adapter.history(end - payload.days * 86400, end, now)
        config = BacktestConfig(**payload.model_dump(exclude={"days"}))
        fair_candles = adapter.history(candles[0].time, end, now, fair=True)
        funding = adapter.funding_history(candles[0].time, end)
        contract = adapter.contract_info()
        result = run_backtest(candles, config, funding, fair_candles, contract)
        result.update(
            symbol=market_symbol(),
            timeframe="15m",
            days=payload.days,
            dataset_start=candles[0].time,
            dataset_end=end,
            # Freeze input bars: a later exchange correction cannot change this report.
            candles=[asdict(candle) for candle in candles],
            fair_candles=[asdict(candle) for candle in fair_candles],
            funding=funding,
            contract=contract,
        )
        with Session.begin() as session:
            session.scalar(select(MarketState).where(MarketState.id == state_id()).with_for_update())
            save_candles(session, candles)
            run = BacktestRun(source=SOURCE, result=result)
            session.add(run)
            session.add(
                Event(kind="backtest", message=f"Backtest MEXC Futures {payload.days} ngày")
            )
            session.flush()
            return serialize_run(run)
    except MarketDataError as exc:
        raise market_failure(exc) from exc
    finally:
        if adapter:
            adapter.close()
        research_lock.release()
