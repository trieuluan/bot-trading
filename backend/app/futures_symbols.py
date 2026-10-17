"""Fixed paper universes; request and worker contexts never change each other's pair."""

from contextlib import contextmanager
from contextvars import ContextVar

SYMBOLS = (
    "BTC_USDT", "ETH_USDT", "SOL_USDT", "XRP_USDT", "DOGE_USDT",
    "AVAX_USDT", "SUI_USDT", "NEAR_USDT", "BNB_USDT", "LINK_USDT",
)
_symbol = ContextVar("futures_symbol", default=SYMBOLS[0])

def current_symbol():
    return _symbol.get()

def state_id():
    return SYMBOLS.index(current_symbol()) + 1

def market_symbol():
    return current_symbol().replace("_", "/") + ":USDT"

@contextmanager
def symbol_scope(symbol):
    if symbol not in SYMBOLS:
        raise ValueError("Unsupported Futures symbol")
    token = _symbol.set(symbol)
    try:
        yield
    finally:
        _symbol.reset(token)
