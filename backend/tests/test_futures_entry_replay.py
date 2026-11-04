from backend.app.strategy import Candle
from scripts.futures_entry_replay import advance_entry, entry_level, new_sample


class Setup:
    side, stop, target = "long", 90.0, 120.0

def sample(arm):
    item = new_sample("s", Setup, {"rule": {}}, 0, 10.0)
    item["level"] = 98.0
    return item

def test_body_levels_and_invalid_levels():
    bar = Candle(0, 100, 106, 99, 104, 1)
    assert entry_level("body50", [bar], "long", 90) == 102
    assert entry_level("body50", [bar], "long", 103) is None
    assert entry_level("body38", [bar], "short", 104) is None
    assert entry_level("ema20_m15", [bar], "long", 90) is None

def test_limit_expires_without_trade_through():
    item = sample("body38")
    advance_entry(item, "body38", Candle(900, 100, 101, 98, 100, 1), None, [], [])
    assert item["status"] == "open" and item["entry"] is None
    advance_entry(item, "body38", Candle(900 + 4 * 900, 100, 101, 99, 100, 1), None, [], [])
    assert item["exit_reason"] == "expired"

def test_retest_cancels_when_close_back_through_level():
    item = sample("retest_h1")
    advance_entry(item, "retest_h1", Candle(900, 100, 101, 97, 97.5, 1), None, [], [])
    assert item["exit_reason"] == "failed_retest"

def test_paired_value_credits_maker_fee_saving():
    from scripts.futures_entry_paired import FEE, value
    s = {"status": "resolved", "net_r": -1.0, "risk": 5.0, "reference_risk": 10.0,
         "entry": 100.0, "arm": "body50"}
    assert value(s, FEE) == -0.5
    assert abs(value(s, 0.0) - (-0.5 + 100 * FEE / 10)) < 1e-12
    assert value({"status": "cancelled"}, 0.0) == 0.0

def test_entry_policy_gene_validates_mutates_and_seeds_one_body50_g0():
    import random

    from backend.app.futures_strategies import (
        ENTRY_SEEDS,
        RULE_SEEDS,
        crossover,
        genome_name,
        mutate,
        shadow_only,
        validate_genome,
    )
    seed = ENTRY_SEEDS[0]
    validate_genome(seed)
    assert seed["entry_policy"] == "body50_confirm" and seed["rule"] == RULE_SEEDS[0]["rule"]
    assert genome_name(seed) != genome_name(RULE_SEEDS[0]) and not shadow_only(seed)
    assert not shadow_only(RULE_SEEDS[0])
    assert crossover(RULE_SEEDS[1], seed)["entry_policy"] == "body50_confirm"
    seen = {mutate(RULE_SEEDS[0], random.Random(i)).get("entry_policy", "market") for i in range(400)}
    assert {"body50_confirm", "retest_h1"} <= seen
    try:
        validate_genome({**seed, "entry_policy": "bogus"})
    except ValueError:
        return
    raise AssertionError

def test_learner_limit_fill_and_unfilled_scores_zero():
    from backend.app.futures_learner import advance
    item = sample("body50")
    item.update(entry_policy="body50", first_entry_time=900)
    advance(item, Candle(900, 100, 101, 99, 100, 1), Candle(900, 100, 101, 99, 100, 1), [])
    assert item["entry"] is None and item["status"] == "open"
    fill = Candle(1800, 100, 101, 97, 100, 1)
    advance(item, fill, fill, [])
    assert item["entry"] == 98.0
    miss = sample("body50")
    miss.update(entry_policy="body50", first_entry_time=900)
    for n in range(1, 6):
        bar = Candle(n * 900, 100, 101, 99.5, 100, 1)
        advance(miss, bar, bar, [])
    assert miss["status"] == "resolved" and miss["net_r"] == 0.0 and miss["entry"] is None

def test_confirm_entries_cannot_qualify_from_unlabeled_windows():
    from backend.app.futures_learner import WINDOW_SECONDS, qualification
    from backend.app.futures_strategies import ENTRY_SEEDS
    candidate = {"genome": ENTRY_SEEDS[0], "born_at": 0, "windows": [{
        "start_time": 0, "end_time": WINDOW_SECONDS, "gains": 200, "losses": 1,
        "trade_count": 100, "net_pnl": 199, "max_drawdown_percent": 1}]}
    result = qualification(candidate)
    assert not result["eligible"] and not result["checks"]["entry_windows_supported"]
    assert all(value for key, value in result["checks"].items() if key != "entry_windows_supported")


def test_sweep_confirms_on_wick_through_level_and_cancels_on_close_through():
    item = sample("sweep_h1")
    # bar wicks through 98.0 (low 97.5) but closes back on trend side (100.5 > 98.0)
    advance_entry(item, "sweep_h1", Candle(900, 100, 101, 97.5, 100.5, 1), None, [], [])
    assert item.get("confirmed") and item["entry_time"] == 1800

    item_fail = sample("sweep_h1")
    # bar closes through 98.0 (close 97.0 < 98.0)
    advance_entry(item_fail, "sweep_h1", Candle(900, 100, 101, 96, 97.0, 1), None, [], [])
    assert item_fail["exit_reason"] == "closed_through_level"

def test_confirm_entries_qualify_from_matching_windows():
    from backend.app.futures_learner import WINDOW_SECONDS, qualification
    from backend.app.futures_strategies import ENTRY_SEEDS
    window = {"start_time": 0, "end_time": WINDOW_SECONDS, "gains": 200, "losses": 100,
              "trade_count": 100, "net_pnl": 99, "max_drawdown_percent": 1,
              "entry_policy": "body50_confirm"}
    assert qualification({"genome": ENTRY_SEEDS[0], "born_at": 0, "windows": [window]})["eligible"]
    assert not qualification({"genome": ENTRY_SEEDS[0], "born_at": 0,
                              "windows": [{**window, "entry_policy": "market"}]})["eligible"]

def test_validation_replays_confirmation_before_next_open(monkeypatch):
    from backend.app import futures_learner as learner
    from backend.app.futures_strategies import ENTRY_SEEDS
    from backend.app.strategy import Setup

    monkeypatch.setattr(learner, "WARMUP", 1)
    monkeypatch.setattr(learner, "WINDOW_BARS", 4)
    monkeypatch.setattr(learner, "WINDOW_SECONDS", 3600)
    monkeypatch.setattr(learner, "strategy_setup", lambda genome, history, funding, **kwargs:
                        Setup("long", 104, 90, 120, "test") if history[-1].time == 900 else None)
    bars = [Candle(0, 100, 101, 99, 100, 1), Candle(900, 100, 105, 99, 104, 1),
            Candle(1800, 102.5, 105, 101, 104, 1), Candle(2700, 105, 106, 104, 105, 1),
            Candle(3600, 105, 121, 104, 120, 1)]
    contract = {"contractSize": .001, "volUnit": 1, "minVol": 1, "maxVol": 100000000}
    captured = []
    simulate = learner.simulate

    def recording_simulate(*args, **kwargs):
        result = simulate(*args, **kwargs)
        captured.append(result)
        return result

    monkeypatch.setattr(learner, "simulate", recording_simulate)
    candidate = {"genome": ENTRY_SEEDS[0], "born_at": 900, "windows": [], "promoted": False}
    learner.validate_window(candidate, bars, bars, [{"time": 2700, "rate": .01}], contract)
    trade, = captured[-1]["trades"]
    assert trade["signal_time"] == 900 and trade["entry_time"] == 2700
    assert trade["entry"] == 105 * 1.0002 and trade["exit_reason"] == "target"
    assert trade["fees"] > 0 and trade["funding_cost"] == 0
    assert candidate["windows"][0]["entry_policy"] == "body50_confirm"
    assert candidate["windows"][0]["trade_count"] == 1

    for invalid in (Candle(1800, 102.5, 105, 89, 104, 1),
                    Candle(1800, 102.5, 121, 101, 104, 1),
                    Candle(1800, 104, 105, 103, 104, 1),
                    Candle(1800, 102.5, 105, 101, 101.5, 1)):
        changed = [*bars[:2], invalid, *bars[3:]]
        candidate.update(windows=[], promoted=False)
        learner.validate_window(candidate, changed, changed, [], contract)
        assert candidate["windows"][0]["trade_count"] == 0

def test_confirmation_expires_without_replacing_pending_signal():
    from backend.app.backtest import BacktestConfig, simulate
    from backend.app.strategy import Setup

    bars = [Candle(index * 900, 100, 105, 99, 104, 1) for index in range(2)]
    bars += [Candle(index * 900, 104, 106, 103, 105, 1) for index in range(2, 6)]
    bars += [Candle(5400, 102.5, 106, 101, 104, 1), Candle(6300, 105, 121, 104, 120, 1)]
    seen = []

    def signal(history, **kwargs):
        seen.append(history[-1].time)
        return Setup("long", history[-1].close, 90, 120, "test")

    result = simulate(bars, BacktestConfig(), [], bars,
                      {"contractSize": .001, "volUnit": 1, "minVol": 1, "maxVol": 100000000},
                      warmup=1, window=100, signal=signal, entry_policy="body50_confirm")
    assert seen == [900, 5400]
    assert result["trade_count"] == 0
