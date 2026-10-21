"""Closed bid/ask history ingestion. Never reads or changes MEXC trading state."""

import argparse
import json
import math
import os
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from .db import Session, Workspace, WorkspaceCandle, WorkspaceFeedState, utcnow
from .exness import ExnessError, ExnessReadOnly
from .forex_symbols import checked_symbol, configured_symbols

WORKSPACE = "exness_forex"
FRAMES = {"15m": ("M15", 900), "1h": ("H1", 3600), "4h": ("H4", 14400)}
HISTORY_FRAMES = {**FRAMES, "1m": ("M1", 60)}


def connect_exness():
    if os.getenv("EXNESS_ENVIRONMENT", "demo") != "demo":
        raise ExnessError("This feed requires demo configuration and XAUUSD")
    api = ExnessReadOnly.from_env()
    try:
        api.use_access_point()
        limits = api.limits()["limits"]["rest"]
        method = next(row for row in limits["methods"] if row["api_operation_id"] == "getCandleHistory")
        cap = method["limits"]["max_candles_per_request"]
        if isinstance(cap, bool) or not isinstance(cap, int) or cap < 1:
            raise ValueError
        api.candle_limit = min(cap, 1000)
        for rate in (limits["global_account_rate"], method["rate_limit"]):
            interval = rate["window_seconds"] / rate["limit"]
            if not math.isfinite(interval) or interval <= 0:
                raise ValueError
            # ponytail: 2x margin; other clients (API stream, VPS) may share the quota
            api.request_interval = max(api.request_interval, interval * 2 + 0.05)
        api.available_instruments = set(api.instruments()["instruments"])
        if not set(configured_symbols()) & api.available_instruments:
            raise ExnessError("Configured instruments unavailable for account")
        return api
    except (KeyError, ValueError, TypeError, StopIteration, ZeroDivisionError) as exc:
        api.client.close()
        raise ExnessError("Invalid Exness instrument or rate-limit configuration") from exc
    except Exception:
        api.client.close()
        raise


def normalize_rows(rows, frame, start, end, symbol="XAUUSD"):
    checked_symbol(symbol)
    step = HISTORY_FRAMES[frame][1]
    unique = {}
    try:
        for row in rows:
            stamp = datetime.fromisoformat(row["time"].replace("Z", "+00:00"))
            timestamp = stamp.timestamp()
            if stamp.tzinfo is None or timestamp % step or not start <= timestamp < end:
                raise ValueError
            if timestamp + step > end:
                raise ValueError
            volume = row.get("volume")
            if volume is not None and (isinstance(volume, bool) or not isinstance(volume, int) or not 0 <= volume <= 2**63 - 1):
                raise ValueError
            pair = []
            for side in ("bid", "ask"):
                values = {name: float(row[side][name]) for name in ("open", "high", "low", "close")}
                if any(isinstance(row[side][name], bool) for name in values):
                    raise ValueError
                if not all(math.isfinite(value) and value > 0 for value in values.values()):
                    raise ValueError
                if values["low"] > min(values["open"], values["close"]) or values["high"] < max(values["open"], values["close"]):
                    raise ValueError
                pair.append({"workspace_id": WORKSPACE, "symbol": symbol, "timeframe": frame,
                             "price_type": side, "time": int(timestamp), "tick_volume": volume, **values})
            if any(pair[1][field] < pair[0][field] for field in ("open", "high", "low", "close")):
                raise ValueError
            if timestamp in unique and unique[timestamp] != pair:
                raise ValueError
            unique[timestamp] = pair
    except (KeyError, ValueError, TypeError, AttributeError, OverflowError) as exc:
        raise ExnessError("Invalid or conflicting Exness bid/ask candles") from exc
    return [item for stamp in sorted(unique) for item in unique[stamp]]


def sync_page(api, frame, days=90, now=None, *, older=False, symbol="XAUUSD", live_first=False):
    checked_symbol(symbol)
    if symbol not in getattr(api, "available_instruments", {symbol}):
        raise ExnessError(f"Instrument unavailable: {symbol}")
    if frame not in HISTORY_FRAMES or isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= 365:
        raise ValueError("Choose 1m/15m/1h/4h and 1..365 initial calendar days")
    now = int(utcnow().timestamp()) if now is None else now
    current = datetime.fromtimestamp(now, timezone.utc)
    code, step = HISTORY_FRAMES[frame]
    end = now // step * step
    try:
        with Session.begin() as session:
            workspace = session.get(Workspace, WORKSPACE)
            if workspace is None or workspace.venue != WORKSPACE:
                raise ExnessError("Invalid Exness workspace; run migrations")
            state = session.scalar(select(WorkspaceFeedState).where(
                WorkspaceFeedState.workspace_id == WORKSPACE, WorkspaceFeedState.symbol == symbol, WorkspaceFeedState.timeframe == frame,
            ).with_for_update(skip_locked=True))
            if state is None:
                state = WorkspaceFeedState(workspace_id=WORKSPACE, symbol=symbol, timeframe=frame)
                session.add(state)
                session.flush()
            if state.retry_at and state.retry_at.replace(tzinfo=timezone.utc) > current:
                return {"status": "cooldown", "frame": frame}
            target_start = end - days * 86400
            if older and (state.started_at is None or state.checked_until is None):
                return {"status": "waiting_initial_sync", "frame": frame}
            if state.started_at is None:
                state.started_at = max(target_start, end - min(api.candle_limit, 300) * step) if live_first else target_start
            cursor = state.checked_until if state.checked_until is not None else state.started_at
            if cursor > end:
                raise ExnessError("Clock behind persisted Exness cursor")
            cap = api.candle_limit
            if older:
                if cursor < end:
                    return {"status": "waiting_live_sync", "frame": frame}
                if state.started_at <= target_start:
                    return {"status": "current", "frame": frame, "remaining_intervals": 0, "bars": 0}
                batch_end = state.started_at
                start = max(target_start, batch_end - cap * step)
            else:
                if cursor == end:
                    state.error = state.retry_at = None
                    return {"status": "current", "frame": frame, "remaining_intervals": 0, "bars": 0}
                start = cursor if cursor < end else max(state.started_at, end - 2 * step)
                if state.checked_until is not None and 0 < end - cursor <= step and cap > 2:
                    start = max(state.started_at, cursor - 2 * step)
                batch_end = min(end, start + cap * step)
            windows = [(start, batch_end)]
            if live_first and not older and batch_end < end:
                windows.insert(0, (max(batch_end, end - min(cap, 3) * step), end))
            entries = []
            for window_start, window_end in windows:
                raw = api.candles(symbol, code, window_start, window_end, now=now)
                if len(raw) > cap:
                    raise ExnessError("Exness returned more candles than the declared request cap")
                entries.extend(normalize_rows(raw, frame, window_start, window_end, symbol))
            insert = sqlite_insert if session.bind.dialect.name == "sqlite" else postgres_insert
            for offset in range(0, len(entries), 500):
                statement = insert(WorkspaceCandle).values(entries[offset:offset + 500])
                session.execute(statement.on_conflict_do_update(
                    index_elements=["workspace_id", "symbol", "timeframe", "price_type", "time"],
                    set_={field: getattr(statement.excluded, field) for field in (
                        "open", "high", "low", "close", "tick_volume",
                    )},
                ))
            if older:
                state.started_at = start
            else:
                state.checked_until = max(cursor, batch_end)
            state.last_sync = current
            state.error = state.retry_at = None
            remaining = max(0, (state.started_at - target_start) // step) if older else (end - state.checked_until) // step
            return {"status": "current" if remaining == 0 else "backfill_history" if older else "backfill", "frame": frame,
                    "bars": len(entries) // 2, "remaining_intervals": remaining}
    except ExnessError as exc:
        with Session.begin() as session:
            state = session.get(WorkspaceFeedState, (WORKSPACE, symbol, frame), with_for_update=True)
            if state:
                state.error = str(exc)[:200]
                state.retry_at = current + timedelta(seconds=exc.retry_after)
            if exc.rate_limited:
                for feed in session.scalars(select(WorkspaceFeedState).where(WorkspaceFeedState.workspace_id == WORKSPACE)):
                    retry_at = current + timedelta(seconds=exc.retry_after)
                    if feed.retry_at is None or feed.retry_at.replace(tzinfo=timezone.utc) < retry_at:
                        feed.retry_at = retry_at
        raise

def feed_retry_delay(symbol=None):
    now = utcnow()
    with Session() as session:
        retry_at = session.scalar(select(func.max(WorkspaceFeedState.retry_at)).where(
            WorkspaceFeedState.workspace_id == WORKSPACE,
            *([WorkspaceFeedState.symbol == symbol] if symbol is not None else []),
        ))
        return max(0, (retry_at.replace(tzinfo=timezone.utc) - now).total_seconds()) if retry_at else 0


def worker_heartbeat(enabled):
    with Session.begin() as session:
        for state in session.scalars(select(WorkspaceFeedState).where(WorkspaceFeedState.workspace_id == WORKSPACE)):
            state.enabled = enabled
            state.worker_heartbeat = utcnow()


def record_connection_failure(retry_after=60, *, rate_limited=False):
    with Session.begin() as session:
        for state in session.scalars(select(WorkspaceFeedState).where(WorkspaceFeedState.workspace_id == WORKSPACE)):
            state.error = ("Exness rate limit; waiting before retry" if rate_limited
                           else "Exness connection/configuration unavailable; retrying")
            retry_at = utcnow() + timedelta(seconds=retry_after)
            if state.retry_at is None or state.retry_at.replace(tzinfo=timezone.utc) < retry_at:
                state.retry_at = retry_at


def feed_summary(symbol="XAUUSD"):
    now = utcnow()
    with Session() as session:
        rows = session.scalars(select(WorkspaceFeedState).where(WorkspaceFeedState.workspace_id == WORKSPACE, WorkspaceFeedState.symbol == symbol)).all()
        counts = {frame: (count, first, last) for frame, count, first, last in session.execute(
            select(WorkspaceCandle.timeframe, func.count(), func.min(WorkspaceCandle.time), func.max(WorkspaceCandle.time))
            .where(WorkspaceCandle.workspace_id == WORKSPACE, WorkspaceCandle.symbol == symbol, WorkspaceCandle.price_type == "bid")
            .group_by(WorkspaceCandle.timeframe)
        )}
        frames = []
        for state in rows:
            count, first, last = counts.get(state.timeframe, (0, None, None))
            step = HISTORY_FRAMES[state.timeframe][1]
            frames.append({"timeframe": state.timeframe, "bars_per_side": count,
                           "first_candle": first, "last_candle": last, "checked_until": state.checked_until,
                           "last_sync": state.last_sync, "error": state.error, "retry_at": state.retry_at,
                           "remaining_intervals": max(0, (int(now.timestamp()) // step * step - state.checked_until) // step) if state.checked_until is not None else None,
                           "worker_online": bool(state.enabled and state.worker_heartbeat and (now - state.worker_heartbeat.replace(tzinfo=timezone.utc)).total_seconds() < 45)})
    return frames


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=90, help="Initial calendar-day lookback; resumes persisted cursor")
    args = parser.parse_args()
    if not 1 <= args.days <= 365:
        parser.error("--days must be 1..365")
    api = connect_exness()
    try:
        now = int(utcnow().timestamp())
        for frame in FRAMES:
            while True:
                result = sync_page(api, frame, days=args.days, now=now)
                print(json.dumps(result), flush=True)
                if result["status"] == "current":
                    break
                if result["status"] != "backfill":
                    raise ExnessError("Feed busy or in cooldown; rerun to resume")
        print(json.dumps(feed_summary(), default=str), flush=True)
    finally:
        api.client.close()


if __name__ == "__main__":
    try:
        main()
    except ExnessError as exc:
        raise SystemExit(str(exc)) from None
