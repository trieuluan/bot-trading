import asyncio
import hashlib
import hmac
import os
import threading
import time
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timezone
from typing import Literal

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select, text

from .alerts import watchdog
from .app_auth import AUTH_TTL, COOKIE, auth_token
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
from .exness_execution import enabled as demo_execution_enabled
from .exness_execution import router as execution_router
from .exness_feed import feed_summary
from .exness_observation import observation_summary
from .exness_quotes import quotes
from .exness_quotes import router as quotes_router
from .feed import load_candles, save_candles
from .forex_learning import router as learning_router
from .forex_reporting import journal_csv
from .forex_stream import router as forex_stream_router
from .forex_symbols import configured_symbols, request_symbol
from .futures_history import router as futures_history_router
from .futures_learner import router as futures_learning_router
from .futures_stream import board_snapshot
from .futures_stream import invalidate as invalidate_stream
from .futures_stream import router as stream_router
from .futures_symbols import SYMBOLS, market_symbol, symbol_scope
from .market import DemoAdapter, serialize
from .mexc import SOURCE, SYMBOL, TIMEFRAME, MarketDataError, MexcFuturesAdapter
from .notifications import notifications_loop
from .observation import router as observation_router
from .paper import router as paper_router
from .research import check_cooldown, market_failure, research_lock
from .research import router as research_router
from .settings import validate_mode


@asynccontextmanager
async def lifespan(app):
    validate_mode()
    quotes.start()
    alert_task = asyncio.create_task(watchdog())
    notification_task = asyncio.create_task(notifications_loop())
    try:
        yield
    finally:
        alert_task.cancel()
        notification_task.cancel()
        with suppress(asyncio.CancelledError):
            await alert_task
        with suppress(asyncio.CancelledError):
            await notification_task
        await quotes.stop()


app = FastAPI(title="Signal Desk API", version="0.2.0", lifespan=lifespan)
app.include_router(research_router)
app.include_router(paper_router)
app.include_router(execution_router)
app.include_router(learning_router)
app.include_router(futures_learning_router)
app.include_router(futures_history_router)
app.include_router(auto_router)
app.include_router(observation_router)
app.include_router(quotes_router)
app.include_router(stream_router)
app.include_router(forex_stream_router)

def authenticated(request):
    password = os.getenv("APP_PASSWORD", "")
    if not password:
        return True
    token = request.cookies.get(COOKIE, "")
    if len(token) > 128:
        return False
    try:
        expires, nonce, signature = token.split(".")
        payload = f"{expires}.{nonce}"
        expected = hmac.new(password.encode(), payload.encode(), hashlib.sha256).hexdigest()
        return int(time.time()) < int(expires) and hmac.compare_digest(
            signature.encode(), expected.encode())
    except ValueError:
        return False

class Login(BaseModel):
    model_config = ConfigDict(extra="forbid")
    password: str = Field(min_length=1, max_length=256)

@app.get("/api/auth/status")
def auth_status(request: Request):
    return {"required": bool(os.getenv("APP_PASSWORD")), "authenticated": authenticated(request)}

@app.post("/api/auth/login")
async def auth_login(payload: Login, request: Request):
    expected = os.getenv("APP_PASSWORD", "")
    if not expected or not hmac.compare_digest(payload.password.encode(), expected.encode()):
        await asyncio.sleep(1)
        return JSONResponse(status_code=401, content={"detail": "Sai mật khẩu"})
    response = JSONResponse({"ok": True})
    response.set_cookie(COOKIE, auth_token(), httponly=True, samesite="strict", max_age=AUTH_TTL,
                        secure=request.headers.get("x-forwarded-proto", request.url.scheme) == "https")
    return response

@app.post("/api/auth/logout")
def auth_logout():
    response = JSONResponse({"ok": True})
    response.delete_cookie(COOKIE)
    return response

@app.get("/api/workspaces/exness_forex/paper/trades.csv")
def forex_journal(channel: Literal["live", "replay", "trend", "range", "router"] = "live", symbol: str = "XAUUSD"):
    symbol = request_symbol(symbol)
    return StreamingResponse(journal_csv(channel, symbol), media_type="text/csv", headers={
        "Content-Disposition": f'attachment; filename="exness-paper-{symbol}-{channel}.csv"',
        "Cache-Control": "no-store",
    })

@app.middleware("http")
async def futures_symbol_scope(request, call_next):
    if request.url.path.startswith("/api/workspaces/exness_forex"):
        return await call_next(request)
    symbol = request.query_params.get("symbol") or request.headers.get("X-Futures-Symbol") or SYMBOLS[0]
    if symbol not in SYMBOLS:
        return JSONResponse(status_code=422, content={"detail": "Unsupported Futures symbol"})
    with symbol_scope(symbol):
        response = await call_next(request)
    if request.method != "GET":
        invalidate_stream()
    return response

@app.get("/api/workspaces/mexc_futures/symbols")
def futures_symbols():
    return {"symbols": list(SYMBOLS)}


@app.get("/api/workspaces/mexc_futures/board")
def futures_board():
    """Ví, Auto Paper và learner của mọi cặp trong một request (cache dùng chung với SSE)."""
    return {"at": int(time.time()), "symbols": board_snapshot()}

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


@app.middleware("http")
async def password_gate(request, call_next):
    path = request.url.path
    if path not in ("/api/health", "/api/auth/status", "/api/auth/login", "/api/auth/logout") \
            and not authenticated(request):
        return JSONResponse(status_code=401, content={"detail": "Cần đăng nhập"})
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    return response

class Control(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool

def workspace_values(workspace):
    return {"id": workspace.id, "name": workspace.name, "venue": workspace.venue,
            "symbol": workspace.symbol, "symbols": list(configured_symbols()) if workspace.id == "exness_forex" else [workspace.symbol], "execution_enabled": workspace.id == "exness_forex" and demo_execution_enabled()}

@app.get("/api/workspaces")
def workspaces():
    with Session() as session:
        rows = session.scalars(select(Workspace).order_by(Workspace.id)).all()
        return {"workspaces": [workspace_values(row) for row in rows]}

BACKFILL_BARS = 1000

def backfill_older_candles(before, limit):
    """Download older closed M15 candles without advancing any trading cursor."""
    if not research_lock.acquire(blocking=False):
        raise HTTPException(409, "Market download already running; retry when finished")
    adapter = None
    try:
        where = (MarketCandle.source == SOURCE, MarketCandle.symbol == market_symbol(),
                 MarketCandle.timeframe == TIMEFRAME)
        with Session() as session:
            oldest = session.scalar(select(func.min(MarketCandle.time)).where(*where))
            older = session.scalar(select(func.count()).where(
                *where, *([MarketCandle.time < before] if before else []))) if oldest else 0
        if oldest is None or older > limit:
            return
        check_cooldown()
        adapter = MexcFuturesAdapter()
        now = adapter.server_time()
        end = min((before + 899) // 900 * 900, oldest, now // 900 * 900)
        start = max(0, end - max(BACKFILL_BARS, limit + 1) * 900)
        if end <= start:
            return
        candles = adapter.history(start, end, now)
        with Session.begin() as session:
            save_candles(session, candles)
    except MarketDataError as exc:
        raise market_failure(exc) from exc
    finally:
        if adapter:
            adapter.close()
        research_lock.release()

@app.get("/api/workspaces/{workspace_id}/candles")
def workspace_candles(
    workspace_id: str,
    timeframe: Literal["1m", "15m", "1h", "4h"] = "15m",
    price_type: Literal["last", "bid", "ask"] | None = None,
    limit: int = Query(1000, ge=1, le=5000),
    before: int | None = Query(None, ge=1),
    symbol: str | None = Query(None),
):
    with Session() as session:
        workspace = session.get(Workspace, workspace_id)
        if workspace is None:
            raise HTTPException(404, "Unknown workspace")
        mexc = workspace.venue == SOURCE
        if mexc and price_type not in (None, "last"):
            raise HTTPException(422, "MEXC candle history uses last price, not bid/ask")
    if mexc and timeframe == TIMEFRAME and before is not None:
        backfill_older_candles(before, limit)
    with Session() as session:
        workspace = session.get(Workspace, workspace_id)
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
        if before is not None:
            query = query.where(model.time < before)
        rows = session.scalars(query.where(
            model.symbol == (market_symbol() if workspace.venue == SOURCE else (request_symbol(symbol) if symbol else workspace.symbol)), model.timeframe == timeframe,
        ).order_by(model.time.desc()).limit(limit + 1)).all()
        has_more = len(rows) > limit
        rows = rows[:limit]
        if mexc and timeframe == TIMEFRAME and rows and rows[-1].time > 0:
            has_more = True
        return {"workspace": {**workspace_values(workspace), **({"symbol": symbol or workspace.symbol} if not mexc else {})}, "timeframe": timeframe,
                "has_more": has_more, "next_before": rows[-1].time if has_more else None,
                "price_type": price_type, "candles": [
                    {field: getattr(row, field) for field in (
                        ("time", "open", "high", "low", "close", "volume")
                        if workspace.venue == SOURCE else ("time", "open", "high", "low", "close", "tick_volume")
                    )} for row in reversed(rows)
                ]}

_dashboard_cache = {}
_dashboard_lock = threading.Lock()

@app.get("/api/workspaces/{workspace_id}/dashboard")
def workspace_dashboard(workspace_id: str, symbol: str = "XAUUSD"):
    """Forex: tính một lần mỗi 5s cho mọi client của cặp (đổi cặp liên tục không quét lại nến)."""
    if workspace_id != "exness_forex":
        return _workspace_dashboard(workspace_id, symbol)
    request_symbol(symbol)
    with _dashboard_lock:
        hit = _dashboard_cache.get(symbol)
        if hit and time.monotonic() - hit[0] < 5:
            return hit[1]
        value = _workspace_dashboard(workspace_id, symbol)
        _dashboard_cache[symbol] = (time.monotonic(), value)
        return value

def _workspace_dashboard(workspace_id: str, symbol: str = "XAUUSD"):
    if workspace_id == "exness_forex":
        request_symbol(symbol)
    with Session() as session:
        workspace = session.get(Workspace, workspace_id)
        if workspace is None:
            raise HTTPException(404, "Unknown workspace")
        identity = workspace_values(workspace)
        if workspace_id == "exness_forex":
            identity = {**identity, "symbol": symbol}
    if workspace_id == SOURCE:
        return {"workspace": identity, **dashboard()}
    from .forex_conversion import cost_context
    from .forex_costs import spec_summary
    from .forex_market_sessions import market_session
    now = int(time.time())
    with Session() as session:
        instrument = spec_summary(session, now, symbol=symbol)
        session_status = market_session(instrument, now, symbol, os.getenv("EXNESS_ACCOUNT_ID") or None)
        try:
            if instrument["status"] == "stale":
                raise ValueError("stale_specs")
            instrument = {**instrument, "profile": cost_context(instrument["profile"], int(time.time()), session,
                            account=os.getenv("EXNESS_ACCOUNT_ID", "unconfigured"), symbol=symbol)}
        except ValueError:
            instrument = {**instrument, "profile": None}
    frames = feed_summary(symbol=symbol if workspace_id != SOURCE else "XAUUSD")
    m15 = next((item for item in frames if item["timeframe"] == "15m"), None)
    bars = m15["bars_per_side"] if m15 else 0
    last_sync = m15["last_sync"] if m15 else None
    worker_online = bool(m15 and m15["worker_online"])
    pending = any(item["remaining_intervals"] is None or item["remaining_intervals"] > 0
                  for item in frames if item["timeframe"] != "1m")
    status = ("backfill" if pending else "synced") if bars else "waiting"
    if any(item["error"] for item in frames):
        status = "error"
    return {
        "workspace": identity,
        "instrument": instrument,
        "market_session": session_status,
        "mode": "demo_execution" if demo_execution_enabled() else "demo_read_only",
        "market": {
            "status": status,
            "message": f"Đã lưu {bars:,} nến M15 mỗi phía bid/ask {symbol}" if bars else f"Chưa có nến {symbol} trong kho",
            "bars": bars, "last_sync": last_sync, "worker_online": worker_online,
        },
        "observation": observation_summary(symbol=symbol),
        "execution_enabled": demo_execution_enabled(),
        "frames": frames,
    }

@app.get("/api/workspaces/exness_forex/markets")
def forex_markets():
    from .db import ExnessEntryIntent, ExnessExecutionState, ExnessExecutionTrade
    from .exness_execution import max_positions, max_positions_per_symbol
    from .forex_conversion import cost_context
    from .forex_costs import spec_summary
    from .forex_entry_intents import RESERVED
    from .forex_learning import execution_status
    from .forex_market_sessions import market_session
    from .forex_position_groups import open_setup_count, ticket_owners

    now = int(time.time())
    account = os.getenv("EXNESS_ACCOUNT_ID", "").strip()
    with Session() as session:
        state = session.get(ExnessExecutionState, account)
        meta = state.snapshot if state else {}
        broker = meta.get("broker", {})
        positions = broker.get("positions", [])
        trades = session.scalars(select(ExnessExecutionTrade).where(ExnessExecutionTrade.account == account)).all()
        owners = ticket_owners(trades)
        intents_by_symbol = {}
        for intent in session.scalars(select(ExnessEntryIntent).where(
            ExnessEntryIntent.account == account, ExnessEntryIntent.available_at <= now,
            (ExnessEntryIntent.status.in_(RESERVED)) | (ExnessEntryIntent.available_at >= now - 21600),
        ).order_by(ExnessEntryIntent.available_at.desc(), ExnessEntryIntent.id)):
            rows = intents_by_symbol.setdefault(intent.symbol, [])
            if len(rows) < 12 or intent.status in RESERVED:
                rows.append({"id": intent.id, "strategy": intent.strategy, "side": intent.side,
                             "status": intent.status, "available_at": intent.available_at,
                             "expires_at": intent.expires_at, "limit_price": intent.limit_price,
                             "stop_price": intent.stop_price,
                             **{key: intent.snapshot.get(key) for key in
                                ("triggered_at", "ended_at", "reason", "winner", "broker_fills")}})
        markets = []
        for symbol in configured_symbols():
            specs = spec_summary(session, now, symbol)
            frames = feed_summary(symbol)
            stream = quotes.snapshot(symbol)
            last = session.scalar(select(WorkspaceCandle).where(
                WorkspaceCandle.workspace_id == "exness_forex", WorkspaceCandle.symbol == symbol,
                WorkspaceCandle.timeframe == "15m", WorkspaceCandle.price_type == "bid",
                WorkspaceCandle.time + 900 <= now,
            ).order_by(WorkspaceCandle.time.desc()).limit(1))
            profile = specs.get("profile")
            reason = None
            try:
                if not profile or specs["status"] == "stale":
                    raise ValueError("specs_unavailable_or_stale")
                verified = cost_context(profile, now, session, account=account, symbol=symbol)
                if verified.get("commission_per_unit") is None:
                    raise ValueError("commission_unverified")
            except ValueError as exc:
                reason = str(exc)
            execution = execution_status(account, now, symbol)
            execution.pop("entry_allowed_ids", None)
            count = sum(position.get("instrument") == symbol for position in positions)
            setups = open_setup_count(account, [p for p in positions if p.get("instrument") == symbol], owners)
            if setups >= max_positions_per_symbol():
                reason = "symbol_position_limit"
            if open_setup_count(account, positions, owners) >= max_positions():
                reason = "account_position_limit"
            tick = stream.get("quote")
            stamp = tick["time"] if tick else last.time + 900 if last else None
            markets.append({"symbol": symbol, "feed": frames, "specs": specs["status"],
                            "market_session": market_session(specs, now, symbol, account or None),
                            "price": tick["bid"] if tick else last.close if last else None,
                            "price_at": stamp, "price_source": "tick" if tick else "closed_m15",
                            "age_seconds": max(0, now - stamp) if stamp is not None else None,
                            "positions": count, "setup_positions": setups, "execution": execution,
                            "entry_intents": intents_by_symbol.get(symbol, []),
                            "blocked_reason": execution["blocked_reason"] or reason,
                            "decision": meta.get("symbols", {}).get(symbol)})
    return {"at": now, "markets": markets, "account_positions": len(positions),
            "account_setups": open_setup_count(account, positions, owners),
            "max_positions": max_positions(), "max_positions_per_symbol": max_positions_per_symbol()}


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
