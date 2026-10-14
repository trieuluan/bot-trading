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


def _client_and_inputs():
    import time
    from unittest.mock import MagicMock
    from backend.app.exness_demo_client import ExnessDemoClient
    cond = {**COND, "instrument": "XAUUSD", "trade_mode": "enabled", "exec_mode": "market", "quote_currency": "USD"}
    now = time.time()
    return ExnessDemoClient(MagicMock()), cond, {"bid": 2600.0, "ask": 2600.4, "time": now, "received_at": now}

def test_structural_stop_adds_buffer_and_rounds_outward(monkeypatch):
    monkeypatch.setenv("EXNESS_DEMO_MAX_LOT", "0.10")
    monkeypatch.setenv("EXNESS_DEMO_RISK_PER_TRADE_USD", "25")
    client, cond, quote = _client_and_inputs()
    long = client.strategy_body({"side": "long", "stop": 2595.0, "stop_buffer": 0.8}, cond, quote, "k1")
    short = client.strategy_body({"side": "short", "stop": 2605.0, "stop_buffer": 0.8}, cond, quote, "k2")
    spread = client.strategy_body({"side": "long", "stop": 2595.0, "stop_buffer": 0.1}, cond, quote, "k3")
    genome = client.strategy_body({"side": "long", "stop": 2595.0, "stop_buffer": 0.8, "generated": True}, cond, quote, "k4")
    assert Decimal(long["stop_loss_price"]) == Decimal("2594.200")
    assert Decimal(short["stop_loss_price"]) == Decimal("2605.800")
    assert Decimal(spread["stop_loss_price"]) == Decimal("2594.600")
    assert Decimal(genome["stop_loss_price"]) == Decimal("2595.000")

def test_sl_buffer_uses_true_range_including_previous_close():
    from backend.app.exness_management import structure_stop_buffer
    rows = [bar(index * 900, 100, 101, 99, 100) for index in range(15)]
    rows[-1] = bar(14 * 900, 110, 111, 109, 110)
    assert structure_stop_buffer(rows) == pytest.approx((13 * 2 + 11) / 14 * 0.25)
    with pytest.raises(ExnessError, match="15 closed"):
        structure_stop_buffer(rows[-14:])

@pytest.mark.parametrize("buffer", [-1, float("nan"), float("inf"), True])
def test_invalid_sl_buffer_rejected(buffer):
    client, cond, quote = _client_and_inputs()
    with pytest.raises(ExnessError):
        client.strategy_body({"side": "long", "stop": 2595, "stop_buffer": buffer}, cond, quote, "bad")
