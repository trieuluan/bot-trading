import copy
import math
import random

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from backend.app import futures_learner as learner
from backend.app.auto_paper import auto_tick
from backend.app.db import AutoPaperDecision, FuturesLearningState, MarketState, PaperTrade, Session
from backend.app.feed import save_candles
from backend.app.futures_strategies import RULE_SEEDS, SEEDS, genome_name, mutate, strategy_levels, strategy_setup, validate_genome
from backend.app.main import app
from backend.app.market import Candle
from backend.app.strategy import Setup
from backend.tests.test_auto_paper import ready as auto_ready

client = TestClient(app)


@pytest.fixture
def ready(monkeypatch):
    return auto_ready.__wrapped__(monkeypatch)


def candle(offset, opening=100, high=101, low=99, closing=100):
    return Candle(offset * 900, opening, high, low, closing, 1)


def sample(**changes):
    return {"strategy": "x", "candle_time": 0, "entry_time": 900, "side": "long", "stop": 95,
            "target": 110, "regime": "range", "status": "open", "entry": None, "net_r": None,
            "label_available_at": None, **changes}


def candidate(**changes):
    window = {"start_time": 0, "end_time": learner.WINDOW_SECONDS, "trade_count": 120, "net_pnl": 5,
              "max_drawdown_percent": 4, "gains": 200, "losses": 100}
    return {"genome": SEEDS[0], "born_at": 0, "parents": [], "generation": 0,
            "windows": [window], "promoted": False, **changes}


def test_next_open_entry_stop_first_and_delayed_label():
    trade = sample()
    learner.advance(trade, candle(0), candle(0), [])
    assert trade["entry"] is None
    learner.advance(trade, candle(1, high=120, low=90), candle(1), [])
    assert trade["exit_reason"] == "stop" and trade["net_r"] < 0
    assert trade["label_available_at"] == 1800
    assert learner.score([trade], "x", "long", "range", 1799) == math.inf
    assert math.isfinite(learner.score([trade], "x", "long", "range", 1800))


def test_short_stop_and_funding_charged_only_for_carried_position():
    trade = sample(side="short", stop=110, target=90)
    learner.advance(trade, candle(1), candle(1), [{"time": 900, "rate": .01}])
    assert trade["funding_cost"] == 0
    learner.advance(trade, candle(2, high=120, low=80), candle(2), [{"time": 1800, "rate": .01}])
    assert trade["funding_cost"] == -1
    assert trade["exit_reason"] == "stop" and trade["net_r"] < 0


def test_missing_entry_or_gap_through_target_excludes_label():
    missing = sample()
    learner.advance(missing, candle(2), candle(2), [])
    assert missing["status"] == "excluded"
    invalid = sample()
    learner.advance(invalid, candle(1, opening=120, high=121, low=119, closing=120), candle(1), [])
    assert invalid["status"] == "excluded"


@pytest.mark.parametrize("field,value", [("trade_count", 99), ("gains", 130),
                                         ("max_drawdown_percent", 10), ("net_pnl", -1),
                                         ("start_time", 900)])
def test_gate_requires_all_forward_criteria(field, value):
    rule = candidate()
    assert learner.qualification(rule)["eligible"]
    rule["windows"][0][field] = value
    assert not learner.qualification(rule)["eligible"]
    assert not learner.qualification(candidate(windows=[]))["eligible"]


def test_bounded_unique_children_and_frozen_parents():
    state = learner.new_state(1000)
    before = copy.deepcopy(state["candidates"])
    seeds = len(SEEDS) + len(RULE_SEEDS) + len(learner.EXIT_SEEDS)
    assert seeds < len(before) <= 2 * seeds
    for name, rule in before.items():
        validate_genome(rule["genome"])
        assert genome_name(rule["genome"]) == name and rule["born_at"] > 1000
    name = next(iter(before))
    state["samples"] = [sample(strategy=name, status="resolved", net_r=1, label_available_at=2000)] * 20
    learner.evolve(state, 30000)
    assert len(state["candidates"]) <= learner.CAP
    assert len(state["candidates"]) > len(before)
    assert all(state["candidates"][key] == value for key, value in before.items())
    assert mutate(SEEDS[0], random.Random(1)) != SEEDS[0]


def test_cannot_promote_shadow_or_fall_back_to_legacy():
    state = learner.new_state(1000)
    name = next(iter(state["candidates"]))
    with Session.begin() as session:
        learner.save_state(session, state)
    assert client.post("/api/workspaces/mexc_futures/learning/promotion",
                       json={"strategy": name, "promoted": True}).status_code == 409
    assert client.post("/api/workspaces/mexc_futures/learning/config",
                       json={"enabled": True, "auto_mode": "learner"}).status_code == 200
    with Session() as session, pytest.raises(HTTPException):
        learner.select_for_auto(session, 900, 1000)


@pytest.mark.parametrize("promoted", [False, True])
def test_auto_executes_only_explicitly_promoted_qualified_rule(ready, promoted):
    clock, seed = ready
    stamp = seed()
    state = learner.new_state(clock[0])
    name = next(iter(state["candidates"]))
    state["candidates"][name] = candidate(promoted=promoted)
    state["auto_mode"] = "learner"
    state["last_decision"] = {
        "time": stamp, "mode": "qualified" if promoted else "shadow", "selected": name,
        "setup": {"side": "long", "entry": 105, "stop": 90, "target": 135, "reason": "test learner"},
    }
    with Session.begin() as session:
        learner.save_state(session, state)
    auto_tick()
    with Session() as session:
        assert session.scalar(select(func.count()).select_from(PaperTrade)) == int(promoted)
        decision = session.get(AutoPaperDecision, ("BTC_USDT", stamp))
        assert decision.action == ("opened" if promoted else "no_setup")
        if promoted:
            assert decision.parameters["audit"]["strategy"] == name
            assert "range_high" not in decision.parameters["audit"]


def test_worker_forward_cursor_restart_dedup_and_fair_coverage():
    now = 18000005
    stamp = now // 900 * 900 - 900
    state = learner.new_state(now - 3600)
    bars = [Candle(stamp - offset * 900, 100, 101, 99, 100, 1) for offset in reversed(range(30))]
    state["cursor"] = stamp - 900
    with Session.begin() as session:
        session.get(MarketState, 1).source = "mexc_futures"
        learner.save_state(session, state)
        save_candles(session, bars)

    class Adapter:
        def history(self, start, end, now, fair=False):
            return [bar for bar in bars if start <= bar.time < end]

        def funding_history(self, start, end):
            return [{"time": stamp - 28800, "rate": 0.0001, "cycle_hours": 8}]

    learner.learning_tick(now, Adapter())
    with Session() as session:
        first = copy.deepcopy(session.get(FuturesLearningState, 1).payload)
        assert first["cursor"] == stamp and first["error"] is None
    learner.learning_tick(now, Adapter())
    with Session() as session:
        assert session.get(FuturesLearningState, 1).payload == first


def test_step_idempotent_and_rejects_missing_bar():
    state = learner.new_state(1)
    bars = [candle(offset) for offset in range(30)]
    learner.step(state, bars, bars[-1], 30000)
    before = copy.deepcopy(state)
    learner.step(state, bars, bars[-1], 30000)
    assert state == before
    with pytest.raises(ValueError, match="gap"):
        learner.step(state, bars + [candle(31)], candle(31), 30000)

def test_strategy_observations_include_waiting_and_signal_states(monkeypatch):
    state = learner.new_state(1)
    names = list(state["candidates"])
    state["candidates"][names[0]]["born_at"] = 3600
    signal_genome = state["candidates"][names[1]]["genome"]
    monkeypatch.setattr(learner, "strategy_setup", lambda genome, *args, **kwargs:
        Setup("long", 100, 95, 110, "test") if genome == signal_genome else None)
    learner.step(state, [candle(2)], candle(2), 2705)
    decision = state["last_decision"]
    rows = {row["strategy"]: row for row in decision["strategies"]}
    assert len(rows) == len(state["candidates"])
    assert rows[names[0]]["status"] == "warming_up"
    assert rows[names[1]]["status"] == "signal"
    assert rows[names[1]]["side"] == "long"
    assert rows[names[1]]["setup"] == {
        "side": "long", "entry": 100, "stop": 95, "target": 110, "reason": "test"}
    assert rows[names[0]]["setup"] is None and rows[names[0]]["levels"] == []
    assert any(row["status"] == "no_signal" for row in rows.values())
    assert decision["candidates"] == sum(row["status"] == "signal" for row in rows.values())
    with Session.begin() as session:
        state["error"] = "test feed gap"
        learner.save_state(session, state)
    response = client.get("/api/paper/auto")
    assert response.status_code == 200
    enriched = response.json()["learner_decision"]
    for row in enriched["strategies"]:
        assert row.pop("genome") == state["candidates"][row["strategy"]]["genome"]
    assert enriched == decision
    assert response.json()["learner_enabled"] is True
    assert response.json()["learner_error"] == "test feed gap"

def test_chart_reference_levels_exclude_signal_bar_and_require_history():
    bars = [candle(offset) for offset in range(20)] + [candle(20, high=150, low=50)]
    assert strategy_levels(SEEDS[0], bars) == [
        {"title": "Đỉnh 20 M15", "price": 101}, {"title": "Đáy 20 M15", "price": 99}]
    assert strategy_levels(SEEDS[0], bars[:20]) == []

def test_chart_overlays_cover_every_seed_feature():
    bars = [Candle(index * 900, 100 + index % 7, 102 + index % 7, 98 + index % 5,
                   100 + index % 6, 1) for index in range(400)]
    for seed in RULE_SEEDS:
        chart = learner.strategy_chart(seed, bars)
        assert all({"title", "price", "from", "to"} <= set(row) for row in chart["levels"])
        assert all(len(row["points"]) >= 1 for row in chart["curves"])
        assert chart["overlay_details"] and all(row["message"] or row["drawn"] for row in chart["overlay_details"])
    ema_seed = RULE_SEEDS[1]
    assert any(row["title"].startswith("EMA50") for row in learner.strategy_chart(ema_seed, bars)["curves"])
    assert learner.strategy_chart(ema_seed, [])["levels"] == []

def test_paper_learning_uses_unqualified_signal_and_records_selection(ready, monkeypatch):
    clock, seed = ready
    stamp = seed()
    state = learner.new_state(clock[0] - 3600)
    state["auto_mode"] = "paper_learning"
    monkeypatch.setattr(learner, "strategy_setup", lambda *args, **kwargs: Setup(
        side="long", entry=105, stop=90, target=135, reason="paper learning test"))
    bars = [Candle(stamp, 100, 106, 99, 105, 1)]
    learner.step(state, bars, bars[-1], clock[0])
    selected = state["last_decision"]["selected"]
    assert state["last_decision"]["mode"] == "paper_learning"
    assert not state["candidates"][selected]["promoted"]
    assert not learner.qualification(state["candidates"][selected])["eligible"]
    with Session.begin() as session:
        learner.save_state(session, state)
    auto_tick()
    auto_tick()
    with Session() as session:
        assert session.scalar(select(func.count()).select_from(PaperTrade)) == 1
        decision = session.get(AutoPaperDecision, ("BTC_USDT", stamp))
        audit = decision.parameters["audit"]
        assert audit["strategy"] == selected and audit["auto_mode"] == "paper_learning"
        assert audit["selection_reason"] == "explore_unseen_context"
        assert "CHƯA KIỂM CHỨNG" in decision.reason

@pytest.mark.parametrize("condition", ["no_signal", "stale_decision", "error", "paused", "daily_limit", "expired"])
def test_paper_learning_keeps_entry_guards(ready, condition):
    clock, seed = ready
    stamp = seed()
    state = learner.new_state(clock[0] - 3600)
    state["auto_mode"] = "paper_learning"
    name = next(iter(state["candidates"]))
    state["last_decision"] = {
        "time": stamp, "mode": "paper_learning", "auto_mode": "paper_learning", "selected": name,
        "setup": {"side": "long", "entry": 105, "stop": 90, "target": 135, "reason": "test"},
    }
    if condition == "no_signal":
        state["last_decision"].update(mode="abstain", setup=None, selected=None)
    elif condition == "stale_decision":
        state["last_decision"]["time"] -= 900
    elif condition == "error":
        state["error"] = "missing candles"
    elif condition == "paused":
        state["enabled"] = False
    elif condition == "expired":
        clock[0] += 61
    with Session.begin() as session:
        if condition == "daily_limit":
            from backend.app.db import AutoPaperState
            session.get(AutoPaperState, 1).daily_loss_percent = 0
        learner.save_state(session, state)
    auto_tick()
    with Session() as session:
        assert session.scalar(select(func.count()).select_from(PaperTrade)) == 0

def test_paper_learning_config_explicit_and_preserves_promotion(ready):
    _, seed = ready
    seed(enabled=False)
    response = client.post("/api/workspaces/mexc_futures/learning/config",
                           json={"enabled": True, "auto_mode": "paper_learning"})
    assert response.status_code == 200
    assert response.json()["promoted"] == 0
    assert not client.get("/api/paper/auto").json()["enabled"]
    assert client.get("/api/paper/auto").json()["auto_mode"] == "paper_learning"
    assert client.post("/api/workspaces/mexc_futures/learning/config",
                       json={"enabled": False, "auto_mode": "paper_learning"}).status_code == 200
    seed(enabled=True)
    assert client.post("/api/workspaces/mexc_futures/learning/config",
                       json={"enabled": True, "auto_mode": "legacy"}).status_code == 422

def test_paper_learning_does_not_starve_tied_unseen_candidates(monkeypatch):
    state = learner.new_state(1)
    state["auto_mode"] = "paper_learning"
    monkeypatch.setattr(learner, "strategy_setup", lambda *args, **kwargs: Setup(
        side="long", entry=100, stop=95, target=110, reason="tie test"))
    picks = set()
    for offset in range(2, 22):
        fresh = copy.deepcopy(state)
        bars = [candle(offset)]
        learner.step(fresh, bars, bars[-1], offset * 900 + 905)
        picks.add(fresh["last_decision"]["selected"])
    assert len(picks) > 1

def test_learning_freshness_tracks_closed_bar_not_intrabar_age():
    state = learner.new_state(1)
    state["cursor"] = 900
    assert not learner.summary(state, 2699)["stale"]
    assert not learner.summary(state, 2820)["stale"]
    assert learner.summary(state, 2821)["stale"]

def test_htf_seeds_emit_valid_setups_and_sync():
    bars = [candle(offset) for offset in range(120)]
    bars.append(Candle(120 * 900, 100, 130, 99, 129, 50))
    state = learner.new_state(1000)
    old = SEEDS[-1]
    for name in [genome_name(old)] + [n for n, c in state["candidates"].items() if c["parents"] == [genome_name(old)]]:
        del state["candidates"][name]
    learner.sync_seeds(state, 2000)
    assert genome_name(old) in state["candidates"]

def test_evolution_uses_history_replaces_weakest_and_keeps_parents():
    state = learner.new_state(1000)
    state["candidates"] = dict(list(state["candidates"].items())[:learner.CAP])
    seeds = [n for n, c in state["candidates"].items() if not c["parents"]]
    state["samples"] = [sample(strategy=seeds[0], status="resolved", net_r=2, label_available_at=2000)] * 10
    for index in range(1000):
        if len(state["candidates"]) >= learner.CAP:
            break
        learner.add_candidate(state, mutate(SEEDS[index % len(SEEDS)], random.Random(index)), 0, (seeds[1],))
    weak = next(n for n, c in state["candidates"].items() if c["generation"] and n not in seeds)
    state["samples"] += [sample(strategy=weak, status="resolved", net_r=-1, label_available_at=2000)] * 10
    parents = {n: c["genome"] for n, c in state["candidates"].items() if not c["generation"]}
    made = learner.evolve(state, 99999, force=True)
    assert made == 1 and len(state["candidates"]) <= learner.CAP and weak not in state["candidates"]
    assert all(state["candidates"][n]["genome"] == g for n, g in parents.items())
    assert learner.evolve(state, 99999 + 10) == 0

@pytest.mark.parametrize("parent_count", [1, 2, 4])
def test_evolution_records_distinct_parents_or_single_mutation(monkeypatch, parent_count):
    state = learner.new_state(1000)
    state["candidates"] = {name: row for name, row in state["candidates"].items()
                           if row["generation"] == 0}
    parents = list(state["candidates"])[:parent_count]
    monkeypatch.setattr(learner, "performance", lambda *args: {name: (20, 1) for name in parents})
    if parent_count == 1:
        monkeypatch.setattr(learner, "crossover", lambda *args: pytest.fail("Single parent must only mutate"))
    before = set(state["candidates"])
    assert learner.evolve(state, 99999, force=True) == 1
    child = next(row for name, row in state["candidates"].items() if name not in before)
    assert len(child["parents"]) == min(parent_count, 2)
    assert len(set(child["parents"])) == len(child["parents"])

def test_duplicate_children_ignore_boolean_order_and_default_fields():
    from backend.app.futures_strategies import genome_fingerprint
    state = learner.new_state(1000)
    parent = SEEDS[0]
    duplicate = copy.deepcopy(parent)
    duplicate["exit_profile"] = "default"
    duplicate["rule"]["children"].reverse()
    assert genome_name(duplicate) != genome_name(parent)
    assert genome_fingerprint(duplicate) == genome_fingerprint(parent)
    before = len(state["candidates"])
    learner.add_candidate(state, duplicate, 2000, (genome_name(parent),))
    assert len(state["candidates"]) == before
    for field, value in (("lookback", 25), ("exit_policy", "trail")):
        assert genome_fingerprint({**parent, field: value}) != genome_fingerprint(parent)

def test_evolution_rejects_unchanged_child(monkeypatch):
    state = learner.new_state(1000)
    parent = next(iter(state["candidates"]))
    monkeypatch.setattr(learner, "performance", lambda *args: {parent: (20, 1)})
    monkeypatch.setattr(learner, "mutate", lambda genome, rng: copy.deepcopy(genome))
    before = copy.deepcopy(state["candidates"])
    assert learner.evolve(state, 99999, force=True) == 0
    assert state["candidates"] == before

def test_htf_mutations_allow_h1_trend_without_duplicates():
    from backend.app.futures_rule_tree import HTF_CONTEXT_FEATURES, mutate_htf_rule, nodes, validate_htf_rule
    from backend.app.futures_strategies import genome_fingerprint
    assert "trend" in HTF_CONTEXT_FEATURES
    found_h1_trend = False
    for seed in SEEDS:
        for index in range(300):
            child = mutate_htf_rule(seed["rule"], random.Random(index))
            validate_htf_rule(child)
            found_h1_trend |= any(node.get("feature") == "trend" and node.get("frame") == "H1"
                                  for _, node in nodes(child))
    assert found_h1_trend
    aligned = copy.deepcopy(SEEDS[0])
    aligned["rule"]["children"][1] = {"feature": "trend", "frame": "H1"}
    validate_htf_rule(aligned["rule"])
    duplicate = copy.deepcopy(aligned)
    duplicate["rule"]["children"][1]["frame"] = "H4"
    assert genome_fingerprint(aligned) != genome_fingerprint(duplicate)

def test_rule_tree_can_enter_auto_paper(ready):
    state = learner.new_state(1000)
    rule_name = next(n for n, c in state["candidates"].items() if c["genome"]["family"] == "rule_tree")
    candidate = state["candidates"][rule_name]
    assert not learner.shadow_only(candidate["genome"])
    state["auto_mode"] = "paper_learning"
    state["enabled"] = True
    setup = Setup("long", 100, 95, 110, "rule tree test setup")
    state["last_decision"] = {
        "time": 2000, "regime": "trend_up", "candidates": 1,
        "mode": "paper_learning", "auto_mode": "paper_learning",
        "selection_reason": "explore_unseen_context",
        "selected": rule_name, "setup": setup.__dict__,
        "strategies": [],
    }
    with Session.begin() as session:
        learner.save_state(session, state)
    with Session() as session:
        handled, picked_setup, picked_name = learner.select_for_auto(session, 2000, 2000)
        assert handled and picked_name == rule_name and picked_setup.entry == 100


def test_sync_seeds_reserves_room_for_new_roots_at_previous_capacity():
    state = learner.new_state(1000)
    new = [seed for seed in RULE_SEEDS if any(
        node.get("feature") in ("pdh_pdl", "adx_regime")
        for _, node in learner.nodes(seed["rule"]))]
    for seed in new:
        state["candidates"].pop(genome_name(seed), None)
    previous_cap = 96 + len(learner.EXIT_SEEDS)
    rng = random.Random(42)
    for _ in range(1000):
        if len(state["candidates"]) >= previous_cap:
            break
        learner.add_candidate(state, mutate(SEEDS[0], rng), 1000, (genome_name(SEEDS[0]),))
    assert len(state["candidates"]) == previous_cap
    before = copy.deepcopy(state["candidates"])
    learner.sync_seeds(state, 2000)
    for seed in new:
        added = state["candidates"][genome_name(seed)]
        assert added["generation"] == 0 and added["parents"] == []
        assert added["born_at"] > 2000
    assert all(state["candidates"][name] == candidate for name, candidate in before.items())
    assert len(state["candidates"]) <= learner.CAP
    frozen = copy.deepcopy(state)
    learner.sync_seeds(state, 2000)
    assert state == frozen
