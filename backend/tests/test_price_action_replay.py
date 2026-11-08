from backend.app.futures_learner import FEE, SLIP
from backend.app.market import Candle
from scripts.price_action_replay import bucket, h1_swing_stop, trade

def bar(index, low, high, close=None):
    return Candle(index * 900, 100, high, low, close or 100, 1)

def test_trade_stop_first_on_shared_bar_and_target_net_of_costs():
    shared = trade([bar(0, 98, 103)], 0, 1, 99, 2.0)
    assert shared[1] == "stop" and shared[0] < -1
    entry = 100 * (1 + SLIP)
    target = entry + 2 * (entry - 99)
    win = trade([bar(0, 99.5, 100.5), bar(1, 100, target + 1)], 0, 1, 99, 2.0)
    exit_price = target * (1 - SLIP)
    assert win[1] == "target"
    assert abs(win[0] - (exit_price - entry - (entry + exit_price) * FEE) / (entry - 99)) < 1e-12

def test_bucket_drops_incomplete_higher_timeframe_bar():
    rows = [bar(index, 99, 101) for index in range(7)]
    assert [row.time for row in bucket(rows, 3600)] == [0]

def test_h1_swing_stop_respects_atr_floor_and_swing_distance():
    window = [bar(index, 95, 105) for index in range(5)]
    assert h1_swing_stop(100, 1, window, 2.0) == 94.5
    tight = [bar(index, 99.5, 100.5) for index in range(5)]
    assert h1_swing_stop(100, 1, tight, 2.0) == 98.0

def test_exit_r_break_even_after_one_r_and_half_partial():
    from scripts.price_action_exits import exit_r
    rows = [Candle(0, 100, 100.5, 99.5, 100, 1), Candle(900, 100, 102, 99.9, 100, 1),
            Candle(1800, 100, 100, 99, 100, 1)]
    be = exit_r(rows, 0, 1, 99, "be1_t2")
    assert -0.2 < be < 0  # stopped at entry: only costs
    half = exit_r(rows, 0, 1, 99, "half1_t3")
    assert 0.3 < half < 0.5  # half banked at 1R, rest stopped at entry

def test_gene_audit_splits_htf_slots():
    from scripts.futures_gene_audit import genes
    genome = {"entry_policy": "body50", "exit_policy": "adaptive", "rule": {"op": "all", "children": [
        {"op": "all", "children": [{"feature": "trend", "frame": "H4"}, {"feature": "swing_structure", "frame": "H4"},
                                   {"feature": "market_alignment", "frame": "H4"}]},
        {"feature": "session_vwap", "frame": "H1"}, {"feature": "pin_bar", "frame": "M15"}]}}
    assert genes(genome) == [("entry", "body50"), ("exit", "adaptive"), ("h1", "session_vwap"),
                             ("h4_confirm", "market_alignment"), ("h4_confirm", "swing_structure"),
                             ("m15", "pin_bar")]

def test_gene_ablation_candidates_valid():
    from scripts.futures_gene_ablation import candidates
    pop = candidates()
    assert "dryup:orig" in pop and "dryup:swap_breakout" in pop
    assert "exit_0:adaptive" in pop and "exit_0:fixed" in pop

def test_limit_trade_unfilled_zero_and_fill_bar_stop():
    from scripts.price_action_limit_loc import limit_trade
    rows = [bar(0, 99, 101), bar(1, 100.2, 102), bar(2, 100.3, 102), bar(3, 100.4, 102), bar(4, 100.5, 102)]
    assert limit_trade(rows, 1, 1, 98, 100) == 0.0
    stopped = [bar(0, 99, 101), bar(1, 97.5, 105)]
    assert limit_trade(stopped, 1, 1, 98, 100) < -1.0

def test_htf_run_trade_respects_stop_and_trail():
    from scripts.htf_replay import run_trade
    rows = [bar(0, 99, 101), bar(1, 99.5, 104, 103.5), bar(2, 101, 102, 101.2)]
    trailed = run_trade(rows, 0, 1, 98, "trail", [1.0, 1.0, 1.0], 10, 0.0, 0.0)
    assert trailed[0] > 0.7  # bar 1 close=103.5 ratchets stop to 101.5; bar 2 low=101 hits trailed stop in profit

def test_entry_timing_paired_waits_for_hour_and_tracks_cost_components():
    from backend.app import futures_learner as learner
    from scripts.entry_timing_paired import measured_finish, next_hour, simulate, wait_valid
    assert next_hour(3600) == 3600 and next_hour(900) == 3600
    assert wait_valid([bar(1, 97, 101)], 0, 1, 1, 98, 105) == "stop_before_entry"
    assert wait_valid([bar(1, 99, 106)], 0, 1, 1, 98, 105) == "target_before_entry"
    assert wait_valid([bar(1, 97, 101)], 0, 0, 1, 98, 105) is None
    original = learner.finish
    learner.finish = lambda s, d, p, w, b: measured_finish(s, d, p, w, b, original)
    try:
        rows = [bar(0, 99, 101), bar(1, 100, 105)]
        sample = dict(status="open", entry=None, side="long", stop=98.0, target=104.0, exit_policy="fixed")
        done = simulate(sample, rows, rows, [], [], 0, 3600, lambda i: rows[:i + 1])
    finally:
        learner.finish = original
    assert abs(done["raw_gross_r"] - (done["net_r"] + done["fee_r"] + done["slip_r"] + done["funding_r"])) < 1e-12
    expected_entry = 100 * (1 + learner.SLIP)
    expected_exit = 104 * (1 - learner.SLIP)
    assert abs(done["raw_gross_r"] - 4 / (expected_entry - 98)) < 1e-12
    assert abs(done["fee_r"] - (expected_entry + expected_exit) * learner.FEE / done["risk"]) < 1e-12
    assert done["funding_r"] == 0

def test_entry_timing_finish_accounts_for_partial_funding_and_short_slip():
    from backend.app import futures_learner as learner
    from scripts.entry_timing_paired import measured_finish
    for direction, side in ((1, "long"), (-1, "short")):
        entry = 100 * (1 + direction * learner.SLIP)
        partial = 101 * (1 - direction * learner.SLIP)
        final = 102 * (1 - direction * learner.SLIP)
        sample = dict(raw_entry=100, entry=entry, risk=2, side=side,
                      quantity=0.5, partial_exits=[dict(price=partial, quantity=0.5)],
                      realized_gross=direction * (partial - entry) * 0.5,
                      realized_exit_fee=partial * 0.5 * learner.FEE, funding_cost=0.01)
        measured_finish(sample, direction, 102, "target", bar(0, 99, 103), learner.finish)
        assert abs(sample["raw_gross_r"] - direction * 0.75) < 1e-12
        assert abs(sample["fee_r"] - learner.FEE * (entry + partial * 0.5 + final * 0.5) / 2) < 1e-12
        assert sample["funding_r"] == 0.005

def test_exit_tuning_custom_be_and_partial_without_weak_close():
    from scripts.exit_tuning_replay import custom_partial_size, custom_protective_stop
    strong_candles = [Candle(index * 900, 100, 101.6, 100, 101.6, 1) for index in range(25)]
    pos = dict(side="long", entry=100.0, risk=2.0, stop=98.0, target=104.0,
               exit_policy="adaptive", quantity=2.0, original_quantity=2.0, entry_fee=0.0, funding_cost=0.0)
    assert custom_protective_stop(pos, strong_candles, 0.0, 0.0, 1.0) == 98.0
    assert custom_protective_stop(pos, strong_candles, 0.0, 0.0, 0.75) == 100.0
    at_one_r = [Candle(index * 900, 100, 102.2, 100, 102.1, 1) for index in range(25)]
    assert custom_partial_size(pos, at_one_r, False) == 0.0
    assert custom_partial_size(pos, at_one_r, True) == 1.0

def test_exit_tuning_short_be_covers_costs_and_partial_does_not_repeat():
    from scripts.exit_tuning_replay import custom_partial_size, custom_protective_stop
    candles = [Candle(index * 900, 100, 100, 98.4, 98.4, 1) for index in range(25)]
    pos = dict(side="short", entry=100.0, risk=2.0, stop=102.0, target=96.0,
               exit_policy="adaptive", quantity=2.0, original_quantity=2.0,
               entry_fee=0.16, funding_cost=0.02)
    updated = custom_protective_stop(pos, candles, 0.0008, 0.0002, 0.75)
    assert 98.4 < updated < 100.0
    exit_fill = updated * 1.0002
    assert abs((100 - exit_fill) * 2 - 0.16 - exit_fill * 2 * 0.0008 - 0.02) < 1e-10
    pos.update(partial_exits=[dict(quantity=1.0)], quantity=1.0)
    assert custom_partial_size(pos, [Candle(0, 100, 100, 97.8, 97.8, 1)], True) == 0.0

def test_exit_tuning_be_activates_after_close_not_inside_same_bar(monkeypatch):
    from backend.app import futures_learner as learner
    from scripts.entry_timing_paired import simulate
    from scripts.exit_tuning_replay import custom_protective_stop
    monkeypatch.setattr(learner, "FEE", 0.0)
    monkeypatch.setattr(learner, "SLIP", 0.0)
    monkeypatch.setattr(learner, "protective_stop", lambda pos, candles, fee, slip:
                        custom_protective_stop(pos, candles, fee, slip, 0.75))
    rows = [Candle(0, 100, 101.8, 99.0, 101.6, 1),
            Candle(900, 101.6, 101.7, 99.9, 100.5, 1)]
    pos = dict(status="open", entry=None, side="long", stop=98.0, target=104.0, exit_policy="adaptive")
    done = simulate(pos, rows, rows, [], [], 0, 1800, lambda index: rows[:index + 1])
    assert done["exit_reason"] == "stop" and done["net_r"] == 0.0
    assert done["label_available_at"] == 1800

def test_exit_tuning_timeout_and_dataset_censoring_are_distinct(monkeypatch):
    from backend.app import futures_learner as learner
    from scripts.entry_timing_paired import simulate
    rows = [Candle(index * 900, 100, 100.1, 99.9, 100, 1) for index in range(4)]
    pos = dict(status="open", entry=None, side="long", stop=98.0, target=104.0, exit_policy="fixed")
    monkeypatch.setattr(learner, "MAX_HOLD", 900)
    done = simulate(pos, rows, rows, [], [], 0, 3600, lambda index: rows[:index + 1])
    assert done["status"] == "resolved" and done["exit_reason"] == "timeout"
    monkeypatch.setattr(learner, "MAX_HOLD", 999999 * 900)
    censored = simulate(pos, rows, rows, [], [], 0, 3600, lambda index: rows[:index + 1])
    assert censored["status"] == "excluded" and censored["exit_reason"] == "dataset_boundary"
