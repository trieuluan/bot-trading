from decimal import Decimal
from types import SimpleNamespace

import pytest

from backend.app.exness import ExnessError
from backend.app.exness_management import exit_reason, protective_stop, size_lot, split_bodies

COND = {"volume_min": "0.01", "volume_max": "100", "volume_step": "0.01",
        "contract_size": "100", "point_digits": 3}

def bar(time, open_, high, low, close):
    return SimpleNamespace(time=time, open=open_, high=high, low=low, close=close)

def test_lot_scales_by_risk_and_caps(monkeypatch):
    monkeypatch.setenv("EXNESS_DEMO_MAX_LOT", "0.10")
    monkeypatch.setenv("EXNESS_DEMO_RISK_PER_TRADE_USD", "25")
    assert size_lot(Decimal("20"), COND) == Decimal("0.01")
    assert size_lot(Decimal("5"), COND) == Decimal("0.05")
    assert size_lot(Decimal("1"), COND) == Decimal("0.10")
    with pytest.raises(ExnessError):
        size_lot(Decimal("26"), COND)

def test_max_lot_setting_cannot_exceed_requested_ceiling(monkeypatch):
    monkeypatch.setenv("EXNESS_DEMO_MAX_LOT", "1")
    with pytest.raises(ExnessError):
        size_lot(Decimal("1"), COND)

def test_split_preserves_total_volume_and_sets_tp1():
    parts = split_bodies({"side": "buy", "volume": "0.05", "stop_loss_price": "99",
                          "take_profit_price": "102"}, COND, Decimal("100"))
    assert [part["volume"] for part in parts] == ["0.02", "0.03"]
    assert parts[0]["take_profit_price"] == "101.000" and parts[1]["take_profit_price"] == "102"
    assert split_bodies({"side": "buy", "volume": "0.01", "stop_loss_price": "99"}, COND, Decimal("100"))[0]["role"] == "runner"

def test_breakeven_and_tighten_only():
    position = {"open_price": "100", "direction": "buy"}
    order = {"stop_loss_price": "99"}
    assert protective_stop(position, "99", {"bid": 100.5, "ask": 100.6}, order, COND, []) is None
    assert protective_stop(position, "99", {"bid": 101.1, "ask": 101.2}, order, COND, [])["stop"] == "100.000"
    assert protective_stop(position, "99", {"bid": 101.1, "ask": 101.2}, {"stop_loss_price": "100"}, COND, []) is None

def test_trailing_uses_recent_structure_without_loosening():
    rows = [bar(i * 900, 100, 101, 99.5, 100.5) for i in range(20)]
    rows[-3:] = [bar(1, 101, 102, 100.7, 101.8), bar(2, 101.8, 102.4, 101.1, 102.2), bar(3, 102.2, 102.8, 101.5, 102.7)]
    action = protective_stop({"open_price": "100", "direction": "buy"}, "99",
                             {"bid": 102.7, "ask": 102.8}, {"stop_loss_price": "100"}, COND, rows)
    assert action["stage"] == "trailing" and Decimal(action["stop"]) > Decimal("100")

def test_early_exit_requires_new_adverse_close_after_entry():
    rows = [bar(i * 900, 100 + i, 101 + i, 99 + i, 100.5 + i) for i in range(22)]
    rows[-1] = bar(21 * 900, 120, 120.2, 110, 111)
    assert exit_reason("buy", 21 * 900, rows) == "early_risk_exit"
    assert exit_reason("buy", 22 * 900, rows) is None
    assert exit_reason("sell", 21 * 900, rows) is None
