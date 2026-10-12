from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select, text

from .db import BotState, Event, MarketState, Session, Signal
from .feed import load_candles
from .market import DemoAdapter, serialize
from .mexc import SOURCE, SYMBOL, TIMEFRAME
from .paper import router as paper_router
from .research import router as research_router
from .settings import validate_mode


@asynccontextmanager
async def lifespan(app):
    validate_mode()
    yield


app = FastAPI(title="Signal Desk API", version="0.2.0", lifespan=lifespan)
app.include_router(research_router)
app.include_router(paper_router)


class Control(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool


@app.get("/api/health")
def health():
    try:
        with Session() as session:
            session.execute(text("SELECT 1"))
            if session.get(BotState, 1) is None:
                raise RuntimeError("Run migrations")
            source = session.get(MarketState, 1).source
    except Exception as exc:
        raise HTTPException(503, "Database is not ready; run migrations") from exc
    return {"status": "ok", "mode": "paper", "data_source": source}


@app.get("/api/dashboard")
def dashboard():
    with Session() as session:
        state = session.get(BotState, 1)
        if state is None:
            raise HTTPException(503, "Run migrations")
        market = session.get(MarketState, 1)
        source = market.source
        real_market = source == SOURCE
        candles = (
            load_candles(session, limit=120)
            if real_market
            else DemoAdapter().candles(end=state.last_bar)
        )
        beat = state.heartbeat
        if beat and beat.tzinfo is None:
            beat = beat.replace(tzinfo=timezone.utc)
        online = beat is not None and (datetime.now(timezone.utc) - beat).total_seconds() < 30
        signals = session.scalars(
            select(Signal).where(Signal.source == source).order_by(Signal.id.desc()).limit(10)
        ).all()
        events = session.scalars(select(Event).order_by(Event.id.desc()).limit(12)).all()
        count = session.scalar(
            select(func.count()).select_from(Signal).where(Signal.source == source)
        )
        return {
            "mode": "paper",
            "data_source": source,
            "symbol": SYMBOL if real_market else "BTC/USDT",
            "timeframe": TIMEFRAME,
            "market": {
                "last_sync": market.last_sync,
                "last_closed_time": candles[-1].time if candles else None,
                "error": market.error,
                "retry_at": market.retry_at,
                "stale": real_market
                and (
                    not market.last_sync
                    or not candles
                    or (datetime.now(timezone.utc).timestamp() - candles[-1].time > 1800)
                    or (
                        datetime.now(timezone.utc) - market.last_sync.replace(tzinfo=timezone.utc)
                    ).total_seconds()
                    > 60
                ),
            },
            "bot": {
                "enabled": state.enabled,
                "online": online,
                "heartbeat": beat,
                "last_bar": state.last_bar,
            },
            "candles": serialize(candles),
            "signal_count": count,
            "signals": [
                {
                    "id": s.id,
                    "source": s.source,
                    "symbol": s.symbol,
                    "timeframe": s.timeframe,
                    "time": s.candle_time,
                    "side": s.side,
                    "entry": s.entry,
                    "stop": s.stop,
                    "target": s.target,
                    "reason": s.reason,
                }
                for s in signals
            ],
            "events": [
                {"id": e.id, "time": e.created_at, "kind": e.kind, "message": e.message}
                for e in events
            ],
        }


@app.post("/api/bot/control")
def control(payload: Control):
    with Session.begin() as session:
        # Serialize commands with the worker's signal transaction.
        state = session.scalar(select(BotState).where(BotState.id == 1).with_for_update())
        if state is None:
            raise HTTPException(503, "Run migrations")
        if state.enabled != payload.enabled:
            state.enabled = payload.enabled
            session.add(
                Event(
                    kind="control",
                    message="Bật theo dõi tín hiệu; không gửi lệnh"
                    if payload.enabled
                    else "Đã dừng tạo tín hiệu mới",
                )
            )
    return {"enabled": payload.enabled, "mode": "paper"}
