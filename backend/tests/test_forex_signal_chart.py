import copy
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from backend.app import forex_learning
from backend.app.forex_entry_policy import tree_id, with_entry
from backend.app.forex_htf_v2 import g0_trees


@pytest.fixture
def snapshot(monkeypatch):
    end = 1792584000
    tree = with_entry(g0_trees("EURUSD")[0])
    strategy = tree_id(tree)
    signal = {"strategy": strategy, "symbol": "EURUSD", "side": "long", "available_at": end,
              "stop": 1.08, "target": 1.12, "ref_price": 1.10,
              "htf_structure": {"level": 1.095, "invalidation": 1.085, "target_reason": "H1 swing",
                                "setup_time": end - 7200, "confirmed_h1": end - 3600},
              "entry_policy": {"limit_price": 1.098, "stop_price": 1.102, "expires_at": end + 3600}}
    sample = {"strategy": strategy, "tree": tree, "signal": signal, "recorded_at": end + 5,
              "status": "pending", "side": "long"}
    def ledger(account, symbol):
        assert account == "chart-test" and symbol == "EURUSD"
        return {"samples": [sample]}
    monkeypatch.setenv("EXNESS_ACCOUNT_ID", "chart-test")
    monkeypatch.setattr("backend.app.forex_entry_pilot.read_ledger", ledger)
    frames = {}
    for frame, step in (("15m", 900), ("1h", 3600), ("4h", 14400)):
        frames[frame] = [SimpleNamespace(time=end - step * (80 - index), price_type="bid",
                                        open=1.1, close=1.1, high=1.11, low=1.09, tick_volume=100)
                         for index in range(81)]
    def load(session, now, symbol, *, window):
        assert now == end and symbol == "EURUSD" and window is forex_learning.TICK_WINDOW
        return frames
    monkeypatch.setattr(forex_learning, "load_frames", load)
    monkeypatch.setattr(forex_learning, "read_ledger", lambda *_: pytest.fail("Must not read current V3 parent"))
    return end, sample, frames


def test_snapshot_uses_frozen_tree_closed_frames_and_virtual_window(snapshot):
    end, sample, frames = snapshot
    original = copy.deepcopy(sample)
    chart = forex_learning.signal_chart(sample["strategy"], end, "EURUSD", now=end + 10)
    row = chart["row"]
    assert row["chart"]["parameters"] == sample["tree"]
    assert row["captured_zone"]["source"] == "v4_pilot"
    assert row["captured_zone"]["level"] == 1.095
    assert len(row["chart"]["levels"]) == 4
    assert all(level["from"] == end and level["to"] == end + 3600 for level in row["chart"]["levels"])
    assert all(point["time"] <= end for line in row["chart"]["overlays"]["lines"] for point in line["points"])
    for rows in frames.values():
        rows[-1].close = 999999
    assert forex_learning.signal_chart(sample["strategy"], end, "EURUSD", now=end + 10) == chart
    assert sample == original


@pytest.mark.parametrize("change,status", [("future", 404), ("recorded_future", 404),
                                           ("strategy", 404), ("symbol", 409), ("tree", 409), ("missing", 409)])
def test_snapshot_rejects_invisible_mismatched_or_missing_data(snapshot, change, status):
    end, sample, frames = snapshot
    strategy, now = sample["strategy"], end + 10
    if change == "future":
        now = end - 1
    elif change == "recorded_future":
        sample["recorded_at"] = now + 1
    elif change == "strategy":
        strategy = "hast4_unknown"
    elif change == "symbol":
        sample["signal"]["symbol"] = "XAUUSD"
    elif change == "tree":
        sample["tree"] = with_entry(g0_trees("EURUSD")[1])
    else:
        frames["15m"] = []
    with pytest.raises(HTTPException) as error:
        forex_learning.signal_chart(strategy, end, "EURUSD", now=now)
    assert error.value.status_code == status
