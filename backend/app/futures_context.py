"""Cached point-in-time inputs shared by forward signals and validation."""

from sqlalchemy import select

from .db import FuturesOpenInterest
from .feed import load_candles
from .futures_symbols import SYMBOLS, current_symbol, symbol_scope


def load_signal_context(session, since, until):
    symbol = current_symbol()
    peers = SYMBOLS[1:] if symbol == "BTC_USDT" else ("BTC_USDT",)
    benchmarks = {}
    for peer in peers:
        with symbol_scope(peer):
            benchmarks[peer] = load_candles(session, since=since, until=until)
    context = {"benchmarks": benchmarks} if all(benchmarks.values()) else {}
    history = [(row.time, row.hold_vol) for row in session.scalars(
        select(FuturesOpenInterest).where(
            FuturesOpenInterest.symbol == symbol,
            FuturesOpenInterest.time >= since - 4500,
            FuturesOpenInterest.time <= until,
        ).order_by(FuturesOpenInterest.time)
    )]
    if history:
        context["oi_history"] = history
    return context
