"""Account and entry controls shared by the Forex API and its single worker."""

import os
from decimal import Decimal, InvalidOperation

from .exness import ExnessError


def live_mode():
    return os.getenv("EXNESS_ENVIRONMENT") == "real"


def execution_configured():
    flag = "EXNESS_LIVE_EXECUTION_ENABLED" if live_mode() else "EXNESS_DEMO_EXECUTION_ENABLED"
    return os.getenv(flag, "").lower() == "true"


def opening_configured():
    if not execution_configured():
        return False
    if not live_mode():
        return True
    if os.getenv("EXNESS_LIVE_OPEN_ORDERS") != "true" or not os.getenv("APP_PASSWORD", "").strip():
        return False
    try:
        live_risk_limits()
    except ExnessError:
        return False
    return True


def verify_live_account(api):
    if not live_mode() or api.account_id != os.getenv("EXNESS_ACCOUNT_ID", "").strip():
        raise ExnessError("Live Exness account not configured")
    account = api.account()
    settings = account.get("settings")
    if (account.get("is_demo") is not False or str(account.get("account_id")) != api.account_id
            or not isinstance(settings, dict) or settings.get("currency") != "USD"):
        raise ExnessError("Execution requires a broker-verified matching real USD account")
    return account


def live_risk_limits():
    names = ("EXNESS_LIVE_RISK_PER_SETUP_PCT", "EXNESS_LIVE_MAX_TOTAL_RISK_PCT", "EXNESS_LIVE_MAX_DAILY_DRAWDOWN_PCT")
    try:
        setup, total, daily = (Decimal(os.getenv(name, "")) for name in names)
        if all(value.is_finite() for value in (setup, total, daily)) and 0 < setup <= total < 100 and 0 < daily < 100:
            return setup, total, daily
    except InvalidOperation:
        pass
    raise ExnessError("live_risk_limits_missing_or_invalid")
