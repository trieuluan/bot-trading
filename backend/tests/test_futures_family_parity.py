import random
from math import sin

import pytest

from backend.app.futures_rule_tree import FAMILY_PARITY, FAMILY_RULES, matches, validate_rule
from backend.app.futures_strategies import LEGACY_SEEDS as SEEDS, strategy_setup, to_rule_tree
from backend.app.market import Candle
from backend.tests.fixtures.futures_entry_v1 import strategy_setup as legacy_setup


def series():
    rng, price, bars = random.Random(7), 100.0, []
    for i in range(2200):
        open_ = price
        price *= 1 + rng.gauss(0, 0.004) + (0.0008 if (i // 400) % 2 == 0 else -0.0008)
        high = max(open_, price) * (1 + abs(rng.gauss(0, .002)))
        low = min(open_, price) * (1 - abs(rng.gauss(0, .002)))
        bars.append(Candle(1_700_000_000 // 900 * 900 + i * 900, open_, high, low, price,
                           rng.uniform(50, 150) * (3 if rng.random() < .05 else 1)))
    funding = [{"time": bars[0].time + k * 28800, "rate": rng.choice((-.0005, .0005, 0.0)),
                "cycle_hours": 8} for k in range(300)]
    return bars, funding

def test_all_12_families_have_valid_rules():
    assert len(FAMILY_RULES) == 12
    for family, rule in FAMILY_RULES.items():
        assert validate_rule(rule) <= 11
        assert FAMILY_PARITY[family] == 1.0

def test_tree_signals_match_frozen_legacy_entries():
    bars, funding = series()
    seeds = {s["family"]: s for s in SEEDS if "exit_policy" not in s}
    for family, rule in FAMILY_RULES.items():
        seed = seeds[family]
        signals = 0
        context = {"max_er": seed["max_er"], "funding": funding,
                   "funding_threshold": seed["funding_threshold"]}
        for end in range(400, len(bars), 7):
            window = bars[:end]
            old = legacy_setup(seed, window, funding)
            signals += old is not None
            sides = [d for d in (1, -1) if matches(rule, window, d, 20, context=context)]
            new = ("long" if sides[0] == 1 else "short") if len(sides) == 1 else None
            assert new == (old.side if old else None), (family, end)
        if family != "mean_reversion":
            assert signals > 0, family


@pytest.mark.parametrize("direction", (1, -1))
def test_mean_reversion_has_positive_signals_not_only_matching_absences(direction):
    start = 1_700_000_000 // 14400 * 14400
    bars = []
    for index in range(400):
        close = 100 + sin(index / 16)
        bars.append(Candle(start + index * 900, close, close + .1, close - .1, close, 100))
    for index in range(400, 404):
        bars.append(Candle(start + index * 900, 90, 90.5, 89.5, 90, 100))
    bars.append(Candle(start + 404 * 900, 97, 99.2, 96.5, 99, 100))
    if direction == -1:
        bars = [Candle(bar.time, 200 - bar.open, 200 - bar.low, 200 - bar.high,
                       200 - bar.close, bar.volume) for bar in bars]
    seed = next(seed for seed in SEEDS if seed["family"] == "mean_reversion")
    tree = to_rule_tree(seed)
    original = legacy_setup(seed, bars, [])
    converted = strategy_setup(tree, bars, [])
    assert original is not None and converted is not None
    assert original.side == converted.side == ("long" if direction == 1 else "short")
    assert not matches(tree["rule"], bars[:-1], direction, 20)
    assert not matches(tree["rule"], bars, -direction, 20)


def test_funding_context_reaches_nested_rules_and_auto_paper_entry():
    bars, records = series()
    seed = next(seed for seed in SEEDS if seed["family"] == "funding_filter")
    window = next(bars[:end] for end in range(800, len(bars))
                  if legacy_setup(seed, bars[:end], records) is not None)
    original = legacy_setup(seed, window, records)
    tree = to_rule_tree(seed)
    converted = strategy_setup(tree, window, records)
    assert converted is not None and converted.side == original.side
    assert strategy_setup(tree, window, []) is None
    future = [{"time": window[-1].time + 900, "rate": -.001, "cycle_hours": 8}]
    assert strategy_setup(tree, window, future) is None
    expired = [{"time": window[-1].time - 28800, "rate": -.001, "cycle_hours": 8}]
    assert strategy_setup(tree, window, expired) is None
    nested = {"op": "all", "children": [tree["rule"], tree["rule"]]}
    direction = 1 if original.side == "long" else -1
    assert matches(nested, window, direction, 20, context={"funding": records})


def test_tree_twin_has_identical_entry_stop_and_target():
    bars, funding = series()
    for seed in (s for s in SEEDS if "exit_policy" not in s):
        twin, seen = to_rule_tree(seed), 0
        for end in range(400, len(bars), 5):
            old, new = legacy_setup(seed, bars[:end], funding), strategy_setup(twin, bars[:end], funding)
            assert (old and (old.side, old.entry, old.stop, old.target)) == (
                new and (new.side, new.entry, new.stop, new.target)), (seed["family"], end)
            seen += old is not None
        assert seen or seed["family"] == "mean_reversion", seed["family"]
