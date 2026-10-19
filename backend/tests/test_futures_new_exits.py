import random

import pytest

from backend.app.futures_exits import (EVOLVABLE_EXITS, discretionary_exit, exit_parameters,
                                       has_target, partial_size, protective_stop, adaptive_levels, h1_view)
from backend.app.futures_strategies import RULE_SEEDS, mutate, validate_genome, strategy_setup
from backend.app.market import Candle

def bars(count=40, close=100.0, start=0):
    return [Candle(start + i * 900, 100, max(close, 101), min(close, 99), close, 1) for i in range(count)]

def pos(policy, **extra):
    base = dict(side="long", entry=100, risk=2, stop=98, exit_policy=policy, quantity=10,
                entry_time=0)
    return {**base, **extra}

def test_params_validated():
    assert exit_parameters("time_stop")["max_bars"] == 32
    with pytest.raises(ValueError):
        exit_parameters("time_stop", {"max_bars": 1})
    with pytest.raises(ValueError):
        exit_parameters("fixed", {"x": 1})

def test_time_stop_only_when_stalled():
    assert discretionary_exit(pos("time_stop"), bars(128, 100.5)) == "time_stop"
    assert discretionary_exit(pos("time_stop"), bars(128, 105)) is None
    assert discretionary_exit(pos("time_stop"), bars(40, 100.5)) is None
    assert discretionary_exit(pos("time_stop"), bars(10, 100.5)) is None

def test_runner_has_no_target_and_partial_once():
    assert not has_target(pos("runner")) and has_target(pos("adaptive"))
    assert partial_size(pos("runner"), bars(40, 102.5)) == 5
    assert partial_size(pos("runner", partial_exits=[1]), bars(40, 102.5)) == 0

def test_ladder_two_partials_then_complete():
    assert partial_size(pos("scale_out_ladder"), bars(40, 102.5)) == 3
    second = pos("scale_out_ladder", partial_exits=[1], original_quantity=10, quantity=7)
    assert partial_size(second, bars(40, 104.5)) == 3
    assert discretionary_exit(pos("scale_out_ladder"), bars(40, 106.5)) == "ladder_complete"

def test_chandelier_and_volatility_only_tighten():
    for policy in ("atr_chandelier", "volatility_stop", "runner"):
        stop = protective_stop(pos(policy), bars(40, 105), 0.0005, 0.0002)
        assert 98 <= stop < 105
        assert protective_stop({**pos(policy), "stop": 104.9}, bars(40, 105), 0.0005, 0.0002) >= 104.9

def test_funding_exit():
    assert discretionary_exit(pos("funding_exit", funding_cost=5), bars(40, 100.5)) == "funding_exit"
    assert discretionary_exit(pos("funding_exit", funding_cost=0), bars(40, 100.5)) is None

def test_genomes_evolve_into_all_exits():
    seen = set()
    for seed in RULE_SEEDS[:3]:
        for index in range(300):
            child = mutate(seed, random.Random(index))
            validate_genome(child)
            seen.add(child.get("exit_policy", "fixed"))
    assert set(EVOLVABLE_EXITS) - {"fixed"} <= seen | {"adaptive"}

def test_missing_hourly_history_never_falls_back_to_m15():
    assert h1_view(bars(59))[1] is None
    assert adaptive_levels("long", bars(59)) is None
    assert protective_stop(pos("atr_chandelier"), bars(59, 105), 0.0005, 0.0002) == 98

@pytest.mark.parametrize("side", ["long", "short"])
def test_all_policies_start_from_identical_hourly_stop(monkeypatch, side):
    direction = 1 if side == "long" else -1
    monkeypatch.setattr("backend.app.futures_strategies.matches",
                        lambda rule, rows, candidate, lookback, context: candidate == direction)
    rows = bars(80)
    rows[72] = Candle(72 * 900, 100, 108, 92, 100, 1)
    close = 100 + direction * 12
    rows[-1] = Candle(79 * 900, 100, max(101, close), min(99, close), close, 1)
    expected_stop, expected_target = adaptive_levels(side, rows)
    for policy in EVOLVABLE_EXITS:
        genome = {**RULE_SEEDS[0], "exit_policy": policy}
        setup = strategy_setup(genome, rows, [])
        assert setup is not None
        assert setup.stop == expected_stop
        if has_target({"exit_policy": policy}):
            assert setup.target == expected_target
        assert strategy_setup(genome, rows[-21:], []) is None


def test_exit_seeds_cover_every_other_exit_mode_with_same_entry():
    from backend.app.futures_exits import EVOLVABLE_EXITS
    from backend.app.futures_strategies import EXIT_SEEDS
    assert len(EXIT_SEEDS) == 3 * (len(EVOLVABLE_EXITS) - 1)
    for seed in EXIT_SEEDS:
        validate_genome(seed)
    for base in (RULE_SEEDS[0], RULE_SEEDS[1], RULE_SEEDS[7]):
        modes = {s.get("exit_policy", "fixed") for s in EXIT_SEEDS if s["rule"] == base["rule"]}
        assert modes == set(EVOLVABLE_EXITS) - {"adaptive"}
