import pytest

from backend.app.db import ForexInstrumentState, Session
from backend.app.exness import ExnessError
from backend.app.exness_feed import WORKSPACE
from backend.app.forex_costs import lot_size, normalize_specs, spec_summary, sync_specs

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
        assert session.get(ForexInstrumentState, WORKSPACE).snapshot["contract_size"] == "100"

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
        assert session.get(ForexInstrumentState, WORKSPACE).retry_at == 9200
