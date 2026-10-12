from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select, text

from .db import BotState, Event, Session, Signal
from .market import DemoAdapter, serialize
from .settings import validate_mode


@asynccontextmanager
async def lifespan(app):
    validate_mode()
    yield


app = FastAPI(title="Signal Desk API", version="0.1.0", lifespan=lifespan)


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
    except Exception as exc:
        raise HTTPException(503, "Database is not ready; run migrations") from exc
    return {"status": "ok", "mode": "paper", "data_source": "synthetic"}


@app.get("/api/dashboard")
def dashboard():
    with Session() as session:
        state = session.get(BotState, 1)
        if state is None:
            raise HTTPException(503, "Run migrations")
        beat = state.heartbeat
        if beat and beat.tzinfo is None:
            beat = beat.replace(tzinfo=timezone.utc)
        online = beat is not None and (datetime.now(timezone.utc) - beat).total_seconds() < 30
        signals = session.scalars(select(Signal).order_by(Signal.id.desc()).limit(10)).all()
        events = session.scalars(select(Event).order_by(Event.id.desc()).limit(12)).all()
        count = session.scalar(select(func.count()).select_from(Signal))
        return {
            "mode": "paper",
            "data_source": "synthetic",
            "symbol": "BTC/USDT",
            "timeframe": "15m",
            "bot": {
                "enabled": state.enabled,
                "online": online,
                "heartbeat": beat,
                "last_bar": state.last_bar,
            },
            "candles": serialize(DemoAdapter().candles(end=state.last_bar)),
            "signal_count": count,
            "signals": [
                {
                    "id": s.id,
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
                    message="Bật phân tích dữ liệu mô phỏng"
                    if payload.enabled
                    else "Đã dừng tạo tín hiệu mới",
                )
            )
    return {"enabled": payload.enabled, "mode": "paper"}
