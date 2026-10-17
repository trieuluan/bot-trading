"""Read-only, timestamped XAUUSD specification audit. Never assumes missing fees are zero."""

import hashlib
import os
import time
from decimal import ROUND_FLOOR, Decimal, InvalidOperation

from .db import ForexInstrumentState, Session
from .exness import ExnessError
from .exness_feed import WORKSPACE

SPEC_TTL = 3600

def account_tag(account_id):
    return hashlib.sha256(str(account_id).encode()).hexdigest()[:12]

def decimal_value(value, positive=False):
    if isinstance(value, bool):
        raise ValueError("Invalid numeric specification")
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("Invalid numeric specification") from exc
    if not result.is_finite() or (positive and result <= 0):
        raise ValueError("Invalid numeric specification")
    return result

def normalize_specs(account, condition, now, account_id=None):
    settings = account.get("settings", {})
    if account.get("is_demo") is not True or condition.get("instrument") != "XAUUSD":
        raise ValueError("Only verified Demo XAUUSD specifications are accepted")
    numbers = {
        key: str(decimal_value(condition.get(key), positive=True))
        for key in ("contract_size", "volume_min", "volume_max", "volume_step")
    }
    if Decimal(numbers["volume_min"]) > Decimal(numbers["volume_max"]):
        raise ValueError("Invalid lot range")
    digits = condition.get("point_digits")
    if isinstance(digits, bool) or not isinstance(digits, int) or not 0 <= digits <= 10:
        raise ValueError("Invalid price precision")
    multipliers = []
    for row in condition.get("swap_multipliers", []):
        multiplier = decimal_value(row.get("multiplier"))
        if row.get("day") not in ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN") or not 0 <= multiplier <= 10:
            raise ValueError("Invalid swap schedule")
        multipliers.append({"day": row["day"], "multiplier": str(multiplier)})
    currencies_valid = settings.get("currency") == condition.get("quote_currency") == "USD"
    commission = condition.get("commission_settings") or {}
    value = commission.get("commission_value") or {}
    amount = value.get("amount")
    commission_type = commission.get("commission_type")
    commission_per_unit = None
    if (
        currencies_valid and value.get("currency") == "USD"
        and commission_type in ("per_lot_per_side", "per_lot_per_trade") and amount is not None
    ):
        amount = decimal_value(amount)
        if amount < 0:
            raise ValueError("Invalid commission")
        commission_per_unit = float(amount * (2 if commission_type == "per_lot_per_side" else 1)
                                    / Decimal(numbers["contract_size"]))
    source = "api" if commission_per_unit is not None else None
    override = os.getenv("EXNESS_COMMISSION_PER_LOT_USD", "").strip()
    # ponytail: operator override only when API omits commission_settings; demo-only, never guessed.
    if commission_per_unit is None and not condition.get("commission_settings") and override and currencies_valid:
        fee = decimal_value(override)
        if fee < 0:
            raise ValueError("Invalid commission override")
        commission_per_unit = float(fee / Decimal(numbers["contract_size"]))
        commission_type, source = "operator_override_round_turn", "operator_override"
    return {
        "version": "exness_specs_v1", "observed_at": now, "account_tag": account_tag(account_id if account_id is not None else account.get("account_id")), "instrument": "XAUUSD",
        "account_currency": settings.get("currency"), "quote_currency": condition.get("quote_currency"),
        "account_leverage": str(decimal_value(settings.get("leverage"), positive=True)),
        "point_digits": digits, "trade_mode": condition.get("trade_mode"), **numbers,
        "margin_percent": str(decimal_value(condition.get("margin_percent"), positive=True)),
        "calc_mode": condition.get("calc_mode"), "margin_currency": condition.get("margin_currency"),
        "commission_type": commission_type,
        "commission_per_unit": commission_per_unit, "commission_source": source,
        "swap_long": str(decimal_value(condition.get("swap_long"))),
        "swap_short": str(decimal_value(condition.get("swap_short"))),
        "swap_multipliers": multipliers,
        "swap_included": False, "margin_verified": False, "costs_calibrated": False,
        "issues": (["commission_unavailable"] if commission_per_unit is None else [])
        + ([] if currencies_valid else ["currency_conversion_unverified"])
        + ["swap_units_and_rollover_unverified", "dynamic_margin_unverified", "slippage_assumed"],
    }

def sync_specs(api, now=None):
    now = int(time.time()) if now is None else now
    try:
        with Session.begin() as session:
            state = session.get(ForexInstrumentState, WORKSPACE)
            # ponytail: cache keyed by account only; commission override changes still wait for TTL.
            aid = getattr(api, "account_id", None)
            stale = bool(aid is not None and state and state.snapshot and state.snapshot.get("account_tag") != account_tag(aid))
            if state and state.retry_at > now and not stale:
                return
            if state is None:
                state = ForexInstrumentState(workspace_id=WORKSPACE, checked_at=now, retry_at=now + SPEC_TTL)
                session.add(state)
            else:
                state.retry_at = now + SPEC_TTL
    except Exception:
        return
    try:
        acc = api.account()
        aid = getattr(api, "account_id", acc.get("account_id"))
        snapshot = normalize_specs(acc, api.condition("XAUUSD"), now, account_id=aid)
        error = None
        retry = now + SPEC_TTL
    except (ExnessError, ValueError, KeyError, TypeError, AttributeError) as exc:
        snapshot = None
        error = "Instrument specification unavailable; costs remain unverified"
        retry = now + max(SPEC_TTL, int(getattr(exc, "retry_after", SPEC_TTL)))
    try:
        with Session.begin() as session:
            state = session.get(ForexInstrumentState, WORKSPACE)
            if state is not None:
                state.checked_at, state.error, state.retry_at = now, error, retry
                if snapshot is not None:
                    state.snapshot = snapshot
    except Exception:
        pass

def spec_summary(session, now=None):
    now = int(time.time()) if now is None else now
    try:
        state = session.get(ForexInstrumentState, WORKSPACE)
    except Exception:
        return {
            "status": "unavailable",
            "checked_at": None,
            "error": "Chưa áp dụng migration 0012",
            "profile": None,
        }
    profile = state.snapshot if state else None
    return {
        "status": "unavailable" if not profile else "stale"
        if state.error or not 0 <= now - profile["observed_at"] < SPEC_TTL else "partial",
        "checked_at": state.checked_at if state else None,
        "error": state.error if state else None, "profile": profile,
    }

def lot_size(units, profile):
    contract = Decimal(profile["contract_size"])
    step = Decimal(profile["volume_step"])
    lots = min(Decimal(str(units)) / contract, Decimal(profile["volume_max"]))
    lots = (lots / step).to_integral_value(rounding=ROUND_FLOOR) * step
    if lots < Decimal(profile["volume_min"]):
        return None
    return float(lots * contract), float(lots)
