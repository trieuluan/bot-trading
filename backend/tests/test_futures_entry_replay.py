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
    assert seed["entry_policy"] == "body50" and seed["rule"] == RULE_SEEDS[0]["rule"]
    assert genome_name(seed) != genome_name(RULE_SEEDS[0]) and not shadow_only(seed)
    assert not shadow_only(RULE_SEEDS[0])
    assert crossover(RULE_SEEDS[1], seed)["entry_policy"] == "body50"
    seen = {mutate(RULE_SEEDS[0], random.Random(i)).get("entry_policy", "market") for i in range(400)}
    assert {"body50", "retest_h1"} <= seen
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

def test_limit_entries_cannot_qualify_from_market_only_windows():
    from backend.app.futures_learner import WINDOW_SECONDS, qualification
    from backend.app.futures_strategies import ENTRY_SEEDS
    candidate = {"genome": ENTRY_SEEDS[0], "born_at": 0, "windows": [{
        "start_time": 0, "end_time": WINDOW_SECONDS, "gains": 200, "losses": 1,
        "trade_count": 100, "net_pnl": 199, "max_drawdown_percent": 1}]}
    result = qualification(candidate)
    assert not result["eligible"] and not result["checks"]["entry_windows_supported"]
    assert all(value for key, value in result["checks"].items() if key != "entry_windows_supported")
