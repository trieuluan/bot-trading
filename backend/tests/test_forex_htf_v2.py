import copy
from types import SimpleNamespace

import pytest

from backend.app import forex_htf_v2 as mod
from backend.app.forex_detectors import H1_DETECTORS, M15_TRIGGERS
from backend.app.forex_htf_v2 import (
    EXIT_POLICIES,
    crossover,
    describe,
    evolve_population,
    g0_trees,
    mutate,
    tree_id,
    validate_htf2_tree,
)


def bar(time, close=100.0, open=None, high=101.0, low=99.0):
    return SimpleNamespace(time=time, close=close, open=close if open is None else open,
                           high=high, low=low, tick_volume=100)


def test_g0_has_56_unique_valid_trees():
    trees = g0_trees()
    assert len(trees) == 56
    assert len({tree_id(t) for t in trees}) == 56
    assert all(tree_id(t).startswith("hast2_") and describe(t) for t in trees)
    assert {t["root"]["children"][3]["policy"] for t in trees} == {"partial_be_h1", "partial_be_m15"}
    assert {t["root"]["children"][2]["family"] for t in trees} == set(M15_TRIGGERS)


def test_all_h1_detectors_registered_and_in_schema():
    assert set(H1_DETECTORS) == set(mod.H1_FAMILIES)


def test_validator_rejects_bad_trees():
    tree = g0_trees()[0]
    for edit in (
        lambda t: t["root"]["children"].reverse(),
        lambda t: t["root"]["children"].pop(),
        lambda t: t["root"]["children"][1].update(family="rsi"),
        lambda t: t["root"]["children"][2]["params"].update(lookback=9999),
        lambda t: t["root"]["children"][3].update(policy="x"),
        lambda t: t["root"]["children"][0].update(side="up"),
    ):
        bad = copy.deepcopy(tree)
        edit(bad)
        with pytest.raises(ValueError):
            validate_htf2_tree(bad)


def test_id_ignores_nothing_but_logic():
    a, b = g0_trees()[0], copy.deepcopy(g0_trees()[0])
    assert tree_id(a) == tree_id(b)
    b["root"]["children"][3]["policy"] = "fixed_structural"
    assert tree_id(a) != tree_id(b)


def test_crossover_mutation_children_valid():
    import random
    rng = random.Random(1)
    trees = g0_trees()
    for _ in range(100):
        a, b = rng.sample(trees, 2)
        validate_htf2_tree(mutate(crossover(a, b, rng), rng))
    assert "fixed_structural" in EXIT_POLICIES


def test_all_adaptive_exit_policies_validated_and_mutated():
    import random
    from backend.app.forex_exits import DEFAULTS
    rng = random.Random(42)
    tree = g0_trees()[0]
    seen_policies = set()
    seen_params = False
    for _ in range(500):
        child = mutate(tree, rng)
        validate_htf2_tree(child)
        pol = child["root"]["children"][3]["policy"]
        seen_policies.add(pol)
        if "params" in child["root"]["children"][3]:
            seen_params = True
    assert set(DEFAULTS) <= seen_policies
    assert seen_params


def test_h1_sweep_detector_has_invalidation_and_no_future():
    bars = [bar(i * 3600, high=102, low=98) for i in range(30)]
    bars[-1] = bar(29 * 3600, close=99.0, open=98.5, high=99.5, low=96.0)
    setup = H1_DETECTORS["sweep"](bars, "long")
    assert setup["invalidation"] == 96.0 and setup["level"] == 98
    assert H1_DETECTORS["sweep"](bars[:10], "long") is None


def fill(ledger, tree, n, value, day0=0, version=None):
    from backend.app.forex_htf_v2 import logic_version
    ver = logic_version() if version is None else version
    tid = tree_id(tree)
    for i in range(n):
        t = (day0 + i // 3) * 86400
        ledger["samples"].append({"strategy": tid, "status": "resolved", "net_r_ex_swap": value + (i % 2) * 0.1,
                                  "confirm_time": t, "label_available_at": t + 3600,
                                  "strategy_version": ver, "costs": {"verified": True},
                                  "provenance": "live_forward_htf_ast2_shadow"})


def test_evolution_needs_at_least_one_qualified_parent():
    trees = g0_trees()
    ledger = {"population": trees, "samples": [], "generation": 0, "since": 0}
    assert not evolve_population(ledger, 10**9, 1)
    assert ledger["generation"] == 0


def test_evolution_keeps_56_and_is_rate_limited():
    trees = g0_trees()
    ledger = {"population": trees, "samples": [], "generation": 0, "since": 0}
    fill(ledger, trees[0], 12, 0.5)
    fill(ledger, trees[1], 12, 0.4)
    fill(ledger, trees[2], 12, -0.5)
    assert evolve_population(ledger, 10**9, 7)
    assert len(ledger["population"]) == 56
    assert len({tree_id(t) for t in ledger["population"]}) == 56
    assert ledger["generation"] == 1
    # Rate limited by 3600s
    assert not evolve_population(ledger, 10**9 + 1800, 7)


def test_hast2_never_admitted_without_eligibility(monkeypatch):
    from backend.app.forex_htf_v2 import validate_execution_signal
    from backend.app.exness import ExnessError
    import pytest
    signal = {"strategy": "hast2_abc", "side": "long", "shadow_only": False, "verified_execution": True}
    with pytest.raises(ExnessError, match="v2_execution_disabled"):
        validate_execution_signal(signal, "31000117657")


def test_h1_management_aggregates_only_complete_closed_hours():
    from backend.app.forex_buffer_study import closed_h1_rows
    rows = [bar(i * 900, high=100 + i) for i in range(6)]
    out = closed_h1_rows(rows, 6 * 900)
    assert len(out) == 1 and out[0].high == 103


def test_summary_and_tick_without_account(monkeypatch, tmp_path):
    monkeypatch.setattr(mod, "LEDGER", tmp_path / "x.json")
    monkeypatch.delenv("EXNESS_ACCOUNT_ID", raising=False)
    assert mod.htf_ast2_tick() == "unconfigured"
    s = mod.htf_ast2_summary("")
    assert s["tree_count"] == 56 and s["resolved"] == 0


def test_v2_signal_requires_shadow_and_valid_policy():
    from backend.app.exness import ExnessError
    from backend.app.forex_buffer_study import new_variants
    sig = {"strategy": "hast2_x", "shadow_only": True, "htf_ast_v2": True, "exit_policy": "fixed_structural",
           "side": "long", "stop": 1.0, "target": 2.0, "htf_structure": {"atr": 1.0}}
    row = new_variants(sig, [], [], [], {"trend": "up", "utc_session": 1}, bar(0), {})[0]
    assert row["signal"]["partial_be"] is False
    with pytest.raises(ExnessError):
        new_variants({**sig, "exit_policy": "bad"}, [], [], [], {"trend": "up", "utc_session": 1}, bar(0), {})


def test_migration_archives_old_and_starts_clean_g0(tmp_path):
    from backend.app.forex_migration import migrate_ledger, MIGRATION_ID
    import hashlib, json

    ledger_path = tmp_path / "exness-htf-ast2-shadow.json"
    (tmp_path / "exness-learning.json").write_text(json.dumps({"old": "learner"}))
    (tmp_path / "exness-scalp.json").write_text(json.dumps({"old": "scalp"}))
    (tmp_path / "exness-forward-shadow.json").write_text(json.dumps({"old": "shadow"}))

    account = "test_acc_123"
    ledger = migrate_ledger(ledger_path, account, 1000)
    assert ledger["runtime"] == "htf_v2_only_1"
    assert len(ledger["population"]) == 56
    assert len(ledger["samples"]) == 0
    assert ledger["generation"] == 0

    tag = hashlib.sha256(account.encode()).hexdigest()[:16]
    archive_dir = tmp_path / "archive" / MIGRATION_ID / tag
    manifest = json.loads((archive_dir / "manifest.json").read_text())
    assert manifest["migration"] == MIGRATION_ID
    assert "exness-learning.json" in manifest["files"]
    assert "exness-scalp.json" in manifest["files"]
    assert "exness-forward-shadow.json" in manifest["files"]

    # Second call is idempotent, returns existing without modifying
    second = migrate_ledger(ledger_path, account, 2000)
    assert second["since"] == 1000
    assert second["generation"] == 0


def test_v2_execution_validation_blocks_when_disabled(monkeypatch, tmp_path):
    from backend.app.forex_htf_v2 import validate_execution_signal
    from backend.app.exness import ExnessError
    import pytest

    account = "acc_demo_test"
    monkeypatch.setattr(mod, "LEDGER", tmp_path / "x.json")

    # 1. Execution flag disabled
    monkeypatch.setenv("FOREX_HTF_V2_EXECUTION_ENABLED", "false")
    monkeypatch.setenv("EXNESS_DEMO_EXECUTION_ENABLED", "true")
    with pytest.raises(ExnessError, match="v2_execution_disabled"):
        validate_execution_signal({"strategy": "hast2_test", "shadow_only": False}, account)

    # 2. Exness execution disabled
    monkeypatch.setenv("FOREX_HTF_V2_EXECUTION_ENABLED", "true")
    monkeypatch.setenv("EXNESS_DEMO_EXECUTION_ENABLED", "false")
    with pytest.raises(ExnessError, match="v2_execution_disabled"):
        validate_execution_signal({"strategy": "hast2_test", "shadow_only": False}, account)

    # 3. Both enabled, but signal marked shadow_only or not hast2_
    monkeypatch.setenv("EXNESS_DEMO_EXECUTION_ENABLED", "true")
    with pytest.raises(ExnessError, match="shadow_only_signal"):
        validate_execution_signal({"strategy": "hast2_test", "shadow_only": True}, account)
    with pytest.raises(ExnessError, match="shadow_only_signal"):
        validate_execution_signal({"strategy": "ast_test", "shadow_only": False}, account)

    # 4. No candidates in ledger
    with pytest.raises(ExnessError, match="v2_execution_source_unavailable"):
        validate_execution_signal({"strategy": "hast2_test", "shadow_only": False}, account)

def test_every_exit_policy_has_traits_and_is_reachable_by_mutation():
    import random
    from backend.app.forex_htf_v2 import EXIT_TRAITS
    assert set(EXIT_TRAITS) == set(EXIT_POLICIES)
    rng, seen = random.Random(3), set()
    for _ in range(600):
        child = mutate(g0_trees()[0], rng)
        validate_htf2_tree(child)
        seen.add(child["root"]["children"][3]["policy"])
    assert {"evidence_h1", "evidence_m15", "barrier_partial_h1", "fixed_structural"} <= seen

def test_entry_ranking_needs_no_sample_gate():
    trees = g0_trees()
    ledger = {"population": trees, "samples": [], "generation": 0, "since": 0}
    fill(ledger, trees[1], 12, 0.5)
    ranked = mod.qualified_trees(ledger, 10**9)
    assert len(ranked) == 56
    assert ranked[0][1]["eligible"] is True and ranked[0][0] == tree_id(trees[1])
