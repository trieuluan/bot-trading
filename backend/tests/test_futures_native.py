import copy
import random
from dataclasses import replace

import pytest
from sqlalchemy import select

from backend.app import futures_history as history
from backend.app import futures_learner as learner
from backend.app.backtest import BacktestConfig, simulate
from backend.app.db import (
    AutoPaperDecision,
    FuturesLearningState,
    PaperFunding,
    PaperTrade,
    PaperWallet,
    Session,
)
from backend.app.futures_entries import entry_fields
from backend.app.futures_native import NATIVE_ENTRY, entry_session_allowed, hold_seconds, native_atr
from backend.app.futures_rule_tree import matches
from backend.app.futures_strategies import (
    NATIVE_SEEDS,
    SEEDS,
    crossover,
    genome_name,
    mutate,
    strategy_chart,
    strategy_setup,
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
    bars = [Candle(index * 14400 + 14400, 100 + index / 100 - .05, 100 + index / 100 + .2,
                   100 + index / 100 - .2, 100 + index / 100, 1) for index in range(192)]
    last = bars[-1]
    bars[-1] = Candle(last.time, last.open, last.high, 99, last.close, 1)
    candles = expand(bars, 14400)
    assert (candles[-1].time + 900) % 86400 == 4 * 3600
    assert strategy_setup(NATIVE_SEEDS[1], candles, []) is None
    shifted = [replace(bar, time=bar.time + 8 * 3600) for bar in candles]
    setup = strategy_setup(NATIVE_SEEDS[1], shifted, [])
    assert setup and setup.side == "long"
    assert setup.target == pytest.approx(setup.entry + 3 * (setup.entry - setup.stop))
    assert NATIVE_SEEDS[0]["target_r"] == 2
    edge = [replace(bar, time=bar.time + 4 * 3600) for bar in candles]
    assert (edge[-1].time + 900) % 86400 == 8 * 3600
    assert strategy_setup(NATIVE_SEEDS[1], edge, []) is None
    candles = shifted
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


@pytest.mark.parametrize("seconds,allowed", [(12 * 3600 - 1, False), (12 * 3600, True),
                                          (16 * 3600, True), (20 * 3600 - 1, True),
                                          (20 * 3600, False), (0, False)])
def test_native_session_boundaries(seconds, allowed):
    assert entry_session_allowed(NATIVE_SEEDS[1], 100 * 86400 + seconds) is allowed
    assert entry_session_allowed(NATIVE_SEEDS[0], 100 * 86400 + seconds)


@pytest.mark.parametrize("value", [None, [], "all", "12_20"])
def test_native_rejects_unknown_session(value):
    with pytest.raises(ValueError, match="entry session"):
        validate_genome({**NATIVE_SEEDS[1], "entry_session": value})
    with pytest.raises(ValueError, match="entry session"):
        validate_genome({**NATIVE_SEEDS[0], "entry_session": "utc_12_20"})


def test_h4_session_migration_preserves_history_and_open_trade_ownership():
    old = {key: value for key, value in NATIVE_SEEDS[1].items() if key != "entry_session"}
    old["target_r"] = 2.0
    old_name = genome_name(old)
    new_name, d1_name = map(genome_name, (NATIVE_SEEDS[1], NATIVE_SEEDS[0]))
    state = learner.new_state(1000)
    state.pop("h4_session_3r_started_at")
    state["candidates"].pop(new_name)
    state["candidates"][old_name] = dict(genome=old, born_at=900, parents=[],
                                         generation=0, windows=[{"entry_policy": NATIVE_ENTRY}], promoted=False)
    d1_before = copy.deepcopy(state["candidates"][d1_name])
    state["samples"] = [
        dict(strategy=old_name, status="open", entry=None, side="long"),
        dict(strategy=old_name, status="open", entry=100, side="long"),
        dict(strategy=old_name, status="resolved", entry=100, net_r=1),
    ]
    state["drafts"] = {"old_h4": dict(genome=old, status="waiting_slot")}
    state["last_decision"] = {"selected": old_name}
    with Session.begin() as session:
        session.get(FuturesLearningState, 1).payload = state
        trade = PaperTrade(symbol="BTC_USDT", side="long", entry_time=900,
                           entry=100, quantity=1, contracts=1, leverage=1, stop=95, target=110,
                           entry_fee=0.08)
        session.add(trade)
        session.flush()
        trade_id = trade.id
        session.add(AutoPaperDecision(symbol="BTC_USDT", candle_time=900, action="opened",
                                      reason="test", trade_id=trade.id,
                                      parameters={"audit": {"strategy": old_name}}))
    with Session.begin() as session:
        migrated = learner.read_state(session, 1800)
        assert new_name in migrated["candidates"] and old_name not in migrated["candidates"]
        assert migrated["candidates"][d1_name] == d1_before
        assert migrated["candidates"][new_name]["windows"] == []
        assert migrated["candidates"][new_name]["born_at"] == 2700
        assert migrated["rule_archive"][old_name]["genome"] == old
        assert len(migrated["rule_archive"][old_name]["windows"]) == 1
        assert migrated["samples"][0]["status"] == "excluded"
        assert migrated["samples"][1]["status"] == "open"
        assert migrated["samples"][2]["net_r"] == 1
        assert migrated["last_decision"] is None
        assert migrated["drafts"]["old_h4"]["status"] == "rejected"
        assert learner.active_strategies(session, migrated) == {old_name}
        assert session.get(PaperTrade, trade_id).target == 110
        learner.save_state(session, migrated)
    with Session() as session:
        assert learner.read_state(session, 3600) == migrated


@pytest.mark.parametrize("signal_hour,now_hour,opens", [(12, 12, True), (16, 16, True),
                                                       (8, 12, False), (20, 20, False),
                                                       (16, 20, False)])
def test_native_paper_checks_signal_and_execution_session(monkeypatch, signal_hour, now_hour, opens):
    from backend.app import auto_paper

    name = genome_name(NATIVE_SEEDS[1])
    recorded = []
    monkeypatch.setattr(auto_paper, "record_decision", lambda *args: recorded.append(args[4]))
    def quoted(*args, **kwargs):
        raise RuntimeError("entry reached")
    monkeypatch.setattr(auto_paper, "execution_price", quoted)
    args = (None, None, None, 86400 + signal_hour * 3600 - 900, 86400 + now_hour * 3600,
            Setup("long", 100, 95, 115, "test"), name,
            {"candidates": {name: {"genome": NATIVE_SEEDS[1]}}}, {})
    if opens:
        with pytest.raises(RuntimeError, match="entry reached"):
            auto_paper.place_native(*args)
    else:
        auto_paper.place_native(*args)
        assert recorded == ["Outside native entry session"]

def h1_sample(side="long", stop=90):
    base = 100 * 86400 + 12 * 3600
    seed = NATIVE_SEEDS[2]
    fields = entry_fields(seed, [], Setup(side, 100, stop, 130, "test"), base - 900)
    return dict(side=side, stop=stop, target=130, entry_time=base, **fields)

def h1_bars(closes, start=100 * 86400 + 12 * 3600):
    return [Candle(start + index * 900, 100, max(100, close) + 1, min(100, close) - 1, close, 1)
            for index, close in enumerate(closes)]

def test_h4_h1_confirm_is_paper_enabled_and_can_breed():
    shadow = NATIVE_SEEDS[2]
    validate_genome(shadow)
    assert not learner.shadow_only(shadow) and not learner.shadow_only(NATIVE_SEEDS[1])
    assert {key: value for key, value in shadow.items() if key != "entry_confirmation"} == NATIVE_SEEDS[1]
    assert genome_name(shadow) != genome_name(NATIVE_SEEDS[1])
    child = mutate(shadow, random.Random(1))
    assert child["entry_confirmation"] == "h1" and child != shadow
    with pytest.raises(ValueError, match="entry confirmation"):
        validate_genome({**NATIVE_SEEDS[0], "entry_confirmation": "h1"})
    state = learner.new_state(1000)
    legacy = genome_name(SEEDS[0])
    h1_name = genome_name(shadow)
    state["candidates"] = {h1_name: state["candidates"][h1_name]}
    state["samples"] = [
        dict(strategy=legacy, status="resolved", net_r=2.0, label_available_at=1500),
        dict(strategy=h1_name, status="resolved", net_r=1.0, label_available_at=1500),
    ] * 10
    assert learner.evolve(state, 99999, force=True) == 1
    child = next(iter(state["drafts"].values()))
    assert child["parents"] == [h1_name] and child["genome"]["entry_confirmation"] == "h1"

def test_h4_h1_confirm_waits_for_closed_h1_and_enters_next_open():
    sample, bars = h1_sample(), h1_bars([101, 101, 101, 102])
    for index, bar in enumerate(bars):
        wait_confirmed = sample.get("confirmed")
        learner.wait(sample, bar, [], bars[:index + 1])
        assert sample.get("confirmed", False) is (index == 3) and not wait_confirmed
    assert sample["entry_time"] == bars[-1].time + 900

def test_h4_h1_confirm_cancels_on_stop_opposite_h1_or_session_end():
    for closes, reason in (([99] * 16, "expired"), ([90], "stop_before_confirmation")):
        sample, bars = h1_sample(), h1_bars(closes)
        for index, bar in enumerate(bars):
            learner.wait(sample, bar, [], bars[:index + 1])
        assert sample.get("status") == "cancelled" and sample.get("exit_reason") == reason
    sample = h1_sample()
    late = h1_bars([101], sample["entry_time"] + 900)
    learner.wait(sample, late[0], [], late)
    assert sample["exit_reason"] == "missing_confirmation_bar"

@pytest.mark.parametrize("side,stop,closes", [("long", 90, [99] * 12 + [101] * 4),
                                             ("short", 110, [101] * 12 + [99] * 4)])
def test_h4_h1_confirm_fourth_hour_is_allowed_without_future_data(side, stop, closes):
    sample, bars = h1_sample(side, stop), h1_bars(closes)
    for index, bar in enumerate(bars):
        learner.wait(sample, bar, [], bars[:index + 1])
    assert sample["confirmed"] and sample["entry_time"] == bars[-1].time + 900

def test_h4_h1_confirm_cannot_fill_at_20_utc():
    sample = h1_sample()
    shift = 4 * 3600
    for field in ("entry_time", "first_entry_time", "next_confirmation_bar", "hold_until"):
        sample[field] += shift
    bars = h1_bars([99] * 12 + [101] * 4, sample["entry_time"])
    for index, bar in enumerate(bars):
        learner.wait(sample, bar, [], bars[:index + 1])
    assert not sample["confirmed"]
    assert sample["exit_reason"] == "confirmation_outside_session_or_expired"

def test_h4_h1_label_fill_reprices_target_and_preserves_holding_deadline():
    sample = {**h1_sample(), "status": "open", "entry": None, "net_r": None}
    original_stop, deadline = sample["stop"], sample["hold_until"]
    bars = h1_bars([101] * 4)
    for index, bar in enumerate(bars):
        learner.advance(sample, bar, bar, [], bars[:index + 1])
        assert sample["entry"] is None
    fill = Candle(sample["entry_time"], 102, 103, 101, 102, 1)
    learner.advance(sample, fill, fill, [])
    assert sample["entry"] == pytest.approx(102 * (1 + learner.SLIP))
    assert sample["target"] == pytest.approx(sample["entry"] + 3 * sample["risk"])
    assert sample["stop"] == original_stop and sample["hold_until"] == deadline
    last = replace(fill, time=deadline - 900)
    learner.advance(sample, last, last, [])
    assert sample["exit_reason"] == "timeout"

def test_h4_h1_seed_sync_is_idempotent_and_does_not_reset_old_candidates():
    state = learner.new_state(1000)
    name = genome_name(NATIVE_SEEDS[2])
    state["candidates"].pop(name)
    before = copy.deepcopy(state["candidates"])
    learner.sync_seeds(state, 2000)
    assert state["candidates"][name]["born_at"] == 2700
    assert {key: state["candidates"][key] for key in before} == before
    after = copy.deepcopy(state["candidates"])
    learner.sync_seeds(state, 3000)
    assert state["candidates"] == after

def test_legacy_drafts_retire_without_deleting_evidence_or_candidates():
    state = learner.new_state(1000)
    legacy = genome_name(SEEDS[0])
    state["drafts"] = {"old": {"genome": SEEDS[0], "status": "insufficient", "runs": [{"evidence": 1}],
                               "replay": {"total_samples": 0}, "next_retry_at": 900000}}
    candidates = copy.deepcopy(state["candidates"])
    with Session.begin() as session:
        learner.save_state(session, state)
    with Session.begin() as session:
        current = learner.read_state(session, 2000)
        assert current["drafts"]["old"]["status"] == "rejected"
        assert current["drafts"]["old"]["runs"] == [{"evidence": 1}]
        assert current["candidates"] == candidates and legacy in current["candidates"]
        learner.save_state(session, current)
    with Session() as session:
        assert learner.read_state(session, 3000)["drafts"]["old"]["updated_at"] == 2000

def test_h4_h1_confirm_paper_queues_and_fills_after_h1_close(monkeypatch):
    from datetime import datetime, timezone

    from fastapi import HTTPException
    from sqlalchemy import select

    from backend.app import auto_paper
    from backend.app.db import (
        AutoPaperDecision,
        AutoPaperState,
        BotState,
        MarketState,
        PaperPendingOrder,
        PaperTrade,
        PaperWallet,
        Session,
    )
    from backend.app.feed import save_candles
    from backend.app.futures_symbols import state_id

    name = genome_name(NATIVE_SEEDS[2])
    with pytest.raises(HTTPException, match="pending paper order"):
        auto_paper.place_native(None, None, None, 86400 + 16 * 3600 - 900, 86400 + 16 * 3600,
                                Setup("long", 100, 95, 115, "test"), name,
                                {"candidates": {name: {"genome": NATIVE_SEEDS[2]}}}, {})
    sig = 100 * 86400 + 16 * 3600 - 900
    learning = {"auto_mode": "paper_learning", "candidates": {name: {"genome": NATIVE_SEEDS[2]}}}
    with Session.begin() as session:
        wallet = session.get(PaperWallet, state_id())
        wallet.quote = dict(lastPrice=102, fairPrice=102, bid1=101.9, ask1=102.1, time=sig + 905, received_at=sig + 905,
                            bar=dict(time=sig + 900, open=102, high=102, low=102, close=102, volume=0))
        wallet.contract = dict(contractSize=1, volUnit=1, minVol=1, maxVol=100000)
        wallet.funding_error = None
        market = session.get(MarketState, state_id())
        market.source, market.last_sync = "mexc_futures", datetime.fromtimestamp(sig + 905, timezone.utc)
        session.get(BotState, 1).heartbeat = datetime.fromtimestamp(sig + 905, timezone.utc)
        state = session.get(AutoPaperState, state_id())
        state.enabled = True
        auto_paper.place_pending(session, state, wallet, sig, sig + 905,
                                 Setup("long", 100, 90, 130, "test"), name, learning, {})
        order_id = session.scalar(select(PaperPendingOrder.id).where(PaperPendingOrder.signal_time == sig))
        assert order_id and session.get(AutoPaperDecision, ("BTC_USDT", sig)).action == "pending"
        save_candles(session, h1_bars([101, 101, 101, 102], sig + 900))
        now = sig + 5 * 900 + 5
        monkeypatch.setattr("time.time", lambda: now)
        wallet.quote = {**wallet.quote, "time": now, "received_at": now}
        market.last_sync = datetime.fromtimestamp(now, timezone.utc)
    auto_paper.sync_pending_orders(now)
    with Session() as session:
        order = session.get(PaperPendingOrder, order_id)
        trade = session.get(PaperTrade, order.trade_id)
        assert order.status == "filled" and trade.target == pytest.approx(trade.entry + 3 * (trade.entry - 90))
        assert trade.monitoring["hold_until"] == sig + 900 + 5 * 86400

def test_backtest_native_h1_confirm_waits_and_keeps_original_deadline():
    base = 100 * 86400 + 12 * 3600
    candles = [Candle(base - (25 - index) * 900, 100, 101, 99, 101 if index == 28 else 100, 1)
               for index in range(520)]
    sig_time = candles[24].time
    args = dict(candles=candles, config=BacktestConfig(), funding=[], fair_candles=candles,
                contract=dict(contractSize=1, volUnit=1, minVol=1, maxVol=10000),
                signal=lambda bars, **_: Setup("long", 100, 90, 130, "test") if bars[-1].time == sig_time else None,
                warmup=21, entry_policy=NATIVE_ENTRY, target_r=3, entry_confirmation="h1", entry_session="utc_12_20")
    with pytest.raises(ValueError, match="entry confirmation"):
        simulate(**args, signal_frame="D1")
    trade = simulate(**args, signal_frame="H4")["trades"][0]
    assert trade["entry_time"] == sig_time + 5 * 900 and trade["exit_time"] == sig_time + 5 * 86400
