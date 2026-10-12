"""Forward-only, read-only MTF observations. Never publishes trade signals or orders."""

import time
from datetime import timedelta, timezone

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from .db import MarketState, MtfObservationEvent, MtfObservationState, Session, utcnow
from .feed import load_candles, save_candles
from .market import serialize
from .mexc import SOURCE, MarketDataError, MexcFuturesAdapter, validate_candles
from .strategy import aggregate, mtf_scan

router = APIRouter(prefix="/api/observations/mtf", tags=["MTF Observations"])
HISTORY = 1600
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
        candles = load_candles(session, limit=HISTORY, until=end)
    if not candles or candles[-1].time != end - 900:
        return
    client = None
    try:
        backfill = len(candles) < HISTORY
        try:
            validate_candles(candles)
        except MarketDataError:
            backfill = True
        if backfill:
            client = adapter or MexcFuturesAdapter()
            candles = client.history(end - HISTORY * 900, end, int(now))
        validate_candles(candles)
        if (
            len(candles) != HISTORY
            or candles[0].time != end - HISTORY * 900
            or candles[-1].time != end - 900
        ):
            raise MarketDataError("MTF cần đủ 1.600 nến 15m đã đóng liên tục")
        with Session.begin() as session:
            market = session.scalar(
                select(MarketState).where(MarketState.id == 1).with_for_update()
            )
            state = session.scalar(
                select(MtfObservationState).where(MtfObservationState.id == 1).with_for_update()
            )
            if market.source != SOURCE or (
                state.last_candle is not None and state.last_candle >= candles[-1].time
            ):
                return
            if backfill:
                save_candles(session, candles)
            previous = state.snapshot
            gap = state.last_candle is not None and candles[-1].time - state.last_candle > 900
            if state.started_at is None or gap:
                state.started_at = candles[-1].time + 900
                snapshot = observe(candles, None, state.started_at)
                snapshot["reason"] = "Bắt đầu quan sát từ nến kế tiếp; không phát lại lịch sử"
                if gap:
                    snapshot["reason"] = (
                        "Bỏ qua khoảng thiếu dữ liệu; bắt đầu lại, không phát lại setup cũ"
                    )
                    if previous and previous["status"] in ACTIVE:
                        snapshot.update(previous)
                        snapshot.update(
                            status="cancelled",
                            candle_time=candles[-1].time,
                            reason="Hủy setup do gián đoạn dữ liệu; không xác nhận bằng nến đã bỏ lỡ",
                        )
            else:
                snapshot = observe(candles, previous, state.started_at)
            state.snapshot = snapshot
            state.last_candle = candles[-1].time
            state.error = state.retry_at = None
            session.add(MtfObservationEvent(candle_time=state.last_candle, snapshot=snapshot))
    except MarketDataError as exc:
        with Session.begin() as session:
            state = session.scalar(
                select(MtfObservationState).where(MtfObservationState.id == 1).with_for_update()
            )
            state.error = str(exc)
            state.retry_at = utcnow() + timedelta(seconds=exc.retry_after)
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
            "snapshot": state.snapshot,
            "events": [
                {"candle_time": event.candle_time, "snapshot": event.snapshot} for event in events
            ],
            "candles": serialize(load_candles(session, limit=160, until=int(now) // 900 * 900))
            if available
            else [],
        }
