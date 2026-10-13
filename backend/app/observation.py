"""Causal MTF observation and catch-up. Never publishes trade signals or orders."""

import time
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select

from .db import MarketState, MtfObservationEvent, MtfObservationState, MtfResearchSample, Session
from .feed import load_candles, save_candles
from .market import serialize
from .mexc import SOURCE, MarketDataError, MexcFuturesAdapter, validate_candles
from .mtf_research import collect_sample
from .strategy import aggregate, mtf_scan

router = APIRouter(prefix="/api/observations/mtf", tags=["MTF Observations"])
HISTORY = 1600
CATCHUP_BATCH = 96
ACTIVE = ("waiting_retest", "waiting_confirm")


def observe(candles, previous, started_at):
    scan = mtf_scan(candles)
    last = candles[-1]
    hours = aggregate(candles, 3600)
    history = hours[-20:]
    current = None
    if previous and previous["status"] in ACTIVE:
        current = next(
            (
                watch
                for watch in scan["watches"]
                if watch["breakout_time"] == previous["breakout_time"]
                and watch["side"] == previous["side"]
            ),
            None,
        )
        if current is None:
            expired = last.time + 900 > previous["expires_at"]
            current = {
                **previous,
                "status": "expired" if expired else "cancelled",
                "reason": "Quá 12 giờ chưa xác nhận"
                if expired
                else "Xu hướng 4h không còn cùng chiều setup",
            }
        elif any(
            current[key] != previous[key]
            for key in ("level", "range_start", "range_end", "range_high", "range_low")
        ):
            current = {
                **previous,
                "status": "cancelled",
                "reason": "Nến vùng tham chiếu đã thay đổi; hủy setup, không dịch chuyển vùng đã lưu",
            }
    else:
        current = next(
            (
                watch
                for watch in scan["watches"]
                if watch["breakout_time"] + 3600 > started_at
                and watch["breakout_time"] + 3600 == last.time + 900
            ),
            None,
        )
    snapshot = {
        "strategy": "mtf_observer_v1",
        "parameters": {"lookback_1h": 20, "ema_4h": 50, "wait_hours": 12, "target_r": 2},
        "candle_time": last.time,
        "trend": scan["trend"],
        "closed_4h_time": aggregate(candles, 14400)[-1].time,
        "status": "waiting_breakout",
        "reason": "Chờ breakout 1h cùng chiều xu hướng 4h; chỉ quan sát",
        "range_start": history[0].time,
        "range_end": history[-1].time + 3600,
        "range_high": max(c.high for c in history),
        "range_low": min(c.low for c in history),
        "side": None,
        "level": None,
        "breakout_time": None,
        "touch_time": None,
        "confirm_time": None,
        "expires_at": None,
        "stop": None,
        "target": None,
    }
    if current:
        snapshot.update(current)
        snapshot.update(candle_time=last.time, trend=scan["trend"])
        snapshot["expires_at"] = current["breakout_time"] + 3600 + 12 * 3600
    return snapshot


def observation_steps(candles, previous=None, started_at=None, after=None, provenance="historical"):
    """Replay a causal fixed-size window; reject gaps before processing any step."""
    if provenance not in ("live", "catch_up", "historical"):
        raise ValueError("Invalid observation provenance")
    validate_candles(candles)
    if len(candles) < HISTORY:
        raise MarketDataError("MTF cần đủ 1.600 nến 15m đã đóng liên tục")
    started_at = started_at if started_at is not None else candles[HISTORY - 1].time + 900
    if after is not None and candles[HISTORY - 1].time != after + 900:
        raise MarketDataError("Missing catch-up boundary; refusing to skip candles")
    for index in range(HISTORY - 1, len(candles)):
        window = candles[index - HISTORY + 1:index + 1]
        snapshot = observe(window, previous, started_at)
        snapshot["provenance"] = provenance
        previous = snapshot
        yield window, snapshot

def observation_tick(adapter=None, now=None):
    now = time.time() if now is None else now
    end = int(now) // 900 * 900
    with Session() as session:
        market = session.get(MarketState, 1)
        state = session.get(MtfObservationState, 1)
        if state is None:
            raise RuntimeError("Run migrations before observing MTF")
        if market.source != SOURCE or market.error or not market.last_sync:
            return
        if now - market.last_sync.replace(tzinfo=timezone.utc).timestamp() > 60:
            return
        if state.last_candle is not None and state.last_candle >= end - 900:
            return
        if state.retry_at and state.retry_at.replace(tzinfo=timezone.utc).timestamp() > now:
            return
        after = state.last_candle
        first = after + 900 if after is not None else end - 900
        batch_end = min(end, first + CATCHUP_BATCH * 900)
        start = first - (HISTORY - 1) * 900
        candles = load_candles(session, since=start, until=batch_end)
    client = None
    try:
        backfill = len(candles) != (batch_end - start) // 900
        try:
            validate_candles(candles)
        except MarketDataError:
            backfill = True
        if backfill:
            client = adapter or MexcFuturesAdapter()
            candles = client.history(start, batch_end, int(now))
        validate_candles(candles)
        if (
            len(candles) != (batch_end - start) // 900
            or candles[0].time != start
            or candles[-1].time != batch_end - 900
        ):
            raise MarketDataError("MTF cần đủ 1.600 nến 15m đã đóng liên tục")
        with Session.begin() as session:
            market = session.scalar(
                select(MarketState).where(MarketState.id == 1).with_for_update()
            )
            state = session.scalar(
                select(MtfObservationState).where(MtfObservationState.id == 1).with_for_update()
            )
            if market.source != SOURCE or state.last_candle != after:
                return
            if backfill:
                save_candles(session, candles)
            if state.started_at is None:
                state.started_at = first + 900
            provenance = "catch_up" if first < end - 900 or now - (first + 900) > 60 else "live"
            for window, snapshot in observation_steps(
                candles, state.snapshot, state.started_at, after, provenance,
            ):
                state.snapshot = snapshot
                state.last_candle = window[-1].time
                collect_sample(session, window, snapshot)
                session.add(MtfObservationEvent(candle_time=state.last_candle, snapshot=snapshot))
            state.error = state.retry_at = None
    except MarketDataError as exc:
        with Session.begin() as session:
            state = session.scalar(
                select(MtfObservationState).where(MtfObservationState.id == 1).with_for_update()
            )
            state.error = str(exc)
            state.retry_at = datetime.fromtimestamp(now, timezone.utc) + timedelta(
                seconds=exc.retry_after
            )
    finally:
        if client is not None and adapter is None:
            client.close()


@router.get("")
def observation_state():
    now = time.time()
    with Session() as session:
        state = session.get(MtfObservationState, 1)
        if state is None:
            raise HTTPException(503, "Run migrations")
        market = session.get(MarketState, 1)
        available = market.source == SOURCE
        stale = bool(
            not available
            or state.error
            or market.error
            or not market.last_sync
            or now - market.last_sync.replace(tzinfo=timezone.utc).timestamp() > 60
            or state.last_candle != int(now) // 900 * 900 - 900
        )
        events = session.scalars(
            select(MtfObservationEvent).order_by(MtfObservationEvent.candle_time.desc()).limit(20)
        ).all()
        return {
            "observation_only": True,
            "available": available,
            "stale": stale,
            "error": state.error or market.error,
            "started_at": state.started_at,
            "last_candle": state.last_candle,
            "backlog_bars": max(0, (int(now) // 900 * 900 - 900 - state.last_candle) // 900)
            if state.last_candle is not None else None,
            "catchup_batch": CATCHUP_BATCH,
            "snapshot": state.snapshot,
            "events": [
                {"candle_time": event.candle_time, "snapshot": event.snapshot} for event in events
            ],
            "candles": serialize(load_candles(session, limit=160, until=int(now) // 900 * 900))
            if available
            else [],
        }


@router.get("/dataset")
def research_dataset(
    after: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=1000),
    provenance: Literal["live", "catch_up"] | None = None,
):
    with Session() as session:
        query = select(MtfResearchSample)
        if provenance == "live":
            query = query.where(
                (MtfResearchSample.evaluation["provenance"].as_string() == "live")
                | MtfResearchSample.evaluation["provenance"].as_string().is_(None)
            )
        elif provenance:
            query = query.where(MtfResearchSample.evaluation["provenance"].as_string() == provenance)
        rows = session.scalars(
            query
            .where(MtfResearchSample.confirm_time > after)
            .order_by(MtfResearchSample.confirm_time)
            .limit(limit + 1)
        ).all()
        return {
            "observation_only": True,
            "funding_included": False,
            "label_definition": "1 = positive hypothetical PnL after fixed fees/slippage, excluding funding",
            "samples": [
                {
                    "confirm_time": row.confirm_time,
                    "provenance": row.evaluation.get("provenance", "live"),
                    "features": row.features,
                    "evaluation": row.evaluation,
                }
                for row in rows[:limit]
            ],
            "next_after": rows[limit - 1].confirm_time if len(rows) > limit else None,
        }
