import random

import pytest

from backend.app.backtest import BacktestConfig, simulate
from backend.app.futures_exits import protective_stop, structural_target
from backend.app.futures_learner import FEE, SLIP, advance
from backend.app.futures_rules import atr14
from backend.app.futures_strategies import (
    SEEDS,
    crossover,
    exit_policy_enabled,
    genome_name,
    mutate,
)
from backend.app.market import Candle
from backend.app.strategy import Setup
from backend.tests.test_futures_adaptive_exits import hourly_history
from backend.tests.test_paper_recovery import recovery as recovery_fixture


@pytest.fixture
def recovery(monkeypatch):
    return recovery_fixture.__wrapped__(monkeypatch)

def bars(close):
    return [Candle(index * 900, 100, max(101, close), min(99, close),
                   close if index == 14 else 100, 1) for index in range(15)]

def pos(policy, side="long"):
    return dict(side=side, entry=100, risk=2, stop=98 if side == "long" else 102, exit_policy=policy)

def test_fixed_never_moves():
    assert protective_stop(pos("fixed"), bars(110), 0.0005, 0.0002) == 98

def test_breakeven_after_1r_covers_costs_and_never_loosens():
    stop = protective_stop(pos("breakeven"), bars(102.5), 0.0005, 0.0002)
    assert 100.1 < stop < 100.2
    assert protective_stop(pos("breakeven"), bars(101), 0.0005, 0.0002) == 98
    assert protective_stop({**pos("breakeven"), "stop": 101}, bars(102.5), 0.0005, 0.0002) == 101

def test_short_breakeven_mirrors():
    assert 99.8 < protective_stop(pos("breakeven", "short"), bars(97.5), 0.0005, 0.0002) < 99.9


@pytest.mark.parametrize("side", ["long", "short"])
@pytest.mark.parametrize("funding", [0.0, 0.04, -0.04])
def test_breakeven_accounts_for_exact_fills_fees_and_settled_funding(side, funding):
    direction = 1 if side == "long" else -1
    position = {**pos("breakeven", side), "quantity": 3, "entry_fee": 0.15,
                "funding_cost": funding}
    stop = protective_stop(position, bars(100 + direction * 3), FEE, SLIP)
    fill = stop * (1 - direction * SLIP)
    net = direction * (fill - 100) * 3 - 0.15 - fill * 3 * FEE - funding
    assert net == pytest.approx(0, abs=1e-10)


@pytest.mark.parametrize("side", ["long", "short"])
def test_trail_uses_swing_with_atr_buffer_and_only_tightens(side):
    direction = 1 if side == "long" else -1
    history = [Candle(index * 900, 104, 106, 103, 105, 1) if side == "long" else
               Candle(index * 900, 96, 97, 94, 95, 1) for index in range(60)]
    stop = protective_stop(pos("trail", side), history, FEE, SLIP)
    assert stop == pytest.approx(102.7 if direction == 1 else 97.3)
    assert protective_stop({**pos("trail", side), "stop": stop},
                           bars(100 + direction), FEE, SLIP) == stop


def test_teaching_stop_first_and_new_stop_applies_next_bar_only():
    sample = {**pos("breakeven"), "target": 120, "status": "open", "entry_time": 0,
              "funding_cost": 0}
    advance(sample, Candle(0, 100, 104, 99, 103, 1), Candle(0, 100, 104, 99, 103, 1), [])
    assert sample["status"] == "open" and sample["stop"] > 100
    advance(sample, Candle(900, 103, 104, 100, 102, 1), Candle(900, 103, 104, 100, 102, 1), [])
    assert sample["status"] == "resolved" and sample["exit_reason"] == "stop"
    assert sample["net_r"] == pytest.approx(0, abs=1e-10)
    ambiguous = {**pos("trail"), "target": 104, "status": "open", "entry_time": 0,
                 "funding_cost": 0}
    advance(ambiguous, Candle(0, 100, 105, 97, 103, 1), Candle(0, 100, 105, 97, 103, 1), [])
    assert ambiguous["exit_reason"] == "stop" and ambiguous["net_r"] < 0


def test_validation_and_teaching_share_exit_policy():
    candles = [Candle(0, 100, 101, 99, 100, 1), Candle(900, 100, 104, 99, 103, 1),
               Candle(1800, 103, 104, 100, 102, 1), Candle(2700, 102, 103, 97, 98, 1)]
    contract = dict(contractSize=1, volUnit=1, maxVol=1000, minVol=1)
    def signal(history, allow_short=False):
        return Setup("long", 100, 98, 120, "test") if history[-1].time == 0 else None
    config = BacktestConfig()
    fixed = simulate(candles, config, [], candles, contract, warmup=0, signal=signal)
    managed = simulate(candles, config, [], candles, contract, warmup=0, signal=signal,
                       exit_policy="breakeven")
    assert fixed["trades"][0]["exit_time"] == 2700
    assert managed["trades"][0]["exit_time"] == 1800
    assert managed["trades"][0]["net_pnl"] == pytest.approx(0, abs=1e-8)


@pytest.mark.parametrize("side", ["long", "short"])
def test_structure_target_caps_at_barrier_rejects_poor_rr_and_uses_closed_frames(side):
    history = [Candle(index * 900, 100, 110, 90, 100, 1) for index in range(320)]
    stop, target = (98, 120) if side == "long" else (102, 80)
    result = structural_target(side, 100, stop, target, history)
    assert result == pytest.approx(108 if side == "long" else 92)
    poor_stop = 94 if side == "long" else 106
    assert structural_target(side, 100, poor_stop, target, history) is None
    assert structural_target(side, 100, stop, target, history[:30]) is None
    partial = history + [Candle(320 * 900, 100, 200, 1, 100, 1)]
    direction = 1 if side == "long" else -1
    barrier = 110 if side == "long" else 90
    assert structural_target(side, 100, stop, target, partial) == pytest.approx(
        barrier - direction * 0.1 * atr14(partial))


def test_seed_exit_variants_are_distinct():
    seed = SEEDS[0]
    assert seed["exit_policy"] == "adaptive"
    assert genome_name({**seed, "exit_policy": "trail"}) != genome_name(seed)


def test_search_keeps_htf_seeds_adaptive():
    assert len(SEEDS) == 26
    assert all(seed["exit_policy"] == "adaptive" for seed in SEEDS)
    for seed in SEEDS:
        for index in range(50):
            assert exit_policy_enabled(mutate(seed, random.Random(index)))
            for policy in ("breakeven", "structure", "trail", "adaptive"):
                old = {**seed, "exit_policy": policy}
                assert exit_policy_enabled(mutate(old, random.Random(index)))
                assert exit_policy_enabled(crossover(seed, old))

def test_stored_disallowed_policies_cannot_signal_breed_or_execute(monkeypatch):
    from backend.app import futures_learner as learner
    from backend.app.db import Session
    from backend.app.strategy import Setup

    state = learner.new_state(0)
    state["candidates"] = {}
    old = {**SEEDS[0], "exit_policy": "structure"}
    learner.add_candidate(state, old, 0)
    name = genome_name(old)
    monkeypatch.setattr(learner, "strategy_setup", lambda *args, **kwargs: Setup("long", 100, 98, 104, "test"))
    learner.step(state, bars(100), bars(100)[-1], 14000)
    assert state["last_decision"]["selected"] is None
    assert state["last_decision"]["strategies"][0]["status"] == "paused"
    assert state["samples"] == []
    state["samples"] = [{"strategy": name, "status": "resolved", "label_available_at": 0, "net_r": 2}] * 20
    assert learner.evolve(state, 14000, force=True) == 0
    state["auto_mode"] = "paper_learning"
    state["last_decision"] = dict(time=900, mode="paper_learning", auto_mode="paper_learning",
                                  selected=name, setup=dict(side="long", entry=100, stop=98,
                                                            target=104, reason="old"))
    with Session.begin() as session:
        learner.save_state(session, state)
        assert learner.select_for_auto(session, 900, 14000) == (True, None, None)
    assert state["candidates"][name]["genome"] == old
    assert learner.summary(state, 14000)["strategies"][0]["paused"]
    assert not learner.qualification(state["candidates"][name])["eligible"]


def test_paper_management_persists_without_changing_existing_positions(recovery):
    from backend.app.db import PaperTrade, PaperWallet, Session
    from backend.app.feed import save_candles
    from backend.app.paper import reconcile_stops
    clock, seed = recovery
    cursor = seed()
    with Session.begin() as session:
        trade = session.get(PaperTrade, 1)
        wallet = session.get(PaperWallet, 1)
        trade.target = 150
        trade.monitoring = {**trade.monitoring, "exit_policy": "trail", "risk": trade.entry - trade.stop}
        entry = trade.entry
        save_candles(session, [Candle(cursor, entry, entry + 8, entry - 1, entry + 7, 1)])
        reconcile_stops(session, wallet, trade, clock[0])
        assert trade.exit_time is None and trade.stop > entry
        moved_stop = trade.stop
    with Session.begin() as session:
        trade = session.get(PaperTrade, 1)
        assert trade.monitoring["exit_policy"] == "trail"
        assert trade.stop == moved_stop
        reconcile_stops(session, session.get(PaperWallet, 1), trade, clock[0])
        assert trade.stop == moved_stop and trade.exit_time is None

def test_adaptive_levels_partial_once_and_target_pulls_to_resistance():
    from backend.app.futures_exits import adaptive_levels, adaptive_target, partial_size
    history = [Candle(i * 900, 100, 100.5, 99.5, 100, 1) for i in range(21)]
    history[10] = Candle(9000, 100, 105, 99.5, 100, 1)
    history = hourly_history(history)
    levels = adaptive_levels("long", history)
    assert levels is not None
    stop, target = levels
    assert stop < 100 < target
    position = {**pos("adaptive"), "target": 120, "quantity": 3}
    candles = history[:-1] + [Candle(history[-1].time, 104, 104.2, 102.9, 103, 1)]
    assert partial_size(position, candles, 1, 1) == 1
    assert partial_size({**position, "partial_exits": [1]}, candles, 1, 1) == 0
    resistance = candles[:-2] + [Candle(candles[-2].time, 100, 105, 99, 100, 1), candles[-1]]
    assert adaptive_target({**position, "entry": 100, "risk": 2}, resistance) < 120
