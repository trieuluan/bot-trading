import copy

import pytest

from backend.app import futures_learner as learner
from backend.app import futures_retirement as retirement
from backend.app.db import AutoPaperDecision, PaperPendingOrder, PaperTrade, Session
from backend.app.futures_strategies import ENTRY_SEEDS, EXIT_SEEDS, SEEDS, genome_name
from backend.app.futures_symbols import symbol_scope

NOW = 1000000


def samples(name, values, start=0, regime="range"):
    return [{"strategy": name, "candle_time": start + index * 900,
             "entry_time": start + index * 900 + 900, "entry": 100, "risk": 5,
             "status": "resolved", "net_r": value, "side": "long", "regime": regime,
             "label_available_at": start + index * 900 + 1800}
            for index, value in enumerate(values)]


def state_for(weak_values, good_values=None, generation=0, promoted=False):
    state = learner.new_state(0)
    parent = genome_name(SEEDS[0])
    weak_genome = {**SEEDS[0], "lookback": 25}
    weak = genome_name(weak_genome)
    state["candidates"] = {parent: state["candidates"][parent]}
    learner.add_candidate(state, weak_genome, 0)
    state["candidates"][parent]["born_at"] = 0
    state["candidates"][weak].update(generation=generation, promoted=promoted)
    state["samples"] = samples(parent, good_values or [1] * 50) + samples(weak, weak_values)
    return state, parent, weak


def options(state, protected=()):
    return dict(retirement.retirement_candidates(state, retirement.forward_evidence(state, NOW), protected))


@pytest.mark.parametrize("generation", [0, 1, 5])
@pytest.mark.parametrize("promoted", [False, True])
def test_all_generations_and_promoted_share_threshold(generation, promoted):
    state, _, weak = state_for([-0.1] * 49, generation=generation, promoted=promoted)
    assert weak not in options(state)
    state["samples"].append(samples(weak, [-0.1], start=49 * 900)[0])
    assert options(state)[weak]["reason"] == "negative_forward_r"


@pytest.mark.parametrize("changes", [
    {"entry": None}, {"entry": 0}, {"entry": True}, {"risk": 0}, {"risk": float("inf")},
    {"status": "cancelled"}, {"status": "open"}, {"net_r": float("nan")},
    {"label_available_at": NOW + 1}, {"label_available_at": None},
    {"entry_time": -1}, {"candle_time": None}, {"side": "invalid"},
])
def test_invalid_unfilled_cancelled_and_future_samples_do_not_count(changes):
    state, _, weak = state_for([-0.1] * 49)
    state["samples"].append({**samples(weak, [-0.1], start=49 * 900)[0], **changes})
    assert weak not in options(state)


def test_duplicate_sample_keys_do_not_inflate_evidence():
    state, _, weak = state_for([-0.1] * 49)
    state["samples"] += samples(weak, [-0.1]) * 10
    assert weak not in options(state)


def test_drawdown_uses_available_time_and_includes_initial_peak():
    rows = samples("x", [2, -5, -5, 12] + [0.1] * 46)
    assert retirement.metrics(list(reversed(rows)))["max_drawdown_r"] == 10
    stats = retirement.metrics(rows)
    assert stats["mean_r"] > 0
    assert retirement.failure(stats) == "forward_drawdown"
    assert retirement.metrics(samples("x", [-1] * 50))["max_drawdown_r"] == 50
    assert retirement.failure(retirement.metrics(samples("x", [2, -5, -4.99, 12] + [0.1] * 46))) is None


def test_lower_positive_r_without_duplicate_proof_survives():
    state, _, weak = state_for([0.2] * 50)
    for row in state["samples"]:
        if row["strategy"] == weak:
            row["side"] = "short"
    assert options(state) == {}


def test_redundant_evidence_and_normalized_execution_defaults():
    state, parent, weak = state_for([0.2] * 50)
    state["candidates"][weak]["genome"].update(entry_policy="market", exit_profile="default")
    item = options(state)[weak]
    assert item["reason"] == "redundant_forward"
    assert item["retained_strategy"] == parent
    assert item["overlap"]["shared"] == 50 and item["overlap"]["ratio"] == 1


@pytest.mark.parametrize("field,value", [
    ("entry_policy", "body50"), ("exit_policy", "trail"), ("target_r", 3),
    ("stop_buffer", 0.1), ("exit_profile", "breakout"),
])
def test_different_execution_config_is_not_redundant(field, value):
    state, _, weak = state_for([0.2] * 50)
    state["candidates"][weak]["genome"][field] = value
    assert weak not in options(state)


def test_exit_params_defaults_are_normalized():
    genome = {**SEEDS[0], "exit_policy": "time_stop"}
    from backend.app.futures_exits import exit_parameters
    assert retirement.execution_config(genome) == retirement.execution_config(
        {**genome, "exit_params": exit_parameters("time_stop")})


def test_different_exit_params_are_not_redundant():
    state, parent, weak = state_for([0.2] * 50)
    for name, bars in ((parent, 32), (weak, 64)):
        state["candidates"][name]["genome"].update(exit_policy="time_stop", exit_params={"max_bars": bars})
    assert weak not in options(state)


def test_overlap_and_edge_boundaries():
    state, parent, weak = state_for([0.2] * 100, [0.3] * 100)
    right = [row for row in state["samples"] if row["strategy"] == parent]
    left = [row for row in state["samples"] if row["strategy"] == weak]
    for row in left[40:45]:
        row["net_r"] = -0.1
    for row in right[40:45]:
        row["candle_time"] += 1
    assert options(state)[weak]["overlap"]["ratio"] == pytest.approx(95 / 105)
    right[45]["candle_time"] += 1
    left[45]["net_r"] = -0.1
    assert weak not in options(state)


@pytest.mark.parametrize("case", ["few_shared", "low_overlap", "small_edge", "small_context", "context_loss", "unique_profit", "missing_context", "keeper_dd", "keeper_failure"])
def test_redundancy_requires_every_safety_check(case):
    state, parent, weak = state_for([0.2] * 60)
    left = [row for row in state["samples"] if row["strategy"] == weak]
    right = samples(parent, [1] * 60)
    state["samples"] = left + right
    if case == "few_shared":
        for row in right[:11]:
            row.update(candle_time=row["candle_time"] + 1)
    elif case == "low_overlap":
        for row in right[:4]:
            row.update(candle_time=row["candle_time"] + 1)
    elif case == "small_edge":
        for row in right:
            row["net_r"] = 0.29
    elif case == "small_context":
        left[0]["regime"] = right[0]["regime"] = "trend_up"
    elif case == "context_loss":
        for row in left[:10] + right[:10]:
            row["regime"] = "trend_up"
        for row in right[:10]:
            row["net_r"] = 0.1
    elif case == "unique_profit":
        right[30]["candle_time"] += 1
    elif case == "missing_context":
        left[0].pop("regime")
    elif case == "keeper_dd":
        right[30]["net_r"] = -1
    elif case == "keeper_failure":
        right[30]["net_r"] = -10
    assert weak not in options(state)


def test_unmatched_losing_samples_allow_redundancy_and_common_lifetime_only():
    state, parent, weak = state_for([0.2] * 60, [1] * 60)
    for row in state["samples"]:
        if row["strategy"] == weak and row["candle_time"] == 30 * 900:
            row["net_r"] = -0.1
        if row["strategy"] == parent and row["candle_time"] == 30 * 900:
            row["candle_time"] += 1
    assert options(state)[weak]["reason"] == "redundant_forward"
    state["candidates"][weak]["born_at"] = 20 * 900
    assert weak not in options(state)


@pytest.mark.parametrize("kind", ["sample", "decision", "active", "unknown"])
def test_active_protection(kind):
    state, _, weak = state_for([-1] * 50)
    protected = ()
    if kind == "sample":
        state["samples"].append({"strategy": weak, "status": "open"})
    elif kind == "decision":
        state.update(cursor=900, last_decision={"time": 900, "selected": weak})
    elif kind == "active":
        protected = {weak}
    else:
        protected = None
    assert weak not in options(state, protected)


@pytest.mark.parametrize("mutation", ["duplicate", "invalid", "error"])
def test_failed_child_never_archives_existing_tree(monkeypatch, mutation):
    state, parent, _ = state_for([-1] * 50)
    monkeypatch.setattr(learner, "CAP", 2)
    def mutate(genome, rng):
        if mutation == "error":
            raise ValueError("mutation unavailable")
        return copy.deepcopy(state["candidates"][parent]["genome"]) if mutation == "duplicate" else {}
    monkeypatch.setattr(learner, "mutate", mutate)
    before = copy.deepcopy(state["candidates"])
    assert learner.evolve(state, NOW, force=True) == 0
    assert state["candidates"] == before and not state.get("rule_archive")


def test_full_population_without_bad_candidate_stops(monkeypatch):
    state, _, _ = state_for([0.95] * 50)
    monkeypatch.setattr(learner, "CAP", 2)
    before = copy.deepcopy(state["candidates"])
    assert learner.evolve(state, NOW, force=True) == 0
    assert state["candidates"] == before


def test_g0_archive_metadata_no_revival_and_legacy_summary(monkeypatch):
    state, _, weak = state_for([-1] * 50)
    monkeypatch.setattr(learner, "CAP", 2)
    assert learner.evolve(state, NOW, force=True) == 1
    archived = state["rule_archive"][weak]
    assert archived["generation"] == 0
    assert archived["retirement"]["reason"] == "negative_forward_r"
    assert archived["retirement"]["samples"] == 50
    state["candidates"] = {}
    monkeypatch.setattr(learner, "SEEDS", [archived["genome"]])
    monkeypatch.setattr(learner, "RULE_SEEDS", [])
    monkeypatch.setattr(learner, "EXIT_SEEDS", [])
    monkeypatch.setattr(learner, "ENTRY_SEEDS", [])
    learner.sync_seeds(state, NOW)
    assert not state["candidates"]
    assert learner.summary(state, NOW)["retired_strategies"][0]["retirement"]["samples"] == 50
    archived.pop("retirement")
    assert learner.summary(state, NOW)["retired_strategies"][0]["retirement"] is None


@pytest.mark.parametrize("seed", [SEEDS[0], EXIT_SEEDS[0], ENTRY_SEEDS[0]])
def test_archived_real_seeds_never_respawn_or_spawn_children(seed, monkeypatch):
    state = learner.new_state(0)
    name = genome_name(seed)
    archived = state["candidates"][name]
    state.update(candidates={}, rule_archive={name: archived})
    monkeypatch.setattr(learner, "SEEDS", [seed])
    monkeypatch.setattr(learner, "RULE_SEEDS", [seed])
    monkeypatch.setattr(learner, "EXIT_SEEDS", [seed])
    monkeypatch.setattr(learner, "ENTRY_SEEDS", [seed])
    learner.sync_seeds(state, NOW)
    assert state["candidates"] == {}


def test_failed_forward_parent_is_not_used_even_if_replay_profitable(monkeypatch):
    state, parent, weak = state_for([-1] * 50)
    monkeypatch.setattr(learner, "performance", lambda *_: {parent: (50, 1), weak: (1000, 5)})
    assert learner.evolve(state, NOW, force=True) == 1
    child = next(row for name, row in state["candidates"].items() if name not in (parent, weak))
    assert child["parents"] == [parent]


def test_retirement_metadata_ignores_replay_results(monkeypatch):
    base, parent, weak = state_for([-0.1] * 50)
    monkeypatch.setattr(learner, "CAP", 2)
    for historical_r in (-100, 100):
        state = copy.deepcopy(base)
        monkeypatch.setattr(learner, "performance", lambda *_, value=historical_r:
                            {parent: (50, 1), weak: (1000, value)})
        assert learner.evolve(state, NOW, force=True) == 1
        assert state["rule_archive"][weak]["retirement"]["mean_r"] == -0.1


def test_batch_replacement_does_not_reuse_removed_parent(monkeypatch):
    state, parent, weak = state_for([-0.1] * 50)
    other_genome = {**SEEDS[0], "lookback": 30}
    learner.add_candidate(state, other_genome, 0)
    other = genome_name(other_genome)
    state["samples"] += samples(other, [-0.2] * 50)
    monkeypatch.setattr(learner, "CAP", 3)
    assert learner.evolve(state, NOW, max_children=2, force=True) == 2
    assert set(state["rule_archive"]) == {weak, other}
    assert all(row["parents"] == [parent] for name, row in state["candidates"].items() if name != parent)


def test_duplicate_parent_can_retire_without_changing_survivor_genome(monkeypatch):
    state, parent, weak = state_for([0.2] * 50)
    survivor = copy.deepcopy(state["candidates"][parent])
    monkeypatch.setattr(learner, "CAP", 2)
    assert learner.evolve(state, NOW, force=True) == 1
    assert weak not in state["candidates"]
    assert state["candidates"][parent] == survivor
    assert state["rule_archive"][weak]["retirement"]["retained_strategy"] == parent


def test_priorities_use_r_then_drawdown_and_id():
    state, parent, weak = state_for([-0.1] * 50)
    state["candidates"]["drawdown"] = copy.deepcopy(state["candidates"][weak])
    state["samples"] += samples("drawdown", [20, -10] + [0.1] * 48)
    assert [name for name, _ in retirement.retirement_candidates(
        state, retirement.forward_evidence(state, NOW), ())][:2] == [weak, "drawdown"]
    assert parent not in options(state)


def add_pending(session, name, symbol="BTC_USDT", audit=True):
    session.add(PaperPendingOrder(symbol=symbol, signal_time=900, created_at=900, expires_at=4500,
        next_bar=1800, side="long", entry_policy="body50", level=100, stop=95, target=110,
        status="pending", snapshot={"strategy": name, **({"audit": {"strategy": name}} if audit else {})}))


def test_pending_ownership_fail_closed_and_symbol_isolation():
    state, _, weak = state_for([-1] * 50)
    with Session.begin() as session:
        add_pending(session, weak)
        add_pending(session, "unknown", symbol="ETH_USDT", audit=False)
    with Session() as session:
        assert learner.active_strategies(session, state) == {weak}
        with symbol_scope("ETH_USDT"):
            assert learner.active_strategies(session, state) is None


def test_positions_ownership_and_pending_disagreement_fail_closed():
    state, _, weak = state_for([-1] * 50)
    with Session.begin() as session:
        trade = PaperTrade(symbol="BTC_USDT", origin="auto", side="long", entry_time=900,
            entry=100, quantity=1, contracts=1, leverage=1, stop=95, target=110, entry_fee=0)
        session.add(trade)
        session.flush()
        trade_id = trade.id
    with Session() as session:
        assert learner.active_strategies(session, state) is None
    with Session.begin() as session:
        session.add(AutoPaperDecision(symbol="BTC_USDT", candle_time=900, action="opened",
            reason="test", trade_id=trade_id, parameters={"audit": {"strategy": weak}}))
    with Session() as session:
        assert learner.active_strategies(session, state) == {weak}
    with Session.begin() as session:
        add_pending(session, weak, audit=False)
    with Session() as session:
        assert learner.active_strategies(session, state) is None
