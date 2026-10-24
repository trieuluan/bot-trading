import copy
import random

import pytest

from backend.app import futures_drafts as drafts
from backend.app import futures_learner as learner
from backend.app.backtest import BacktestConfig, simulate
from backend.app.db import PaperFunding, PaperTrade, PaperWallet, Session
from backend.app.feed import save_candles
from backend.app.futures_exits import (
    EXIT_POLICIES,
    adaptive_levels,
    adaptive_target,
    h1_view,
    has_target,
    partial_size,
    protective_stop,
    resistance_ahead,
)
from backend.app.futures_learner import FEE, SLIP, advance
from backend.app.futures_strategies import (
    RULE_SEEDS,
    SEEDS,
    crossover,
    genome_fingerprint,
    genome_name,
    mutate,
    strategy_setup,
    validate_genome,
)
from backend.app.market import Candle
from backend.app.paper import close_position, reconcile_stops
from backend.app.strategy import Setup
from backend.tests.test_paper_recovery import recovery as recovery


def hourly_history(rows):
    return [Candle(index * 3600 + quarter * 900, row.open, row.high, row.low,
                   row.close, row.volume / 4)
            for index, row in enumerate(rows) for quarter in range(4)]


def history(side):
    direction = 1 if side == "long" else -1
    prices = [100, 100, 103, 103, 104]
    return [Candle(index * 900, 100 + direction * (price - 100),
                   100 + direction * (price - 100) + 0.5,
                   100 + direction * (price - 100) - 0.5,
                   100 + direction * (price - 100), 1)
            for index, price in enumerate(prices)]


@pytest.mark.parametrize("side", ["long", "short"])
def test_teaching_and_validation_partial_fees_funding_and_full_pnl_match(side):
    direction = 1 if side == "long" else -1
    rows = history(side)
    stop, target = 100 - direction * 2, 100 + direction * 20
    funding = [dict(time=2700, rate=0.0001)]
    sample = dict(side=side, entry=None, stop=stop, target=target, status="open",
                  entry_time=900, exit_policy="adaptive")
    for index in range(1, len(rows)):
        advance(sample, rows[index], rows[index], funding, rows[:index + 1])
    assert sample["status"] == "open"
    assert sample["quantity"] == 0.5
    assert len(sample["partial_exits"]) == 1
    assert sample["partial_exits"][0]["time"] == 2700
    assert sample["funding_cost"] == pytest.approx(direction * rows[3].open * 0.0001 * 0.5)
    sample["entry_time"] = -192 * 900
    advance(sample, rows[-1], rows[-1], [], rows)
    assert sample["status"] == "resolved"

    def signal(candles, allow_short=False):
        return Setup(side, 100, stop, target, "test") if candles[-1].time == 0 else None

    result = simulate(rows, BacktestConfig(), funding, rows,
                      dict(contractSize=0.001, volUnit=1, minVol=1, maxVol=10000000),
                      warmup=0, signal=signal, exit_policy="adaptive")
    trade = result["trades"][0]
    original = trade["original_quantity"]
    assert trade["net_pnl"] / original / sample["risk"] == pytest.approx(sample["net_r"])
    assert result["final_equity"] - 10000 == pytest.approx(trade["net_pnl"])
    assert len(trade["partial_exits"]) == 1


@pytest.mark.parametrize("side", ["long", "short"])
def test_stop_first_no_partial_or_retroactive_new_stop(side):
    direction = 1 if side == "long" else -1
    sample = dict(side=side, entry=100, stop=100 - direction * 2,
                  target=100 + direction * 4, risk=2, funding_cost=0,
                  entry_time=0, status="open", exit_policy="adaptive")
    bar = Candle(0, 100, 105, 95, 100 + direction * 3, 1)
    advance(sample, bar, bar, [], [bar])
    assert sample["exit_reason"] == "stop"
    assert "partial_exits" not in sample


@pytest.mark.parametrize("side", ["long", "short"])
def test_sizing_respects_contract_step_minimum_and_never_exits_twice(side):
    direction = 1 if side == "long" else -1
    sample = dict(side=side, entry=100, risk=2, exit_policy="adaptive", quantity=3)
    rows = history(side)
    assert partial_size(sample, rows, 1, 1) == 1
    strong = rows[:-1] + [Candle(3600, 100 + direction * 2.5,
                                 max(100 + direction * 4.6, 100 + direction * 2.4),
                                 min(100 + direction * 4.6, 100 + direction * 2.4),
                                 100 + direction * 4.5, 1)]
    assert partial_size(sample, strong, 1, 1) == 0
    assert partial_size({**sample, "quantity": 1}, rows, 1, 1) == 0
    assert partial_size({**sample, "partial_exits": [dict(quantity=1)]}, rows, 1, 1) == 0
    assert partial_size({**sample, "exit_policy": "fixed"}, rows, 1, 1) == 0


@pytest.mark.parametrize("side", ["long", "short"])
def test_initial_levels_and_target_stop_only_tighten(side):
    direction = 1 if side == "long" else -1
    rows = [Candle(index * 900, 100, 101, 99, 100, 1) for index in range(21)]
    # Place barrier well above 1.5R (risk is ~1.25, barrier at 105 is >3R away)
    rows[10] = Candle(9000, 100, 106 if side == "long" else 101,
                      99 if side == "long" else 94, 100, 1)
    rows = hourly_history(rows)
    stop, target = adaptive_levels(side, rows)
    assert direction * (100 - stop) > 0
    assert direction * (target - 100) >= 1.5 * abs(100 - stop)
    sample = dict(side=side, entry=100, risk=2, stop=100 - direction * 2,
                  target=100 + direction * 20, exit_policy="adaptive")
    # At index 20, candle moves in profit but closes with opposite body (weak close)
    weak_bar = Candle(rows[-1].time, 100 + direction * 3.5,
                      max(100 + direction * 3.6, 100 + direction * 2.9),
                      min(100 + direction * 3.6, 100 + direction * 2.9),
                      100 + direction * 3.0, 1)
    rows[-1] = weak_bar
    next_stop = protective_stop(sample, rows, FEE, SLIP)
    assert direction * (next_stop - sample["stop"]) > 0
    sample["stop"] = next_stop
    assert protective_stop(sample, rows, FEE, SLIP) == next_stop
    rows.append(Candle(rows[-1].time + 900, 100 + direction * 3.0,
                       max(100 + direction * 4.0, 100 + direction * 2.8),
                       min(100 + direction * 4.0, 100 + direction * 2.8),
                       100 + direction * 3.0, 1))
    assert direction * (adaptive_target(sample, rows) - sample["target"]) < 0


def test_all_generic_trees_can_evolve_adaptive_without_mutating_parent():
    for seed in RULE_SEEDS:
        parent = {**seed, "exit_policy": "adaptive", "target_r": 3.0, "stop_buffer": 0.5}
        original = copy.deepcopy(parent)
        for index in range(30):
            validate_genome(mutate(parent, random.Random(index)))
            validate_genome(crossover(parent, seed))
            validate_genome(crossover(seed, parent))
        assert parent == original


@pytest.mark.parametrize("side", ["long", "short"])
@pytest.mark.parametrize("funding", [2.0, -2.0])
def test_paper_partial_survives_restart_refunds_future_funding_and_balances(recovery, side, funding):
    clock, seed = recovery
    cursor = seed(side)
    direction = 1 if side == "long" else -1
    with Session.begin() as session:
        trade, wallet = session.get(PaperTrade, 1), session.get(PaperWallet, 1)
        initial = trade.quantity
        original_fee = trade.entry_fee
        entry = trade.entry
        trade.target = entry + direction * 40
        trade.monitoring = {**trade.monitoring, "exit_policy": "adaptive", "risk": 2}
        bar = (Candle(cursor, entry + 6, entry + 6.2, entry + 2.9, entry + 3, 1) if direction == 1
               else Candle(cursor, entry - 6, entry - 2.9, entry - 6.2, entry - 3, 1))
        save_candles(session, [bar])
        session.add(PaperFunding(trade_id=1, time=cursor + 1800, rate=0.001, price=entry, cost=funding))
        trade.funding_cost = funding
        wallet.balance -= funding
        session.flush()
        reconcile_stops(session, wallet, trade, clock[0])
        assert trade.quantity < initial
        leg = trade.monitoring["partial_exits"][0]
        expected_fraction = leg["quantity"] / initial
        assert trade.funding_cost == pytest.approx(funding * (1 - expected_fraction))
        assert trade.entry_fee + leg["entry_fee"] == pytest.approx(original_fee)
        balance = wallet.balance
    with Session.begin() as session:
        trade, wallet = session.get(PaperTrade, 1), session.get(PaperWallet, 1)
        reconcile_stops(session, wallet, trade, clock[0])
        assert wallet.balance == balance
        assert len(trade.monitoring["partial_exits"]) == 1
        fill = (entry + direction * 4) * (1 - direction * SLIP)
        close_position(session, wallet, trade, "manual", clock[0], price=fill)
        assert wallet.balance - 10000 == pytest.approx(trade.net_pnl, abs=1e-6)
        expected = (direction * (leg["price"] - entry) * leg["quantity"]
                    + direction * (fill - entry) * trade.quantity - original_fee
                    - leg["exit_fee"] - trade.exit_fee - trade.funding_cost)
        assert trade.net_pnl == pytest.approx(expected, abs=1e-6)


@pytest.mark.parametrize("side", ["long", "short"])
def test_entry_target_stops_before_barrier_and_rejects_close_barrier(side):
    direction = 1 if side == "long" else -1
    rows = [Candle(index * 900, 100, 100.5, 99.5, 100, 1) for index in range(21)]
    rows[10] = Candle(9000, 100, 102.2 if side == "long" else 100.5,
                      99.5 if side == "long" else 97.8, 100, 1)
    rows = hourly_history(rows)
    stop, target = adaptive_levels(side, rows, 2.0)
    risk = abs(100 - stop)
    assert 1.5 * risk <= direction * (target - 100) < 2 * risk
    assert target == pytest.approx(resistance_ahead({"side": side}, rows))
    assert adaptive_levels(side, rows, 2.0, stop_buffer=0.5) is None

    near = [Candle(i * 900, 100, 100.5, 99.5, 100, 1) for i in range(21)]
    near[10] = Candle(9000, 100, 101.1 if side == "long" else 100.5,
                      99.5 if side == "long" else 98.9, 100, 1)
    near = hourly_history(near)
    assert adaptive_levels(side, near, 2.0) is None

    far = [Candle(index * 900, 100, 100.5, 99.5, 100, 1) for index in range(21)]
    far[10] = Candle(9000, 100, 105 if side == "long" else 100.5,
                    99.5 if side == "long" else 95, 100, 1)
    far = hourly_history(far)
    stop, target = adaptive_levels(side, far, 1.5)
    assert target == pytest.approx(100 + direction * 2 * abs(100 - stop))


@pytest.mark.parametrize("side", ["long", "short"])
def test_initial_target_without_barrier_uses_r_cap(side):
    direction = 1 if side == "long" else -1
    rows = [Candle(index * 900, 100, 100.5, 99.5, 100, 1) for index in range(21)]
    rows[-1] = (Candle(18000, 100, 102, 100, 102, 1) if direction == 1
                else Candle(18000, 100, 100, 98, 98, 1))
    rows = hourly_history(rows)
    assert resistance_ahead({"side": side}, rows) is None
    stop, target = adaptive_levels(side, rows, 3.0)
    assert target == pytest.approx(rows[-1].close + direction * 3 * abs(rows[-1].close - stop))


@pytest.mark.parametrize("side", ["long", "short"])
def test_hourly_barrier_and_strong_close_does_not_reduce_target(side):
    direction = 1 if side == "long" else -1
    rows = [Candle(index * 900, 100, 100.5, 99.5, 100, 1) for index in range(81)]
    rows[28] = (Candle(28 * 900, 100, 103, 99.5, 100, 1) if direction == 1
                else Candle(28 * 900, 100, 100.5, 97, 100, 1))
    rows[-1] = (Candle(80 * 900, 100, 101, 100, 101, 1) if direction == 1
                else Candle(80 * 900, 100, 100, 99, 99, 1))
    position = dict(side=side, entry=100, risk=1, target=100 + direction * 10,
                    exit_policy="adaptive")
    barrier = resistance_ahead(position, rows)
    assert barrier == pytest.approx(100 + direction * (3 - 0.1 * h1_view(rows)[1]))
    assert resistance_ahead(position, rows[-21:]) is None
    assert adaptive_target(position, rows) == position["target"]
    assert partial_size(position, rows) == 0


@pytest.mark.parametrize("side", ["long", "short"])
def test_tree_setup_preserves_structural_target_and_checks_final_stop_buffer(monkeypatch, side):
    direction = 1 if side == "long" else -1
    monkeypatch.setattr("backend.app.futures_strategies.matches",
                        lambda rule, rows, candidate, lookback, context: candidate == direction)
    rows = [Candle(index * 900, 100, 100.5, 99.5, 100, 1) for index in range(21)]
    rows[10] = (Candle(9000, 100, 102.2, 99.5, 100, 1) if direction == 1
                else Candle(9000, 100, 100.5, 97.8, 100, 1))
    rows = hourly_history(rows)
    genome = {**RULE_SEEDS[0], "exit_policy": "adaptive"}
    setup = strategy_setup(genome, rows, [])
    assert setup is not None
    assert (setup.stop, setup.target) == adaptive_levels(side, rows, genome["target_r"], genome["stop_buffer"])
    assert direction * (setup.target - setup.entry) < genome["target_r"] * abs(setup.entry - setup.stop)
    assert strategy_setup({**genome, "stop_buffer": 0.5}, rows, []) is None


class MutationChoices(random.Random):
    def __init__(self, *choices):
        super().__init__(0)
        self.choices = list(choices)
        self.calls = []

    def choice(self, options):
        self.calls.append(list(options))
        if not self.choices:
            return super().choice(options)
        value = self.choices.pop(0)
        assert value in options
        return value


@pytest.mark.parametrize("policy", [policy for policy in EXIT_POLICIES
                                   if has_target({"exit_policy": policy})])
@pytest.mark.parametrize("target", [1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0])
def test_hard_target_mutation_steps_and_bounds(policy, target):
    parent = {**copy.deepcopy(RULE_SEEDS[0]), "exit_policy": policy, "target_r": target}
    original = copy.deepcopy(parent)
    for expected in (value for value in (target - 0.5, target + 0.5) if 2 <= value <= 5):
        child = mutate(parent, MutationChoices("target_r", expected))
        assert child["target_r"] == expected
        validate_genome(child)
    assert parent == original


@pytest.mark.parametrize("policy", EXIT_POLICIES)
def test_target_and_buffer_mutation_eligibility(policy):
    parent = {**RULE_SEEDS[0], "exit_policy": policy}
    rng = MutationChoices("lookback", 25)
    child = mutate(parent, rng)
    assert ("target_r" in rng.calls[0]) == has_target(parent)
    assert ("stop_buffer" in rng.calls[0]) == (policy == "adaptive")
    assert child["target_r"] == 2
    if policy != "adaptive":
        with pytest.raises(ValueError):
            validate_genome({**parent, "stop_buffer": 0.1})
    else:
        assert mutate(parent, MutationChoices("stop_buffer", 0.1))["stop_buffer"] == 0.1


@pytest.mark.parametrize("key", ["rule", "lookback", "entry_policy", "stop_buffer", "exit_params"])
def test_other_mutations_normalize_legacy_target_without_changing_parent(key):
    parent = {**copy.deepcopy(RULE_SEEDS[0]), "target_r": 1.5}
    if key == "exit_params":
        parent["exit_policy"] = "time_stop"
    original = copy.deepcopy(parent)
    child = mutate(parent, MutationChoices(key))
    assert child["target_r"] == 2.0
    validate_genome(child)
    assert parent == original


@pytest.mark.parametrize("policy", ["fixed", "adaptive", "trail", "runner", "scale_out_ladder"])
@pytest.mark.parametrize("target", [1.5, 5.0])
def test_exit_policy_changes_use_final_policy_and_keep_buffer_restricted(policy, target):
    parent = {**copy.deepcopy(RULE_SEEDS[0]), "target_r": target, "stop_buffer": 0.3}
    if policy == "adaptive":
        parent.update(exit_policy="runner", target_r=2.0, stop_buffer=0.0)
    original = copy.deepcopy(parent)
    child = mutate(parent, MutationChoices("exit_policy", policy))
    assert child.get("exit_policy", "fixed") == policy
    assert child["target_r"] == (max(2, parent["target_r"]) if has_target(child) else 2)
    assert child["stop_buffer"] == (parent["stop_buffer"] if policy == "adaptive" else 0)
    assert parent == original


@pytest.mark.parametrize("policy", ["fixed", "adaptive", "trail", "runner", "scale_out_ladder"])
@pytest.mark.parametrize("target", [1.5, 4.5, 5.0])
def test_crossover_inherits_mate_policy_then_normalizes_newborn(policy, target):
    parent = {**copy.deepcopy(RULE_SEEDS[0]), "target_r": target, "stop_buffer": 0.3}
    mate = {**copy.deepcopy(RULE_SEEDS[1]), "exit_policy": policy,
            "target_r": 5.0 if has_target({"exit_policy": policy}) else 2.0}
    original = copy.deepcopy((parent, mate))
    for first, second in ((parent, mate), (mate, parent)):
        child = crossover(first, second)
        assert child.get("exit_policy", "fixed") == second.get("exit_policy", "fixed")
        assert child["target_r"] == (max(2, second["target_r"]) if has_target(child) else 2)
        assert child["stop_buffer"] == (second["stop_buffer"] if child.get("exit_policy") == "adaptive" else 0)
        validate_genome(child)
    assert (parent, mate) == original


@pytest.mark.parametrize("target", [1, 5.5, float("nan"), float("inf"), True, "5"])
def test_invalid_target_genes_rejected(target):
    with pytest.raises(ValueError):
        validate_genome({**RULE_SEEDS[0], "target_r": target})


def test_default_fixed_policy_can_evolve_target():
    parent = {key: value for key, value in RULE_SEEDS[0].items() if key != "exit_policy"}
    assert mutate(parent, MutationChoices("target_r", 2.5))["target_r"] == 2.5


def test_legacy_drafts_seeds_and_fingerprints_remain_unchanged():
    original_seeds = copy.deepcopy(SEEDS)
    parent = {**copy.deepcopy(RULE_SEEDS[0]), "target_r": 1.5}
    original = copy.deepcopy(parent)
    fingerprint, name = genome_fingerprint(parent), genome_name(parent)
    state = learner.new_state(1000)
    state["drafts"] = {}
    draft_id = drafts.enqueue(state, parent, 1000, ())
    draft = state["drafts"][draft_id]
    draft.update(status="waiting_slot", replay={"total_samples": 40, "validation_mean_r": 0.4})
    frozen = copy.deepcopy(draft)

    child = mutate(parent, MutationChoices("lookback", 25))
    new_id = drafts.enqueue(state, child, 2000, ())
    assert state["drafts"][new_id]["genome"]["target_r"] == 2
    assert state["drafts"][new_id]["strategy"] == genome_name(child)
    assert state["drafts"][new_id]["fingerprint"] == genome_fingerprint(child)
    equivalent = mutate({**parent, "target_r": 2.0}, MutationChoices("lookback", 25))
    assert drafts.enqueue(state, equivalent, 3000, ()) is None
    learner.sync_seeds(state, 3000)
    assert draft == frozen
    assert parent == original
    assert genome_fingerprint(parent) == fingerprint and genome_name(parent) == name
    assert SEEDS == original_seeds


def test_legacy_crossover_lineage_uses_original_inherited_target():
    first = copy.deepcopy(RULE_SEEDS[0])
    mate = {**copy.deepcopy(RULE_SEEDS[1]), "target_r": 1.5}
    state = learner.new_state(1000)
    learner.add_candidate(state, mate, 1000)
    old_child = {**crossover(first, mate), "target_r": 1.5, "lookback": 25}
    parents = (genome_name(first), genome_name(mate))
    learner.add_candidate(state, old_child, 2000, parents)
    original = copy.deepcopy(state)
    lineage = learner.lineage(state, state["candidates"][genome_name(old_child)])
    assert {"gene": "target_r", "value": 1.5} in lineage["from_mate"]
    assert lineage["mutated"] == [{"gene": "lookback", "before": 20, "after": 25}]
    assert state == original


@pytest.mark.parametrize("side,direction", [("long", 1), ("short", -1)])
@pytest.mark.parametrize("cap", [1.5, 3.5, 5.0])
def test_target_cap_and_nearby_barrier_do_not_change_engine(monkeypatch, side, direction, cap):
    rows = [Candle(index * 900, 100, 101, 99, 100, 1) for index in range(84)]
    monkeypatch.setattr("backend.app.futures_exits.resistance_ahead", lambda *args: None)
    stop, target = adaptive_levels(side, rows, cap)
    risk = abs(100 - stop)
    assert direction * (target - 100) / risk == pytest.approx(max(2, cap))
    barrier = 100 + direction * 1.7 * risk
    monkeypatch.setattr("backend.app.futures_exits.resistance_ahead", lambda *args: barrier)
    assert adaptive_levels(side, rows, cap) == (stop, barrier)
    barrier = 100 + direction * 1.4 * risk
    assert adaptive_levels(side, rows, cap) is None
