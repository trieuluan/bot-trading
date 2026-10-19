import asyncio
from contextlib import suppress
import json
import math
import tempfile
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select, text

from .alerts import watchdog
from .notifications import notifications_loop
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
from .forex_learner import router as learning_router
from .forex_paper import forex_paper_summary
from .forex_reporting import journal_csv
from .forex_strategies import STRATEGIES
from .futures_symbols import SYMBOLS, market_symbol, symbol_scope
from .futures_learner import router as futures_learning_router
from .futures_history import router as futures_history_router
from .market import DemoAdapter, serialize
from .mexc import SOURCE, SYMBOL, TIMEFRAME, MarketDataError, MexcFuturesAdapter
from .observation import router as observation_router
from .paper import router as paper_router
from .research import check_cooldown, market_failure, research_lock, router as research_router
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

@app.get("/api/workspaces/exness_forex/paper/trades.csv")
def forex_journal(channel: Literal["live", "replay", "trend", "range", "router"] = "live"):
    return StreamingResponse(journal_csv(channel), media_type="text/csv", headers={
        "Content-Disposition": f'attachment; filename="exness-paper-{channel}.csv"',
        "Cache-Control": "no-store",
    })

@app.middleware("http")
async def futures_symbol_scope(request, call_next):
    symbol = request.query_params.get("symbol") or request.headers.get("X-Futures-Symbol") or SYMBOLS[0]
    if symbol not in SYMBOLS:
        return JSONResponse(status_code=422, content={"detail": "Unsupported Futures symbol"})
    with symbol_scope(symbol):
        return await call_next(request)

@app.get("/api/workspaces/mexc_futures/symbols")
def futures_symbols():
    return {"symbols": list(SYMBOLS)}

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
            "symbol": workspace.symbol, "execution_enabled": workspace.id == "exness_forex" and demo_execution_enabled()}

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
            model.symbol == (market_symbol() if workspace.venue == SOURCE else workspace.symbol), model.timeframe == timeframe,
        ).order_by(model.time.desc()).limit(limit + 1)).all()
        has_more = len(rows) > limit
        rows = rows[:limit]
        if mexc and timeframe == TIMEFRAME and rows and rows[-1].time > 0:
            has_more = True
        return {"workspace": workspace_values(workspace), "timeframe": timeframe,
                "has_more": has_more, "next_before": rows[-1].time if has_more else None,
                "price_type": price_type, "candles": [
                    {field: getattr(row, field) for field in (
                        ("time", "open", "high", "low", "close", "volume")
                        if workspace.venue == SOURCE else ("time", "open", "high", "low", "close", "tick_volume")
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
    frames = feed_summary()
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
        "mode": "demo_execution" if demo_execution_enabled() else "demo_read_only",
        "market": {
            "status": status,
            "message": f"Đã lưu {bars:,} nến M15 mỗi phía bid/ask XAUUSD" if bars else "Chưa có nến XAUUSD trong kho",
            "bars": bars, "last_sync": last_sync, "worker_online": worker_online,
        },
        "observation": observation_summary(),
        "paper": forex_paper_summary(),
        "execution_enabled": demo_execution_enabled(),
        "frames": frames,
    }


RESEARCH_DIRECTORY = Path(__file__).resolve().parents[2] / "research-output"
FOREX_REPLAY_LOCK = Lock()


@app.post("/api/workspaces/exness_forex/research/replay")
def replay_forex_strategies():
    if not FOREX_REPLAY_LOCK.acquire(blocking=False):
        raise HTTPException(409, "Replay đang chạy; chờ kết quả, không gửi lại")
    temporary = None
    try:
        from scripts.exness_mtf_replay import run_comparison

        from .exness_observation import load_frames

        now = int(time.time())
        with Session() as session:
            report = run_comparison(load_frames(session, now), now)
        RESEARCH_DIRECTORY.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=RESEARCH_DIRECTORY,
                                         prefix="exness-compare-", suffix=".tmp", delete=False) as output:
            temporary = Path(output.name)
            json.dump(report, output, ensure_ascii=False, allow_nan=False)
        temporary.replace(RESEARCH_DIRECTORY / "exness-strategy-compare.json")
        return exness_research()
    except ValueError as exc:
        raise HTTPException(422, "Dữ liệu chưa đủ hoặc không hợp lệ để replay; báo cáo cũ được giữ nguyên") from exc
    except OSError as exc:
        raise HTTPException(503, "Không lưu được báo cáo replay; kiểm tra thư mục research-output") from exc
    finally:
        try:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        finally:
            FOREX_REPLAY_LOCK.release()

@app.get("/api/workspaces/exness_forex/research")
def exness_research():
    results = []
    compare_path = RESEARCH_DIRECTORY / "exness-strategy-compare.json"
    comparison = None
    comparison_error = None
    if compare_path.exists():
        try:
            if compare_path.stat().st_size > 8 * 1024 * 1024:
                raise ValueError("Oversized comparison")
            comparison = json.loads(compare_path.read_text(encoding="utf-8"))
            if (comparison["version"] != "exness_portfolio_compare_v1"
                    or comparison["source"] != "exness_forex" or comparison["symbol"] != "XAUUSD"
                    or not isinstance(comparison["assumptions"], dict)):
                raise ValueError("Wrong comparison source")
            for key in ("start", "end", "as_of", "processed_bars"):
                if type(comparison[key]) is not int or comparison[key] < 0:
                    raise ValueError("Invalid comparison time or count")
            identities = set()
            for row in comparison["results"]:
                identity = (row["strategy"], row["slippage_bps"])
                if (identity in identities or identity[0] not in STRATEGIES
                        or type(identity[1]) is not int or identity[1] not in (0, 1, 2)
                        or row["status"] not in ("blocked", "open", "complete")
                        or type(row["equity_verified"]) is not bool):
                    raise ValueError("Invalid comparison row")
                identities.add(identity)
                for key in ("signals", "accepted", "skipped_busy", "trades", "win_rate", "final_balance",
                            "return_pct", "profit_factor", "max_drawdown_pct", "mean_r"):
                    value = row[key]
                    if value is None and key in ("win_rate", "profit_factor", "mean_r"):
                        continue
                    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                        raise ValueError("Invalid comparison metric")
                if (not isinstance(row["rejection_reasons"], dict)
                        or any(not isinstance(key, str) or type(value) is not int or value < 0
                               for key, value in row["rejection_reasons"].items())
                        or row["blocked_at"] is not None and (type(row["blocked_at"]) is not int or row["blocked_at"] < 0)):
                    raise ValueError("Invalid rejection counts or gap timestamp")
                if any(type(row.get(key, 0)) is not int or row.get(key, 0) < 0
                       for key in ("excluded_trades", "session_closes")):
                    raise ValueError("Invalid exclusion counts")
                gap = row.get("blocked_gap")
                if gap is not None and (not isinstance(gap, dict)
                        or any(type(gap.get(key)) is not int or gap[key] < 0
                               for key in ("after", "before", "missing_intervals"))
                        or gap.get("classification") not in ("unverified_gap", "bid_ask_mismatch")):
                    raise ValueError("Invalid gap details")
            if len(identities) != 12:
                raise ValueError("Incomplete comparison")
            comparison = {key: comparison[key] for key in (
                "version", "source", "symbol", "start", "end", "as_of", "processed_bars", "dataset_sha256", "assumptions",
            )} | {"results": [{key: row[key] for key in (
                "strategy", "slippage_bps", "signals", "accepted", "skipped_busy", "trades", "win_rate",
                "final_balance", "return_pct", "profit_factor", "max_drawdown_pct", "mean_r", "status",
                "blocked_at", "equity_verified", "rejection_reasons",
            )} | {"blocked_gap": row.get("blocked_gap"),
                  "excluded_trades": row.get("excluded_trades", 0),
                  "session_closes": row.get("session_closes", 0)}
                for row in comparison["results"]]}
        except (OSError, UnicodeError, ValueError, TypeError, KeyError):
            comparison = None
            comparison_error = "Báo cáo so sánh không hợp lệ; chạy lại replay --compare"
    for filename in ("exness-mtf-replay.json", "exness-mtf-replay-slip-1bps.json", "exness-mtf-replay-slip-2bps.json"):
        path = RESEARCH_DIRECTORY / filename
        if not path.exists():
            continue
        try:
            if path.stat().st_size > 8 * 1024 * 1024:
                raise ValueError("Oversized report")
            report = json.loads(path.read_text(encoding="utf-8"))
            if report["version"] != "exness_mtf_replay_v1" or report["source"] != "exness_forex" or report["symbol"] != "XAUUSD":
                raise ValueError("Wrong source")
            if not all(isinstance(report[key], dict) for key in ("summary", "assumptions", "by_side")):
                raise ValueError("Invalid report")
            checks = [(report["summary"], key, key in ("win_rate", "mean_r_ex_swap", "profit_factor_ex_swap")) for key in (
                "confirmations", "resolved", "win_rate", "mean_r_ex_swap", "sum_r_ex_swap",
                "profit_factor_ex_swap", "closed_sample_drawdown_r",
            )]
            checks.append((report["assumptions"], "slippage_bps_each_side", False))
            for side in ("long", "short"):
                checks.extend((report["by_side"][side], key, key == "win_rate") for key in ("resolved", "sum_r_ex_swap", "win_rate"))
            for group, key, nullable in checks:
                value = group[key]
                if value is None and nullable:
                    continue
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                    raise ValueError("Invalid metric")
            for key in ("start", "end", "as_of"):
                if isinstance(report[key], bool) or not isinstance(report[key], int) or report[key] < 0:
                    raise ValueError("Invalid timestamp")
            if not isinstance(report["dataset_sha256"], str) or len(report["dataset_sha256"]) != 64:
                raise ValueError("Invalid dataset hash")
            results.append({key: report[key] for key in (
                "version", "source", "symbol", "as_of", "start", "end", "dataset_sha256",
                "processed_bars", "evaluated_bars", "summary", "assumptions", "by_side", "by_month",
            )})
        except (OSError, UnicodeError, ValueError, TypeError, KeyError):
            return {"available": False, "reports": [], "error": "Báo cáo Exness không hợp lệ; cần chạy lại replay"}
    from .exness_gaps import gap_audits
    with Session() as session:
        audits = gap_audits(session)
    return {"available": bool(results or comparison), "reports": results, "comparison": comparison,
            "gap_audits": audits, "error": comparison_error}

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
