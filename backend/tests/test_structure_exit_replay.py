from backend.app.market import Candle
from scripts.structure_exit_replay import settle_boundary, structure_exit


def history(highs, closes):
    return [Candle(hour * 3600 + part * 900, 100, high, 99, close, 1)
            for hour, (high, close) in enumerate(zip(highs, closes)) for part in range(4)]


def test_structure_exit_waits_for_hour_close_and_confirmed_pivot():
    rows = history([101, 102, 105, 102, 101, 103, 104, 106], [100] * 7 + [105.5])
    position = dict(side="short", entry_time=6 * 3600)
    assert structure_exit(position, rows[:-1]) is None
    assert structure_exit(position, rows) == "h1_structure_1close"
    assert structure_exit(position, rows[:12]) is None


def test_structure_exit_two_distinct_hours_and_frozen_level():
    rows = history([101, 102, 105, 102, 101, 103, 104, 106, 107], [100] * 7 + [105.5, 105.8])
    position = dict(side="short", entry_time=6 * 3600)
    assert structure_exit(position, rows[:32], 2) is None
    assert position["structure_pending"]["price"] == 105
    assert structure_exit(position, rows[:33], 2) is None
    assert structure_exit(position, rows, 2) == "h1_structure_2close"


def test_structure_exit_reclaim_resets_and_long_is_symmetric():
    rows = history([101, 102, 105, 102, 101, 103, 104, 106, 107], [100] * 7 + [105.5, 104])
    position = dict(side="short", entry_time=6 * 3600)
    assert structure_exit(position, rows[:32], 2) is None
    assert structure_exit(position, rows, 2) is None
    assert "structure_pending" not in position
    mirrored = [Candle(row.time, 210-row.open, 210-row.low, 210-row.high,
                       210-row.close, row.volume) for row in rows[:32]]
    assert structure_exit(dict(side="long", entry_time=6 * 3600), mirrored) == "h1_structure_1close"


def test_boundary_settlement_retains_unresolved_trades_and_costs():
    from backend.app import futures_learner as learner
    rows = [Candle(index * 900, 100, 100.1, 99.9, 100, 1) for index in range(4)]
    position = dict(status="open", entry=None, side="long", stop=98, target=104, exit_policy="fixed")
    result = settle_boundary(position, rows, rows, [], [], 0, 3600, lambda index: rows[:index+1])
    assert result["status"] == "resolved" and result["exit_reason"] == "boundary_mark"
    assert result["net_r"] < 0
    assert result["label_available_at"] == 3600
    assert learner.discretionary_exit.__module__ == "backend.app.futures_exits"
