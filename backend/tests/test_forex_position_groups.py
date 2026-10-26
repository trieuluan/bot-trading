from backend.app.db import ExnessExecutionTrade
from backend.app.forex_position_groups import (
    group_page,
    open_setup_count,
    position_groups,
    ticket_owners,
)


def test_position_groups_groups_bot_legs_and_keeps_isolated():
    trade = ExnessExecutionTrade(
        account="acc-1", symbol="XAUUSD", signal_time=1700000000,
        snapshot={"signal": {"strategy": "hast4_alpha"}, "legs": [
            {"role": "tp1", "position_id": "101", "body": {"side": "buy"}},
            {"role": "runner", "position_id": "102", "body": {"side": "buy"}}
        ]}
    )
    owners = ticket_owners([trade])
    rows = [
        {"symbol": "XAUUSD", "position_id": "101", "side": "buy", "status": "closed",
         "initial_volume": 0.05, "remaining_volume": 0.0, "open_price": 2700.0,
         "open_time": 1700000005, "pnl": 25.0, "has_closes": True, "source": "learning",
         "strategy": "hast4_alpha", "signal_time": 1700000000, "role": "tp1",
         "take_profit_filled": True, "ownership_verified": True, "setup_id": "setup:acc-1:XAUUSD:1700000000"},
        {"symbol": "XAUUSD", "position_id": "102", "side": "buy", "status": "open",
         "initial_volume": 0.05, "remaining_volume": 0.05, "open_price": 2700.0,
         "open_time": 1700000005, "pnl": 0.0, "has_closes": False, "source": "learning",
         "strategy": "hast4_alpha", "signal_time": 1700000000, "role": "runner",
         "take_profit_filled": False, "ownership_verified": True, "setup_id": "setup:acc-1:XAUUSD:1700000000"},
        {"symbol": "XAUUSD", "position_id": "999", "side": "sell", "status": "closed",
         "initial_volume": 0.02, "remaining_volume": 0.0, "open_price": 2710.0,
         "open_time": 1699990000, "pnl": 10.0, "has_closes": True, "source": "manual",
         "strategy": None, "signal_time": None, "role": "runner",
         "take_profit_filled": False, "ownership_verified": False, "setup_id": None},
    ]
    groups = position_groups(rows)
    assert len(groups) == 2
    setup = next(g for g in groups if g["id"] == "setup:acc-1:XAUUSD:1700000000")
    assert setup["status"] == "open"
    assert setup["open_count"] == 1
    assert setup["ticket_count"] == 2
    assert setup["initial_volume"] == 0.10
    assert setup["remaining_volume"] == 0.05
    assert setup["tp1_taken"] is True
    assert setup["realized_pnl"] == 25.0

    manual = next(g for g in groups if g["id"] == "ticket:XAUUSD:999")
    assert manual["status"] == "closed"
    assert manual["ticket_count"] == 1
    assert manual["ownership_verified"] is False

    # Pagination & filters
    paged = group_page(groups, offset=0, limit=1, status="open")
    assert len(paged["groups"]) == 1
    assert paged["groups"][0]["id"] == setup["id"]
    assert paged["total"] == 1
    assert paged["has_more"] is False
    assert len(paged["open_groups"]) == 1

    # Setup count
    open_positions = [{"instrument": "XAUUSD", "position_id": "102", "direction": "buy"}]
    assert open_setup_count("acc-1", open_positions, owners) == 1

    # External position added
    open_positions.append({"instrument": "XAUUSD", "position_id": "888", "direction": "sell"})
    assert open_setup_count("acc-1", open_positions, owners) == 2


def test_incomplete_history_does_not_mark_closed():
    rows = [
        {"symbol": "EURUSD", "position_id": "201", "side": "buy", "status": "history_incomplete",
         "initial_volume": None, "remaining_volume": None, "open_price": 1.08,
         "open_time": 1700000000, "pnl": None, "has_closes": False, "source": "external",
         "strategy": None, "signal_time": None, "role": "runner",
         "take_profit_filled": False, "ownership_verified": False, "setup_id": None}
    ]
    groups = position_groups(rows)
    assert len(groups) == 1
    assert groups[0]["status"] == "history_incomplete"
    assert groups[0]["history_incomplete"] is True

def test_missing_position_ids_are_counted_independently():
    positions = [{"instrument": "XAUUSD"}, {"instrument": "XAUUSD", "position_id": None}]
    assert open_setup_count("acc-1", positions, {}) == 2
