from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select, text

from .auto_paper import router as auto_router
from .db import (
    AutoPaperState,
    BotState,
    Event,
    MarketCandle,
    MarketState,
    Session,
    Signal,
    Workspace,
    WorkspaceCandle,
)
from .feed import load_candles
from .market import DemoAdapter, serialize
from .mexc import SOURCE, SYMBOL, TIMEFRAME
from .observation import router as observation_router
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
app.include_router(auto_router)
app.include_router(observation_router)

@app.middleware("http")
async def legacy_workspace_guard(request, call_next):
    if request.url.path.startswith("/api/") and not request.url.path.startswith((
        "/api/workspaces", "/api/health",
    )):
        scopes = request.query_params.getlist("workspace_id")
        if request.headers.get("X-Workspace-ID"):
            scopes.append(request.headers["X-Workspace-ID"])
        if any(scope != SOURCE for scope in scopes):
            return JSONResponse(status_code=409, content={
                "detail": "Legacy endpoints belong to MEXC; use workspace-scoped endpoints",
            })
    return await call_next(request)


class Control(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool

def workspace_values(workspace):
    return {"id": workspace.id, "name": workspace.name, "venue": workspace.venue,
            "symbol": workspace.symbol, "execution_enabled": False}

@app.get("/api/workspaces")
def workspaces():
    with Session() as session:
        rows = session.scalars(select(Workspace).order_by(Workspace.id)).all()
        return {"workspaces": [workspace_values(row) for row in rows]}

@app.get("/api/workspaces/{workspace_id}/candles")
def workspace_candles(
    workspace_id: str,
    timeframe: Literal["1m", "15m", "1h", "4h"] = "15m",
    price_type: Literal["last", "bid", "ask"] | None = None,
    limit: int = Query(120, ge=1, le=2000),
):
    with Session() as session:
        workspace = session.get(Workspace, workspace_id)
        if workspace is None:
            raise HTTPException(404, "Unknown workspace")
        price_type = price_type or ("last" if workspace.venue == SOURCE else "bid")
        if workspace.venue == SOURCE:
            if price_type != "last":
                raise HTTPException(422, "MEXC candle history uses last price, not bid/ask")
            query = select(MarketCandle).where(MarketCandle.source == SOURCE)
            model = MarketCandle
        else:
            if price_type == "last":
                raise HTTPException(422, "Exness requires explicit bid or ask candles")
            query = select(WorkspaceCandle).where(
                WorkspaceCandle.workspace_id == workspace_id,
                WorkspaceCandle.price_type == price_type,
            )
            model = WorkspaceCandle
        rows = session.scalars(query.where(
            model.symbol == workspace.symbol, model.timeframe == timeframe,
        ).order_by(model.time.desc()).limit(limit)).all()
        return {"workspace": workspace_values(workspace), "timeframe": timeframe,
                "price_type": price_type, "candles": [
                    {field: getattr(row, field) for field in (
                        ("time", "open", "high", "low", "close", "volume")
                        if workspace.venue == SOURCE else ("time", "open", "high", "low", "close")
                    )} for row in reversed(rows)
                ]}

@app.get("/api/workspaces/{workspace_id}/dashboard")
def workspace_dashboard(workspace_id: str):
    with Session() as session:
        workspace = session.get(Workspace, workspace_id)
        if workspace is None:
            raise HTTPException(404, "Unknown workspace")
        identity = workspace_values(workspace)
    if workspace_id == SOURCE:
        return {"workspace": identity, **dashboard()}
    return {
        "workspace": identity,
        "mode": "demo_read_only",
        "market": {"status": "not_started", "message": "Chưa bật đồng bộ nến Exness"},
        "observation": {"status": "not_started", "message": "Chưa chạy MTF Exness"},
        "paper": {"status": "unavailable", "message": "Ví MEXC không dùng cho Exness"},
        "execution_enabled": False,
    }


@app.get("/api/health")
def health():
    try:
        with Session() as session:
            session.execute(text("SELECT 1"))
            if session.get(BotState, 1) is None or session.get(AutoPaperState, 1) is None:
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
