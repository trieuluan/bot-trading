import copy
import random

import pytest

from backend.app.backtest import BacktestConfig, simulate
from backend.app.db import PaperFunding, PaperTrade, PaperWallet, Session
from backend.app.feed import save_candles
from backend.app.futures_exits import adaptive_levels, adaptive_target, partial_size, protective_stop
from backend.app.futures_exits import resistance_ahead
from backend.app.futures_rules import atr14
from backend.app.futures_learner import FEE, SLIP, advance
from backend.app.futures_strategies import RULE_SEEDS, crossover, mutate, strategy_setup, validate_genome
from backend.app.market import Candle
from backend.app.paper import close_position, reconcile_stops
from backend.app.strategy import Setup
from backend.tests.test_paper_recovery import recovery


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
    stop, target = adaptive_levels(side, rows)
    assert direction * (100 - stop) > 0
    assert direction * (target - 100) >= 1.5 * abs(100 - stop)
    sample = dict(side=side, entry=100, risk=2, stop=100 - direction * 2,
                  target=100 + direction * 20, exit_policy="adaptive")
    # At index 20, candle moves in profit but closes with opposite body (weak close)
    weak_bar = Candle(18000, 100 + direction * 3.5,
                      max(100 + direction * 3.6, 100 + direction * 2.9),
                      min(100 + direction * 3.6, 100 + direction * 2.9),
                      100 + direction * 3.0, 1)
    rows[-1] = weak_bar
    next_stop = protective_stop(sample, rows, FEE, SLIP)
    assert direction * (next_stop - sample["stop"]) > 0
    sample["stop"] = next_stop
    assert protective_stop(sample, rows, FEE, SLIP) == next_stop
    rows.append(Candle(18900, 100 + direction * 3.0,
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
    stop, target = adaptive_levels(side, rows, 2.0)
    risk = abs(100 - stop)
    assert 1.5 * risk <= direction * (target - 100) < 2 * risk
    assert target == pytest.approx(resistance_ahead({"side": side}, rows))
    assert adaptive_levels(side, rows, 2.0, stop_buffer=0.5) is None

    near = [Candle(i * 900, 100, 100.5, 99.5, 100, 1) for i in range(21)]
    near[10] = Candle(9000, 100, 101.1 if side == "long" else 100.5,
                      99.5 if side == "long" else 98.9, 100, 1)
    assert adaptive_levels(side, near, 2.0) is None

    far = list(rows)
    far[10] = Candle(9000, 100, 105 if side == "long" else 100.5,
                     99.5 if side == "long" else 95, 100, 1)
    stop, target = adaptive_levels(side, far, 1.5)
    assert target == pytest.approx(100 + direction * 2 * abs(100 - stop))


@pytest.mark.parametrize("side", ["long", "short"])
def test_initial_target_without_barrier_uses_r_cap(side):
    direction = 1 if side == "long" else -1
    rows = [Candle(index * 900, 100, 100.5, 99.5, 100, 1) for index in range(21)]
    rows[-1] = (Candle(18000, 100, 102, 100, 102, 1) if direction == 1
                else Candle(18000, 100, 100, 98, 98, 1))
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
    assert barrier == pytest.approx(100 + direction * (3 - 0.1 * atr14(rows)))
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
    genome = {**RULE_SEEDS[0], "exit_policy": "adaptive"}
    setup = strategy_setup(genome, rows, [])
    assert setup is not None
    assert (setup.stop, setup.target) == adaptive_levels(side, rows, genome["target_r"])
    assert direction * (setup.target - setup.entry) < genome["target_r"] * abs(setup.entry - setup.stop)
    assert strategy_setup({**genome, "stop_buffer": 0.5}, rows, []) is None
