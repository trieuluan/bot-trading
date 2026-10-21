"""Multi-symbol conversion to USD account currency."""

import math
import time
from decimal import Decimal

from sqlalchemy import select

from .db import Session, WorkspaceCandle
from .exness_feed import WORKSPACE
from .forex_symbols import CONVERSION_SYMBOLS

MAX_CONVERSION_AGE = 3600


def conversion_to_usd(quote_currency, session=None, now=None):
    quote_currency = (quote_currency or "").upper()
    now = int(time.time()) if now is None else now
    if quote_currency == "USD":
        return {
            "rate": Decimal("1"),
            "gain_rate": Decimal("1"),
            "pair": None,
            "direction": "direct",
            "price": 1.0,
            "observed_at": now,
            "verified": True,
        }

    pair = CONVERSION_SYMBOLS.get(quote_currency)
    if not pair:
        return {
            "rate": None,
            "pair": None,
            "direction": "unsupported",
            "price": None,
            "observed_at": None,
            "verified": False,
        }

    owns_session = session is None
    s = Session() if owns_session else session
    try:
        candle = s.scalar(
            select(WorkspaceCandle)
            .where(
                WorkspaceCandle.workspace_id == WORKSPACE,
                WorkspaceCandle.symbol == pair,
                WorkspaceCandle.timeframe == "15m",
                WorkspaceCandle.price_type == "bid",
                WorkspaceCandle.time + 900 <= now,
            )
            .order_by(WorkspaceCandle.time.desc())
            .limit(1)
        )
        if not candle or not math.isfinite(candle.close) or candle.close <= 0 or now - (candle.time + 900) > MAX_CONVERSION_AGE:
            return {
                "rate": None,
                "pair": pair,
                "direction": f"{'divide' if quote_currency in ('JPY', 'CAD', 'CHF') else 'multiply'}_{pair.lower()}",
                "price": candle.close if candle else None,
                "observed_at": candle.time + 900 if candle else None,
                "verified": False,
            }
        ask = s.scalar(select(WorkspaceCandle).where(
            WorkspaceCandle.workspace_id == WORKSPACE, WorkspaceCandle.symbol == pair,
            WorkspaceCandle.timeframe == "15m", WorkspaceCandle.price_type == "ask",
            WorkspaceCandle.time == candle.time,
        ))
        if not ask or not math.isfinite(ask.close) or ask.close < candle.close:
            raise ValueError("Missing paired conversion ask candle")
        price = Decimal(str(candle.close))
        ask_price = Decimal(str(ask.close))
        if quote_currency in ("JPY", "CAD", "CHF"):
            rate = Decimal("1") / price
            gain_rate = Decimal("1") / ask_price
            direction = f"divide_{pair.lower()}"
        elif quote_currency == "GBP":
            rate = ask_price
            gain_rate = price
            direction = f"multiply_{pair.lower()}"
        else:
            return {
                "rate": None,
                "pair": pair,
                "direction": "unsupported",
                "price": None,
                "observed_at": None,
                "verified": False,
            }
        return {
            "rate": rate,
            "gain_rate": gain_rate,
            "pair": pair,
            "direction": direction,
            "price": float(price),
            "ask": float(ask_price),
            "observed_at": candle.time + 900,
            "verified": True,
        }
    finally:
        if owns_session:
            s.close()


def cost_context(profile, now, session=None, *, account=None, symbol=None):
    if not profile or profile.get("account_currency") != "USD":
        raise ValueError("Only verified USD demo account costs supported")
    if account is not None or symbol is not None:
        from .forex_costs import SPEC_TTL, account_tag
        if (profile.get("instrument") != symbol or profile.get("account_tag") != account_tag(account)
                or not 0 <= now - profile.get("observed_at", 0) < SPEC_TTL):
            raise ValueError("Instrument specification identity mismatch or stale")
    conversion = conversion_to_usd(profile.get("quote_currency"), session, now)
    if not conversion["verified"]:
        raise ValueError("Currency conversion unavailable or stale")
    commission = profile.get("commission_account_per_unit", profile.get("commission_per_unit"))
    if commission is not None and (not Decimal(str(commission)).is_finite() or Decimal(str(commission)) < 0):
        raise ValueError("Invalid commission")
    result = {**profile, "conversion": {key: str(value) if isinstance(value, Decimal) else value
                                       for key, value in conversion.items()}}
    result["quote_to_account_loss"] = str(conversion["rate"])
    result["quote_to_account_gain"] = str(conversion["gain_rate"])
    result["commission_account_per_unit"] = commission
    result["commission_per_unit"] = None if commission is None else float(Decimal(str(commission)) / conversion["gain_rate"])
    return result
