"""Forex instrument identity; never changes the selected instrument globally."""

import os

from fastapi import HTTPException

SYMBOLS = (
    "XAUUSD", "EURUSD", "GBPUSD", "AUDUSD", "NZDUSD", "USDJPY", "USDCAD",
    "USDCHF", "EURJPY", "GBPJPY", "EURGBP",
)
CONVERSION_SYMBOLS = {"JPY": "USDJPY", "CAD": "USDCAD", "CHF": "USDCHF", "GBP": "GBPUSD"}


def configured_symbols():
    raw = os.getenv("EXNESS_SYMBOLS", "").strip() or os.getenv("EXNESS_SYMBOL", "XAUUSD")
    symbols = tuple(dict.fromkeys(part.strip().upper() for part in raw.split(",") if part.strip()))
    if not symbols or any(symbol not in SYMBOLS for symbol in symbols):
        raise ValueError("Unsupported EXNESS_SYMBOLS configuration")
    return symbols


def checked_symbol(symbol):
    if symbol not in SYMBOLS:
        raise ValueError("Unsupported Forex symbol")
    return symbol


def request_symbol(symbol="XAUUSD"):
    if symbol not in configured_symbols():
        raise HTTPException(status_code=422, detail="Forex symbol is not configured")
    return symbol
