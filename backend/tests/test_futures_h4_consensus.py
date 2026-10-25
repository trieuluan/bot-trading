import copy
import random

import pytest

from backend.app import futures_learner as learner
from backend.app.db import FuturesLearningState, Session
from backend.app.feed import save_candles
from backend.app.futures_context import load_signal_context
from backend.app.futures_rule_tree import (
    H4_CONFIRMATIONS,
    H4_FEATURES,
    consensus_slot,
    h4_consensus,
    is_htf_rule,
    matches,
    mutate_htf_rule,
    validate_htf_rule,
    validate_rule,
)
from backend.app.futures_strategies import RULE_SEEDS, crossover, genome_name
from backend.app.futures_symbols import SYMBOLS, symbol_scope
from backend.app.market import Candle


def bars(direction=1, count=960):
    return [Candle(index * 900, 200 + direction * index * .1,
                   202 + direction * index * .1, 198 + direction * index * .1,
                   201 + direction * index * .1, 100) for index in range(count)]


def test_h4_requires_ema_and_one_of_two_confirmations():
    slot = h4_consensus(("ema_distance", "ema_slope"))
    assert matches(slot, bars(), 1, 20)
    assert not matches(slot, bars(), -1, 20)
    slot["children"][2]["feature"] = "swing_structure"  # xác nhận 1 đạt, 2 không
    assert matches(slot, bars(), 1, 20)
    slot["children"][1]["feature"] = "market_alignment"
    assert not matches(slot, bars(), 1, 20)  # 0 xác nhận
    flat = bars(count=960)
    for index, bar in enumerate(flat):
        flat[index] = Candle(bar.time, 300, 301, 299, 300, 100)
    assert not matches(h4_consensus(("ema_distance", "ema_slope")), flat, 1, 20)  # không EMA50

def test_consensus_preserves_depth_and_node_budget_for_h1_m15():
    rule = copy.deepcopy(RULE_SEEDS[0]["rule"])
    rule["children"][1] = {"op": "all", "children": [
        {"feature": feature, "frame": "H1"} for feature in ("bos", "adx_regime", "breakout")
    ]}
    rule["children"][2] = {"op": "any", "children": [
        {"feature": feature, "frame": "M15"} for feature in ("volume", "pin_bar", "engulfing")
    ]}
    assert validate_rule(rule) == 13
    validate_htf_rule(rule, consensus=True)


@pytest.mark.parametrize("change", ("any", "duplicate", "missing", "frame", "trend", "neutral"))
def test_invalid_consensus_rejected(change):
    rule = copy.deepcopy(RULE_SEEDS[0]["rule"])
    slot = rule["children"][0]
    if change == "any":
        slot["op"] = "any"
    elif change == "duplicate":
        slot["children"][2] = copy.deepcopy(slot["children"][1])
    elif change == "missing":
        slot["children"].pop()
    elif change == "frame":
        slot["children"][1]["frame"] = "H1"
    elif change == "trend":
        slot["children"][0]["feature"] = "volume"
    else:
        slot["children"][1]["feature"] = "compression"
    with pytest.raises(ValueError):
        validate_htf_rule(rule, consensus=True)


def test_seeds_evolution_and_crossover_keep_three_distinct_h4_leaves():
    seen = set()
    for seed_index, seed in enumerate(RULE_SEEDS):
        validate_htf_rule(seed["rule"], consensus=True)
        for index in range(80):
            child = mutate_htf_rule(seed["rule"], random.Random(seed_index * 100 + index))
            validate_htf_rule(child, consensus=True)
            assert consensus_slot(child["children"][0])
            seen.update(node["feature"] for node in child["children"][0]["children"][1:])
    assert seen == set(H4_CONFIRMATIONS)
    mate = copy.deepcopy(RULE_SEEDS[1])
    mate["rule"]["children"][0] = h4_consensus(("ema_distance", "adx_regime"))
    child = crossover(RULE_SEEDS[0], mate)
    validate_htf_rule(child["rule"], consensus=True)
    assert child["rule"]["children"][0] == mate["rule"]["children"][0]


@pytest.mark.parametrize("feature", H4_FEATURES)
def test_new_features_reject_wrong_frame_and_short_history(feature):
    for frame in ("H1", "M15"):
        with pytest.raises(ValueError):
            validate_rule({"feature": feature, "frame": frame})
    assert not matches({"feature": feature, "frame": "H4"}, bars(count=799), 1, 20)


def test_market_alignment_needs_btc_and_five_of_ten_closed_aligned_series():
    rule = {"feature": "market_alignment", "frame": "H4"}
    local = bars()
    peers = {symbol: bars(1 if index < 4 else -1)
             for index, symbol in enumerate(symbol for symbol in SYMBOLS if symbol != "BNB_USDT")}
    assert matches(rule, local, 1, 20, context={"benchmarks": peers})
    future = [Candle(local[-1].time + offset * 900, 1, 10000, 1, 10000, 100)
              for offset in range(1, 17)]
    assert matches(rule, local, 1, 20, context={
        "benchmarks": {symbol: rows + future for symbol, rows in peers.items()}})
    for bad in (
        {"BTC_USDT": peers["BTC_USDT"]},
        {**peers, "BTC_USDT": bars(-1)},
        {**peers, "XRP_USDT": bars(-1)},
        {**peers, "ETH_USDT": peers["ETH_USDT"][:-16]},
        {**peers, "ETH_USDT": []},
    ):
        assert not matches(rule, local, 1, 20, context={"benchmarks": bad})


def test_closed_h4_confirmations_ignore_partial_bar():
    candles = bars()
    for feature in ("ema_distance", "ema_slope", "swing_structure", "support_proximity"):
        rule = {"feature": feature, "frame": "H4"}
        expected = matches(rule, candles, 1, 20)
        for direction in (1, -1):
            partial = Candle(candles[-1].time + 900, 100, 10000, .1,
                             10000 if direction == 1 else .1, 100)
            assert matches(rule, candles + [partial], 1, 20) == expected


def test_alt_context_loads_all_peers_without_changing_relative_strength_reference():
    with Session.begin() as session:
        for symbol in SYMBOLS:
            with symbol_scope(symbol):
                save_candles(session, bars())
        with symbol_scope("BNB_USDT"):
            context = load_signal_context(session, 0, 960 * 900)
    assert set(context["benchmarks"]) == set(SYMBOLS) - {"BNB_USDT"}
    rule = {"feature": "relative_strength", "frame": "H1"}
    context["benchmarks"]["BTC_USDT"] = bars(-1)
    assert matches(rule, bars(), 1, 20, context=context)
    assert matches(rule, bars(), 1, 20, context={"btc_candles": bars(-1)})


def test_v1_migration_archives_old_scores_preserves_open_samples_and_is_idempotent():
    old = learner.new_state(1000)
    old["version"] = "futures_htf_v1"
    genome = copy.deepcopy(RULE_SEEDS[0])
    genome["rule"]["children"][0] = {"feature": "trend", "frame": "H4"}
    old_name = genome_name(genome)
    candidate = copy.deepcopy(next(iter(old["candidates"].values())))
    candidate.update(genome=genome, windows=[{"net_pnl": 10}], promoted=True)
    old["candidates"] = {old_name: candidate}
    old.update(enabled=False, auto_mode="paper_learning", cursor=900,
               funding=[{"time": 900, "rate": .001}],
               samples=[{"strategy": old_name, "status": "open", "exit_rule": genome["rule"]}])
    frozen = copy.deepcopy(old)
    with Session.begin() as session:
        session.get(FuturesLearningState, 1).payload = old
    with Session.begin() as session:
        state = learner.read_state(session, 2000)
        assert state["version"] == learner.VERSION
        assert not state["enabled"] and state["auto_mode"] == "paper_learning"
        assert state["cursor"] == 900 and state["funding"] == frozen["funding"]
        assert state["samples"] == frozen["samples"]
        assert state["rule_archive"][old_name] == frozen["candidates"][old_name]
        assert old_name not in state["candidates"] and state["last_decision"] is None
        for candidate in state["candidates"].values():
            validate_htf_rule(candidate["genome"]["rule"], consensus=True)
            assert candidate["born_at"] == 2700
            assert not candidate["windows"] and not candidate["promoted"]
        assert session.get(FuturesLearningState, 1).payload == frozen
        learner.save_state(session, state)
    with Session() as session:
        assert learner.read_state(session, 4000) == state
    assert is_htf_rule(genome["rule"])
    with pytest.raises(ValueError):
        validate_htf_rule(genome["rule"], consensus=True)
