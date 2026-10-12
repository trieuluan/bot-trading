"""Persist closed market bars and recover missed bars using REST polling."""

from datetime import timedelta

from sqlalchemy import select

from .db import BotState, Event, MarketCandle, MarketState, Session, Signal, utcnow
from .market import Candle
from .mexc import INTERVAL_SECONDS, SOURCE, SYMBOL, TIMEFRAME, MarketDataError, MexcFuturesAdapter
from .strategy import breakout


def load_candles(
    session, limit: int | None = None, since: int | None = None, until: int | None = None
) -> list[Candle]:
    query = (
        select(MarketCandle)
        .where(
            MarketCandle.source == SOURCE,
            MarketCandle.symbol == SYMBOL,
            MarketCandle.timeframe == TIMEFRAME,
        )
        .order_by(MarketCandle.time.desc())
    )
    if since is not None:
        query = query.where(MarketCandle.time >= since)
    if until is not None:
        query = query.where(MarketCandle.time < until)
    if limit:
        query = query.limit(limit)
    rows = list(session.scalars(query))
    return [Candle(r.time, r.open, r.high, r.low, r.close, r.volume) for r in reversed(rows)]


def save_candles(session, candles: list[Candle]):
    for candle in candles:
        session.merge(
            MarketCandle(
                source=SOURCE,
                symbol=SYMBOL,
                timeframe=TIMEFRAME,
                **candle.__dict__,
            )
        )


def sync_market(adapter: MexcFuturesAdapter | None = None) -> None:
    """Network outside transaction; cursor/analysis inside one locked transaction."""
    with Session() as session:
        market = session.get(MarketState, 1)
        if market.source != SOURCE:
            return
        retry_at = market.retry_at
        if retry_at and retry_at.replace(tzinfo=utcnow().tzinfo) > utcnow():
            return
        existing = load_candles(session, limit=1)
        last_processed = market.last_processed

    client = adapter or MexcFuturesAdapter()
    try:
        now = client.server_time()
        end = now // INTERVAL_SECONDS * INTERVAL_SECONDS
        # Backtest downloads can populate newer bars before the worker catches up.
        # Resume from the analysis cursor, not just the newest cached candle.
        if last_processed is not None:
            start = last_processed - 20 * INTERVAL_SECONDS
        else:
            start = existing[-1].time if existing else end - 120 * INTERVAL_SECONDS
        end = min(end, start + 10_000 * INTERVAL_SECONDS)
        candles = client.history(start, end, now)
        with Session.begin() as session:
            # Always lock bot before market, also used by the control/config endpoints.
            bot = session.scalar(select(BotState).where(BotState.id == 1).with_for_update())
            market = session.scalar(
                select(MarketState).where(MarketState.id == 1).with_for_update()
            )
            if market.source != SOURCE:
                return
            save_candles(session, candles)
            session.flush()
            history = load_candles(
                session,
                since=market.last_processed - 20 * INTERVAL_SECONDS
                if market.last_processed is not None
                else None,
                until=end,
            )
            if market.last_processed is None:
                market.last_processed = candles[-1].time
            elif bot.enabled:
                for index, candle in enumerate(history):
                    if candle.time <= market.last_processed:
                        continue
                    window = history[max(0, index - 20) : index + 1]
                    if len(window) < 21 or any(
                        b.time - a.time != INTERVAL_SECONDS for a, b in zip(window, window[1:])
                    ):
                        raise MarketDataError("Gap in stored candles; signal processing paused")
                    setup = breakout(window, allow_short=True)
                    exists = session.scalar(
                        select(Signal.id).where(
                            Signal.source == SOURCE,
                            Signal.symbol == SYMBOL,
                            Signal.timeframe == TIMEFRAME,
                            Signal.candle_time == candle.time,
                        )
                    )
                    if setup and exists is None:
                        session.add(
                            Signal(
                                source=SOURCE,
                                symbol=SYMBOL,
                                timeframe=TIMEFRAME,
                                candle_time=candle.time,
                                **setup.__dict__,
                            )
                        )
                        session.add(
                            Event(
                                kind="signal",
                                message=f"MEXC Futures: breakout {setup.side.upper()}; không gửi lệnh",
                            )
                        )
                    market.last_processed = candle.time
            else:
                # Do not replay bars that arrived while the user intentionally paused the bot.
                market.last_processed = candles[-1].time
            market.last_sync = utcnow()
            market.error = None
            market.retry_at = None
    except MarketDataError as exc:
        with Session.begin() as session:
            market = session.scalar(
                select(MarketState).where(MarketState.id == 1).with_for_update()
            )
            if market.source == SOURCE:
                if market.error != str(exc):
                    session.add(Event(kind="market_error", message=str(exc)))
                market.error = str(exc)
                market.retry_at = utcnow() + timedelta(seconds=exc.retry_after)
        raise
    finally:
        if adapter is None:
            client.close()
