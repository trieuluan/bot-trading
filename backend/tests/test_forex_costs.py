from datetime import datetime, timezone

import pytest

from backend.app.db import ForexInstrumentState, Session
from backend.app.exness import ExnessError
from backend.app.exness_feed import WORKSPACE
from backend.app.forex_costs import lot_size, normalize_specs, spec_summary, sync_specs
from backend.app.forex_market_sessions import market_session

XAU_SESSIONS = [
    {"trade_mode": "trading_disabled", "week_start_time": "Sun, 00:00:00", "week_end_time": "Sun, 22:01:29"},
    {"trade_mode": "enabled", "week_start_time": "Sun, 22:01:30", "week_end_time": "Sun, 22:04:59"},
    {"trade_mode": "enabled", "week_start_time": "Sun, 22:05:00", "week_end_time": "Sun, 23:59:59"},
    *[row for day in ("Mon", "Tue", "Wed", "Thu") for row in (
        {"trade_mode": "enabled", "week_start_time": f"{day}, 00:00:00", "week_end_time": f"{day}, 20:57:59"},
        {"trade_mode": "trading_disabled", "week_start_time": f"{day}, 20:58:00", "week_end_time": f"{day}, 22:00:29"},
        {"trade_mode": "enabled", "week_start_time": f"{day}, 22:00:30", "week_end_time": f"{day}, 22:01:59"},
        {"trade_mode": "enabled", "week_start_time": f"{day}, 22:02:00", "week_end_time": f"{day}, 23:59:59"},
    )],
    {"trade_mode": "enabled", "week_start_time": "Fri, 00:00:00", "week_end_time": "Fri, 20:57:59"},
    {"trade_mode": "trading_disabled", "week_start_time": "Fri, 20:58:00", "week_end_time": "Fri, 23:59:59"},
    {"trade_mode": "trading_disabled", "week_start_time": "Sat, 00:00:00", "week_end_time": "Sat, 23:59:59"},
]

EUR_SESSIONS = [
    {"trade_mode": "trading_disabled", "week_start_time": "Sun, 00:00:00", "week_end_time": "Sun, 21:04:59"},
    {"trade_mode": "enabled", "week_start_time": "Sun, 21:05:00", "week_end_time": "Sun, 23:59:59"},
    *[{"trade_mode": "enabled", "week_start_time": f"{day}, 00:00:00", "week_end_time": f"{day}, 23:59:59"}
      for day in ("Mon", "Tue", "Wed", "Thu")],
    {"trade_mode": "enabled", "week_start_time": "Fri, 00:00:00", "week_end_time": "Fri, 20:58:59"},
    {"trade_mode": "trading_disabled", "week_start_time": "Fri, 20:59:00", "week_end_time": "Fri, 23:59:59"},
    {"trade_mode": "trading_disabled", "week_start_time": "Sat, 00:00:00", "week_end_time": "Sat, 23:59:59"},
]


def utc(day, hour, minute, second=0):
    return int(datetime(2026, 10, day, hour, minute, second, tzinfo=timezone.utc).timestamp())


def specification():
    return {
        "instrument": "XAUUSD", "quote_currency": "USD", "margin_currency": "XAU",
        "contract_size": "100", "volume_min": "0.01", "volume_max": "200",
        "volume_step": "0.01", "point_digits": 3, "margin_percent": "1",
        "calc_mode": "forex", "trade_mode": "enabled", "swap_long": "-516.4", "swap_short": "0",
        "swap_multipliers": [{"day": "WED", "multiplier": 3}],
    }

def account():
    return {"is_demo": True, "account_id": "not-to-store", "settings": {"currency": "USD", "leverage": "2000"}}

def test_missing_commission_swap_units_and_margin_are_explicit_not_calibrated():
    profile = normalize_specs(account(), specification(), 1000)
    assert profile["commission_per_unit"] is None
    assert profile["costs_calibrated"] is False
    assert profile["swap_included"] is False
    assert profile["margin_verified"] is False
    assert profile["swap_long"] == "-516.4"
    assert profile["swap_multipliers"] == [{"day": "WED", "multiplier": "3"}]
    assert "not-to-store" not in str(profile)

@pytest.mark.parametrize("kind,amount,expected", [
    ("per_lot_per_side", "3.5", 0.07), ("per_lot_per_trade", "7", 0.07),
    ("per_lot_per_side", "0", 0.0), ("undefined", "0", None), ("per_million_usd", "7", None),
])
def test_only_supported_explicit_commission_is_applied(kind, amount, expected):
    condition = specification()
    condition["commission_settings"] = {"commission_type": kind, "commission_value": {"amount": amount, "currency": "USD"}}
    profile = normalize_specs(account(), condition, 1000)
    assert profile["commission_per_unit"] == expected

@pytest.mark.parametrize("field,value", [
    ("contract_size", "NaN"), ("volume_min", "-0.01"), ("volume_max", "0"),
    ("volume_step", True), ("point_digits", True), ("point_digits", 20),
    ("swap_long", "Infinity"), ("volume_min", "300"),
])
def test_invalid_specs_rejected(field, value):
    condition = specification()
    condition[field] = value
    with pytest.raises(ValueError):
        normalize_specs(account(), condition, 1000)

def test_live_account_never_accepted():
    data = account()
    data["is_demo"] = False
    with pytest.raises(ValueError, match="Demo"):
        normalize_specs(data, specification(), 1000)

@pytest.mark.parametrize("units,result", [(2.49, (2.0, 0.02)), (0.99, None), (1.0, (1.0, 0.01)), (30000, (20000.0, 200.0))])
def test_decimal_lot_floor_min_max_preserves_risk_cap(units, result):
    assert lot_size(units, normalize_specs(account(), specification(), 1000)) == result

def test_refresh_persists_sanitized_profile_and_throttles_across_restart():
    calls = []

    class API:
        def account(self):
            calls.append("account")
            return account()

        def condition(self, instrument):
            calls.append(instrument)
            return specification()

    sync_specs(API(), 1000)
    sync_specs(API(), 1001)
    assert calls == ["account", "XAUUSD"]
    with Session() as session:
        assert spec_summary(session, 1002)["status"] == "partial"
        assert spec_summary(session, 5000)["status"] == "stale"
        assert session.get(ForexInstrumentState, (WORKSPACE, "XAUUSD")).snapshot["contract_size"] == "100"

def test_success_refreshes_before_spec_ttl_gap():
    class API:
        def account(self):
            return account()

        def condition(self, instrument):
            return specification()

    sync_specs(API(), 1000)
    with Session() as session:
        assert session.get(ForexInstrumentState, (WORKSPACE, "XAUUSD")).retry_at == 4000

def test_failed_refresh_preserves_profile_but_marks_it_unusable():
    with Session.begin() as session:
        session.add(ForexInstrumentState(
            workspace_id=WORKSPACE, checked_at=1000, retry_at=1000,
            snapshot=normalize_specs(account(), specification(), 1000),
        ))

    class API:
        def account(self):
            raise ExnessError("credential-not-to-leak", retry_after=7200)

    sync_specs(API(), 2000)
    with Session() as session:
        result = spec_summary(session, 2001)
        assert result["status"] == "stale"
        assert result["profile"]["observed_at"] == 1000
        assert "credential-not-to-leak" not in str(result)
        assert session.get(ForexInstrumentState, (WORKSPACE, "XAUUSD")).retry_at == 9200

def test_operator_override_only_when_api_omits_commission(monkeypatch):
    monkeypatch.setenv("EXNESS_COMMISSION_PER_LOT_USD", "0")
    profile = normalize_specs(account(), specification(), 1000)
    assert profile["commission_per_unit"] == 0.0 and profile["commission_source"] == "operator_override"
    condition = specification()
    condition["commission_settings"] = {"commission_type": "per_lot_per_side", "commission_value": {"amount": "3.5", "currency": "USD"}}
    assert normalize_specs(account(), condition, 1000)["commission_per_unit"] == 0.07
    monkeypatch.delenv("EXNESS_COMMISSION_PER_LOT_USD")
    assert normalize_specs(account(), specification(), 1000)["commission_per_unit"] is None

@pytest.mark.parametrize("fee", ["-1", "NaN", "Infinity", "invalid"])
def test_invalid_commission_override_rejected(monkeypatch, fee):
    monkeypatch.setenv("EXNESS_COMMISSION_PER_LOT_USD", fee)
    with pytest.raises(ValueError):
        normalize_specs(account(), specification(), 1000)

def test_nonzero_override_is_round_turn_and_preserves_currency_guard(monkeypatch):
    monkeypatch.setenv("EXNESS_COMMISSION_PER_LOT_USD", "7")
    assert normalize_specs(account(), specification(), 1000)["commission_per_unit"] == 0.07
    data = account()
    data["settings"]["currency"] = "EUR"
    assert normalize_specs(data, specification(), 1000)["commission_per_unit"] is None
    condition = specification()
    condition["commission_settings"] = {"commission_type": "unsupported"}
    assert normalize_specs(account(), condition, 1000)["commission_per_unit"] is None

def test_cache_invalidated_when_account_changes():
    class API:
        account_id = "new-account"
        calls = 0
        def account(self):
            API.calls += 1
            return account()
        def condition(self, _):
            return specification()
    with Session.begin() as session:
        session.query(ForexInstrumentState).delete()
        session.add(ForexInstrumentState(workspace_id=WORKSPACE, checked_at=1000, retry_at=10**10,
                                         snapshot={**normalize_specs(account(), specification(), 1000), "account_tag": "old"}))
    sync_specs(API(), 2000)
    assert API.calls == 1
    sync_specs(API(), 2001)
    assert API.calls == 1


def test_exness_trade_sessions_boundaries_rollover_and_symbol_schedules():
    def eval_at(symbol, sessions, now):
        cond = {**specification(), "instrument": symbol, "trade_sessions": sessions}
        specs = {"status": "partial", "error": None,
                 "profile": normalize_specs(account(), cond, now - 10, account_id="demo", symbol=symbol)}
        return market_session(specs, now, symbol, "demo")

    assert eval_at("XAUUSD", XAU_SESSIONS, utc(18, 22, 1, 29)) == {
        "status": "closed", "reason": "weekend", "as_of": utc(18, 22, 1, 29),
        "observed_at": utc(18, 22, 1, 19), "next_open_at": utc(18, 22, 1, 30),
        "next_close_at": utc(19, 20, 58, 0), "source": "exness_trade_sessions",
    }
    assert eval_at("XAUUSD", XAU_SESSIONS, utc(18, 22, 1, 30))["status"] == "open"
    assert eval_at("XAUUSD", XAU_SESSIONS, utc(18, 23, 59, 59))["next_close_at"] == utc(19, 20, 58, 0)
    assert eval_at("XAUUSD", XAU_SESSIONS, utc(20, 20, 57, 59))["status"] == "open"
    daily_break = eval_at("XAUUSD", XAU_SESSIONS, utc(20, 20, 58, 0))
    assert (daily_break["status"], daily_break["reason"], daily_break["next_open_at"]) == (
        "closed", "session_break", utc(20, 22, 0, 30))
    assert eval_at("XAUUSD", XAU_SESSIONS, utc(20, 22, 0, 29))["status"] == "closed"
    assert eval_at("XAUUSD", XAU_SESSIONS, utc(20, 22, 0, 30))["next_close_at"] == utc(21, 20, 58, 0)
    eur_during_gold_break = eval_at("EURUSD", EUR_SESSIONS, utc(20, 21, 15, 0))
    assert (eur_during_gold_break["status"], eur_during_gold_break["next_close_at"]) == (
        "open", utc(23, 20, 59, 0))
    fri_close = eval_at("EURUSD", EUR_SESSIONS, utc(23, 20, 59, 0))
    assert (fri_close["status"], fri_close["reason"], fri_close["next_open_at"]) == (
        "closed", "weekend", utc(25, 21, 5, 0))


@pytest.mark.parametrize("sessions,delta,error,account_id,symbol,reason", [
    (None, -10, None, "demo", "XAUUSD", "missing_schedule"),
    ([], -10, None, "demo", "XAUUSD", "missing_schedule"),
    ([{"trade_mode": "enabled", "week_start_time": "Bad", "week_end_time": "Sun, 01:00:00"}],
     -10, None, "demo", "XAUUSD", "invalid_schedule"),
    ([{"trade_mode": "enabled", "week_start_time": "Sun, 00:00:00", "week_end_time": "Sun, 02:00:00"},
      {"trade_mode": "trading_disabled", "week_start_time": "Sun, 01:00:00", "week_end_time": "Sun, 03:00:00"}],
     -10, None, "demo", "XAUUSD", "overlapping_schedule"),
    ([{"trade_mode": "enabled", "week_start_time": "Mon, 00:00:00", "week_end_time": "Mon, 01:00:00"}],
     -10, None, "demo", "XAUUSD", "uncovered_time"),
    (XAU_SESSIONS, -3600, None, "demo", "XAUUSD", "stale_schedule"),
    (XAU_SESSIONS, 1, None, "demo", "XAUUSD", "invalid_timestamp"),
    (XAU_SESSIONS, -10, "sync failed", "demo", "XAUUSD", "sync_error"),
    (XAU_SESSIONS, -10, None, "other", "XAUUSD", "identity_mismatch"),
    (XAU_SESSIONS, -10, None, "demo", "EURUSD", "identity_mismatch"),
])
def test_market_session_fails_closed_to_unknown_without_breaking_cost_specs(
    sessions, delta, error, account_id, symbol, reason
):
    now = utc(20, 12, 0, 0)
    profile = normalize_specs(account(), {**specification(), "trade_sessions": sessions},
                              now + delta, account_id="demo", symbol="XAUUSD")
    assert profile["contract_size"] == "100"
    result = market_session({"status": "partial", "error": error, "profile": profile}, now, symbol, account_id)
    assert (result["status"], result["reason"]) == ("unknown", reason)


def test_dashboard_and_markets_preserve_schedule_when_fx_conversion_or_commission_unverified(monkeypatch):
    from fastapi.testclient import TestClient

    from backend.app import main
    monkeypatch.setenv("EXNESS_ACCOUNT_ID", "demo")
    monkeypatch.setenv("EXNESS_SYMBOLS", "XAUUSD,USDJPY")
    now = utc(20, 21, 15, 0)
    monkeypatch.setattr(main.time, "time", lambda: now)
    main._dashboard_cache.clear()
    with Session.begin() as session:
        for symbol, quote_ccy, sessions in (("XAUUSD", "USD", XAU_SESSIONS), ("USDJPY", "JPY", EUR_SESSIONS)):
            cond = {**specification(), "instrument": symbol, "quote_currency": quote_ccy, "trade_sessions": sessions}
            session.add(ForexInstrumentState(
                workspace_id=WORKSPACE, symbol=symbol, checked_at=now - 5, retry_at=now + 3600,
                snapshot=normalize_specs(account(), cond, now - 5, account_id="demo", symbol=symbol),
            ))
    with TestClient(main.app) as client:
        dash = client.get("/api/workspaces/exness_forex/dashboard?symbol=USDJPY").json()
        assert dash["instrument"]["profile"] is None
        assert dash["market_session"]["status"] == "open"
        rows = {row["symbol"]: row for row in client.get("/api/workspaces/exness_forex/markets").json()["markets"]}
        assert rows["XAUUSD"]["market_session"]["status"] == "closed"
        assert rows["XAUUSD"]["market_session"]["reason"] == "session_break"
        assert rows["USDJPY"]["market_session"]["status"] == "open"
        assert rows["USDJPY"]["blocked_reason"] is not None
