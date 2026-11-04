import copy
import random

import pytest
from sqlalchemy import select

from backend.app import futures_history as history
from backend.app import futures_learner as learner
from backend.app.backtest import BacktestConfig, simulate
from backend.app.db import PaperFunding, PaperTrade, PaperWallet, Session
from backend.app.futures_entries import entry_fields
from backend.app.futures_native import NATIVE_ENTRY, hold_seconds, native_atr, native_levels
from backend.app.futures_rule_tree import matches
from backend.app.futures_strategies import (
    NATIVE_SEEDS, SEEDS, crossover, genome_name, mutate, strategy_chart, strategy_setup,
    validate_genome,
)
from backend.app.market import Candle
from backend.app.paper import reconcile_stops
from backend.app.strategy import Setup, aggregate


def expand(bars, seconds=86400):
    return [Candle(bar.time + offset, bar.open if offset == 0 else bar.close,
                   bar.high, bar.low, bar.close, 10)
            for bar in bars for offset in range(0, seconds, 900)]


def d1_breakout(direction=1):
    bars = [Candle(index * 86400, 100 + index / 10, 101 + index / 10,
                   99 + index / 10, 100 + index / 10, 1) for index in range(60)]
    bars.append(Candle(60 * 86400, 106, 112, 105, 111, 1))
    if direction == -1:
        bars = [Candle(bar.time, 220 - bar.open, 220 - bar.low, 220 - bar.high,
                       220 - bar.close, 1) for bar in bars]
    return expand(bars)


@pytest.mark.parametrize("direction", [1, -1])
def test_d1_signal_native_stop_clock_and_overlay(direction):
    candles = d1_breakout(direction)
    genome = NATIVE_SEEDS[0]
    setup = strategy_setup(genome, candles, [])
    assert setup and setup.side == ("long" if direction == 1 else "short")
    bars = aggregate(candles, 86400)
    swing = min(bar.low for bar in bars[-5:]) if direction == 1 else max(bar.high for bar in bars[-5:])
    risk = max(direction * (bars[-1].close - swing) + 0.25 * native_atr(bars), native_atr(bars))
    assert setup.stop == pytest.approx(setup.entry - direction * risk)
    assert setup.target == pytest.approx(setup.entry + direction * 2 * risk)
    fields = entry_fields(genome, candles, setup, candles[-1].time)
    assert fields["confirmed"] and fields["signal_frame"] == "D1"
    assert strategy_setup(genome, candles[:-1], []) is None
    next_bar = Candle(candles[-1].time + 900, 111, 112, 110, 111, 1)
    assert strategy_setup(genome, [*candles, next_bar], []) is None
    chart = strategy_chart(genome, candles)
    assert len(chart["levels"]) == 2 and len(chart["curves"]) == 2
    assert "Wilder" in chart["overlay_details"][0]["message"]


def test_h4_sweep_uses_only_fully_closed_daily_bias():
    bars = [Candle(index * 14400, 100 + index / 100 - .05, 100 + index / 100 + .2,
                   100 + index / 100 - .2, 100 + index / 100, 1) for index in range(192)]
    last = bars[-1]
    bars[-1] = Candle(last.time, last.open, last.high, 99, last.close, 1)
    candles = expand(bars, 14400)
    setup = strategy_setup(NATIVE_SEEDS[1], candles, [])
    assert setup and setup.side == "long"
    bias = {"feature": "daily_bias", "frame": "D1"}
    assert matches(bias, candles, 1, 20)
    partial = expand([Candle(32 * 86400, 1, 2, .5, 1, 1)], 14400)
    assert matches(bias, candles + partial, 1, 20)
    assert not matches(bias, candles + partial, -1, 20)


def test_daily_aggregation_drops_partial_and_gapped_days():
    candles = d1_breakout()
    assert len(aggregate(candles[:-1], 86400)) == 60
    assert len(aggregate(candles[:50] + candles[51:], 86400)) == 60


@pytest.mark.parametrize("seed", NATIVE_SEEDS)
def test_native_mutation_and_crossover_preserve_execution_binding(seed):
    before = copy.deepcopy(seed)
    for index in range(30):
        child = mutate(seed, random.Random(index))
        validate_genome(child)
        assert child["rule"] == seed["rule"]
        assert child["signal_frame"] == seed["signal_frame"]
        assert child["entry_policy"] == NATIVE_ENTRY
        crossed = crossover(seed, NATIVE_SEEDS[1])
        validate_genome(crossed)
        assert crossed["signal_frame"] == seed["signal_frame"]
    assert seed == before
    assert crossover(SEEDS[0], seed)["rule"] == SEEDS[0]["rule"]
    for patch in ({"entry_policy": "body50_confirm"}, {"signal_frame": []},
                  {"exit_policy": "adaptive"}, {"rule": SEEDS[0]["rule"]}):
        with pytest.raises(ValueError):
            validate_genome({**seed, **patch})


@pytest.mark.parametrize("frame", ["D1", "H4"])
@pytest.mark.parametrize("side", ["long", "short"])
def test_label_fill_target_timeout_and_stop_first(frame, side):
    direction = 1 if side == "long" else -1
    sample = dict(status="open", side=side, entry=None, entry_time=900,
                  stop=90 if direction == 1 else 110, target=120 if direction == 1 else 80,
                  signal_frame=frame, target_r=2, entry_policy=NATIVE_ENTRY, confirmed=True)
    bar = Candle(900, 100, 101, 99, 100, 1)
    learner.advance(sample, bar, bar, [])
    assert sample["status"] == "open"
    assert sample["target"] == pytest.approx(sample["entry"] + direction * 2 * sample["risk"])
    timed = copy.deepcopy(sample)
    expiry = 900 + hold_seconds(frame) - 900
    last = Candle(expiry, bar.close, bar.high, bar.low, bar.close, 1)
    learner.advance(timed, last, last, [])
    assert timed["exit_reason"] == "timeout"
    both = Candle(1800, bar.close, max(sample["stop"], sample["target"]) + 1,
                  min(sample["stop"], sample["target"]) - 1, bar.close, 1)
    learner.advance(sample, both, both, [])
    assert sample["exit_reason"] == "stop"


def test_native_be_applies_only_after_confirming_m15_close():
    sample = dict(status="open", side="long", entry=None, entry_time=900,
                  stop=90, target=120, signal_frame="D1", target_r=2,
                  entry_policy=NATIVE_ENTRY, confirmed=True, exit_policy="breakeven")
    first = Candle(900, 100, 112, 95, 111, 1)
    learner.advance(sample, first, first, [])
    assert sample["status"] == "open" and sample["stop"] > 100
    second = Candle(1800, 111, 112, 99, 100, 1)
    learner.advance(sample, second, second, [])
    assert sample["exit_reason"] == "stop"


def test_native_teaching_and_history_allow_overlapping_positions(monkeypatch):
    setup = Setup("long", 100, 90, 120, "test")
    monkeypatch.setattr(learner, "strategy_setup", lambda *args, **kwargs: setup)
    state = learner.new_state(0)
    name = genome_name(NATIVE_SEEDS[0])
    state["candidates"] = {name: state["candidates"][name]}
    candles = [Candle(index * 900, 100, 101, 99, 100, 1) for index in range(40)]
    for index in (25, 26):
        learner.step(state, candles[:index + 1], candles[index], candles[index].time + 900)
    assert len([sample for sample in state["samples"] if sample["status"] == "open"]) == 2
    monkeypatch.setattr(history, "strategy_setup", lambda *args, **kwargs: setup)
    monkeypatch.setattr(history, "NATIVE_WARMUP", 25)
    report = history.replay(state["candidates"], candles, candles, [], 25 * 900, 40 * 900)
    row = report["strategies"][0]
    assert row["train"]["excluded"] == 10
    assert row["validation"]["excluded"] == 5


def test_backtest_native_overlaps_but_legacy_keeps_single_position():
    candles = [Candle(index * 900, 100, 101, 99, 100, 1) for index in range(350)]
    def signal(bars, **kwargs):
        if (bars[-1].time + 900) % 86400 == 0:
            return Setup("long", 100, 90, 120, "test")
    args = dict(candles=candles, config=BacktestConfig(), funding=[{"time": 200 * 900, "rate": .001}],
                fair_candles=candles, contract=dict(contractSize=1, volUnit=1, minVol=1, maxVol=10000),
                signal=signal, warmup=21)
    native = simulate(**args, entry_policy=NATIVE_ENTRY, signal_frame="D1", target_r=2)
    legacy = simulate(**args)
    assert native["trade_count"] == 3 and legacy["trade_count"] == 1
    assert native["trades"][1]["entry_time"] < native["trades"][0]["exit_time"]
    assert native["trades"][0]["funding_cost"] > 0
    assert native["trades"][2]["funding_cost"] == 0


def test_paper_timeout_reverses_funding_after_historical_exit(monkeypatch):
    entry_time = 96 * 900
    expiry = entry_time + hold_seconds("D1") - 900
    with Session.begin() as session:
        wallet = session.get(PaperWallet, 1)
        trade = PaperTrade(symbol="BTC_USDT", side="long", entry_time=entry_time,
                           entry=100, quantity=1, contracts=1, leverage=1, stop=90, target=120,
                           entry_fee=.08, funding_cost=1,
                           monitoring=dict(next_bar=expiry, signal_frame="D1", risk=10))
        session.add(trade)
        session.flush()
        session.add(PaperFunding(trade_id=trade.id, time=expiry + 900, rate=.01, price=100, cost=1))
        monkeypatch.setattr("backend.app.paper.load_candles", lambda *args, **kwargs: [
            Candle(expiry, 105, 106, 104, 105, 1)])
        reconcile_stops(session, wallet, trade, expiry + 1800)
        assert trade.exit_reason == "timeout" and trade.exit_time == expiry + 899
        assert trade.funding_cost == 0 and trade.monitoring["status"] == "recovered"
        assert session.scalar(select(PaperFunding.cost).where(PaperFunding.trade_id == trade.id)) == 0
