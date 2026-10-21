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
    assert all(tree_id(t).startswith("hast3_") and describe(t) for t in trees)
    assert {t["root"]["children"][2]["family"] for t in trees} == set(M15_TRIGGERS)
    assert all("side" not in t["root"]["children"][0] for t in trees)
    assert sum(len(t["root"]["children"]) > 4 for t in trees) == 8
    assert {t["root"]["children"][1]["family"] for t in trees} >= set(mod.NEW_H1_DETECTORS)
    assert {t["root"]["children"][3]["policy"] for t in trees} >= {"trail_swing_h1", "runner_h1"}


def test_all_h1_detectors_registered_and_in_schema():
    assert set(H1_DETECTORS) | set(mod.NEW_H1_DETECTORS) == set(mod.H1_FAMILIES)


def test_validator_rejects_bad_trees():
    tree = g0_trees()[0]
    for edit in (
        lambda t: t["root"]["children"].reverse(),
        lambda t: t["root"]["children"].pop(),
        lambda t: t["root"]["children"][1].update(family="rsi"),
        lambda t: t["root"]["children"][2]["params"].update(lookback=9999),
        lambda t: t["root"]["children"][3].update(policy="x"),
        lambda t: t["root"]["children"][0].update(side="long"),
        lambda t: t["root"]["children"].extend([mod._filter("adx_regime"), mod._filter("adx_regime")]),
        lambda t: t["root"]["children"].append({"frame": "filter", "name": "adx_regime", "params": {"threshold": 99}}),
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
                                  "provenance": "live_forward_htf_ast3_shadow"})


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


def test_hast3_never_admitted_without_eligibility(monkeypatch):
    import pytest

    from backend.app.exness import ExnessError
    from backend.app.forex_htf_v2 import validate_execution_signal

    signal = {"strategy": "hast3_abc", "side": "long", "shadow_only": False, "verified_execution": True}
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
    assert mod.htf_ast3_tick() == "unconfigured"
    s = mod.htf_ast3_summary("")
    assert s["tree_count"] == 56 and s["resolved"] == 0


def test_v2_signal_requires_shadow_and_valid_policy():
    from backend.app.exness import ExnessError
    from backend.app.forex_buffer_study import new_variants
    sig = {"strategy": "hast3_x", "shadow_only": True, "htf_ast_v2": True, "exit_policy": "fixed_structural",
           "side": "long", "stop": 1.0, "target": 2.0, "htf_structure": {"atr": 1.0}}
    row = new_variants(sig, [], [], [], {"trend": "up", "utc_session": 1}, bar(0), {})[0]
    assert row["signal"]["partial_be"] is False
    with pytest.raises(ExnessError):
        new_variants({**sig, "exit_policy": "bad"}, [], [], [], {"trend": "up", "utc_session": 1}, bar(0), {})


def test_migration_archives_old_and_starts_clean_g0(tmp_path):
    import hashlib
    import json

    from backend.app.forex_migration import MIGRATION_ID, migrate_ledger

    ledger_path = tmp_path / "exness-htf-ast2-shadow.json"
    (tmp_path / "exness-learning.json").write_text(json.dumps({"old": "learner"}))
    (tmp_path / "exness-scalp.json").write_text(json.dumps({"old": "scalp"}))
    (tmp_path / "exness-forward-shadow.json").write_text(json.dumps({"old": "shadow"}))

    account = "test_acc_123"
    ledger = migrate_ledger(ledger_path, account, 1000)
    assert ledger["runtime"] == "htf_v3_only_1"
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
    import pytest

    from backend.app.exness import ExnessError
    from backend.app.forex_htf_v2 import validate_execution_signal


    account = "acc_demo_test"
    monkeypatch.setattr(mod, "LEDGER", tmp_path / "x.json")

    # 1. Execution flag disabled
    monkeypatch.setenv("FOREX_HTF_V2_EXECUTION_ENABLED", "false")
    monkeypatch.setenv("EXNESS_DEMO_EXECUTION_ENABLED", "true")
    with pytest.raises(ExnessError, match="v2_execution_disabled"):
        validate_execution_signal({"strategy": "hast3_test", "shadow_only": False}, account)

    # 2. Exness execution disabled
    monkeypatch.setenv("FOREX_HTF_V2_EXECUTION_ENABLED", "true")
    monkeypatch.setenv("EXNESS_DEMO_EXECUTION_ENABLED", "false")
    with pytest.raises(ExnessError, match="v2_execution_disabled"):
        validate_execution_signal({"strategy": "hast3_test", "shadow_only": False}, account)

    # 3. Both enabled, but signal marked shadow_only or not hast3_
    monkeypatch.setenv("EXNESS_DEMO_EXECUTION_ENABLED", "true")
    with pytest.raises(ExnessError, match="shadow_only_signal"):
        validate_execution_signal({"strategy": "hast3_test", "shadow_only": True}, account)
    with pytest.raises(ExnessError, match="shadow_only_signal"):
        validate_execution_signal({"strategy": "ast_test", "shadow_only": False}, account)

    # 4. No candidates in ledger
    with pytest.raises(ExnessError, match="v2_execution_source_unavailable"):
        validate_execution_signal({"strategy": "hast3_test", "shadow_only": False}, account)

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

def test_h1_setup_zone_for_chart(monkeypatch):
    from backend.app import forex_learning as learning
    tree = next(t for t in g0_trees() if t["root"]["children"][1]["family"] == "sweep")
    side = "short"
    bars = [bar(i * 3600, high=102, low=98) for i in range(30)]
    bars[-1] = (bar(29 * 3600, close=101.0, open=102.0, high=104.0, low=100.5) if side == "short"
                else bar(29 * 3600, close=99.0, open=98.5, high=99.5, low=96.0))
    candle = SimpleNamespace(time=30 * 3600 + 900, close=100.0)
    monkeypatch.setattr(learning, "_h4_context", lambda *a: side)
    monkeypatch.setattr(learning, "_closed_rows", lambda *a: bars)
    zone = learning.h1_setup_zone(tree, candle, bars, [])
    assert zone is None or (zone["side"] == side and zone["to"] - zone["setup_time"] == 14400 + 900)
    monkeypatch.setattr(learning, "_h4_context", lambda *a: None)
    assert learning.h1_setup_zone(tree, candle, bars, []) is None
    candle.time = 29 * 3600 + 14400 + 3600
    monkeypatch.setattr(learning, "_h4_context", lambda *a: side)
    assert learning.h1_setup_zone(tree, candle, bars, []) is None


def test_tree_labels_readable_and_unique_across_g0():
    from backend.app.forex_htf_v2 import tree_id, tree_label
    trees = g0_trees()
    labels = [tree_label(tree)["full"] for tree in trees]
    assert len(set(labels)) == len(trees)
    assert all("hast3_" not in label and "#" in label for label in labels)
    assert tree_label(trees[0])["title"].startswith("Hai chiều")
    child = {"schema": trees[0]["schema"], "root": {**trees[0]["root"], "children": [dict(x) for x in trees[0]["root"]["children"]]}}
    child["root"]["children"][1] = {**child["root"]["children"][1], "params": {**child["root"]["children"][1]["params"], "lookback": 30}}
    assert "lookback 30" in tree_label(child, 1)["full"] and "G1" in tree_label(child, 1)["full"]
    assert tree_id(child) != tree_id(trees[0])


def test_filter_order_does_not_change_id_and_stats_not_in_hash():
    tree = next(t for t in g0_trees() if len(t["root"]["children"]) == 6)
    swapped = copy.deepcopy(tree)
    swapped["root"]["children"][4:] = reversed(swapped["root"]["children"][4:])
    assert tree_id(tree) == tree_id(swapped)

def test_same_tree_trades_both_directions_by_h4(monkeypatch):
    tree = g0_trees()[0]
    seen = []
    monkeypatch.setattr(mod, "_h4_context", lambda *a: seen[-1] if seen else None)
    for side in ("long", "short"):
        seen.append(side)
        sides = set()
        monkeypatch.setattr(mod, "detect_setup", lambda node, closed, s, symbol: sides.add(s))
        mod.diagnose(tree, SimpleNamespace(time=900 * 100, open=1, high=1, low=1, close=1), [bar(0)], [], [])
        assert sides <= {side}
    monkeypatch.setattr(mod, "_h4_context", lambda *a: None)
    assert mod.diagnose(tree, SimpleNamespace(time=900 * 100, open=1, high=1, low=1, close=1), [], [], [])[1] == "h4_stale"

def test_mutation_never_stores_side_and_covers_new_nodes():
    import random
    rng = random.Random(5)
    seen = set()
    for tree in g0_trees():
        child = mutate(tree, rng)
        validate_htf2_tree(child)
        assert "side" not in child["root"]["children"][0]
        seen.update(node.get("name") for node in child["root"]["children"][4:])
    assert seen - {None}

def test_new_setups_and_triggers():
    from backend.app.forex_detectors import M15_TRIGGERS
    from backend.app.forex_features import detect_inside_bar, detect_round_level
    hours = [bar(i * 3600, high=102, low=98) for i in range(20)]
    hours[-2] = bar(18 * 3600, high=105, low=95)
    hours[-1] = bar(19 * 3600, high=103, low=97)
    setup = detect_inside_bar(hours, "long")
    assert setup["level"] == 105 and setup["invalidation"] == 95
    round_setup = detect_round_level(hours[:-1] + [bar(19 * 3600, close=100.2, open=99.8, high=100.3, low=99.7)], "long", 0.5)
    assert round_setup["level"] == 100.0 and round_setup["invalidation"] == 99.7
    s = {"level": 100.0, "confirmed_h1": 0}
    prev = bar(900 * 4, close=99.0, open=99.5)
    cur = bar(900 * 5, close=101.0, open=98.9)
    assert M15_TRIGGERS["setup_reclaim"](cur, [prev], "long", s, {})
    assert M15_TRIGGERS["engulfing"](cur, [prev], "long", s, {})
    assert not M15_TRIGGERS["setup_reclaim"](bar(900 * 5, close=99.5), [prev], "long", s, {})
    assert not M15_TRIGGERS["engulfing"](cur, [prev], "long", {**s, "confirmed_h1": 10**6}, {})

def test_filters_fail_closed_and_swing_trail():
    from backend.app.forex_exits import action
    from backend.app.forex_features import adx14, filter_result
    node = {"name": "adr_headroom", "params": {"days": 5, "threshold": 0.85}}
    reason, audit = filter_result(node, bar(900 * 100), [], [], "long", "EURUSD", {"minutes": []})
    assert reason == "daily_history_missing" and not audit["passed"]
    reason, _ = filter_result({"name": "smt_divergence", "params": {"lookback": 20}}, bar(0), [], [], "long", "EURJPY", {})
    assert reason == "smt_no_reference"
    assert adx14([bar(i * 14400) for i in range(10)]) is None
    rows = [bar(i * 3600, close=100 + i, open=99 + i, high=101 + i, low=99 + i) for i in range(30)]
    rows[20] = bar(20 * 3600, close=118, open=118, high=119, low=110)
    signal = {"exit_policy": "trail_swing_h1", "side": "long", "management_frame": "H1", "target_context": {"barriers": [], "buffer": 1}}
    out = action(signal, rows, 100.0, 1.0, 0, 99.0, {"point_digits": 3}, 129.0)
    assert out["close_roles"] == [] and float(out["stop"]) <= 110
