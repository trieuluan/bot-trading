import copy
import json
import random

import pytest

from backend.app.forex_entry_policy import POLICIES, entry_node, tree_id, with_entry
from backend.app.forex_htf_v2 import mutate, validate_htf2_tree
from backend.app.forex_htf_v4 import (
    PROVENANCE,
    assign_tree,
    candidates,
    evolve_v4_population,
    initialize,
    logic_version,
    new_ledger,
    read_ledger,
    summary,
    tree_stats,
)


def outcome(identifier="tree", setup="s", net_r=1, **updates):
    sample = {"strategy": identifier, "setup_id": setup, "side": "long", "confirm_time": 1000,
              "status": "resolved", "provenance": PROVENANCE, "costs": {"verified": True},
              "strategy_version": "version", "net_r_ex_swap": net_r, "label_available_at": 9000,
              "signal": {"available_at": 1900, "entry_policy": {"policy": "market", "expires_at": 9000}}}
    return {**sample, **updates}


def evolution_ledger():
    ledger = new_ledger("test", "EURUSD", 0)
    first, second = [tree_id(tree) for tree in ledger["population"][:2]]
    ledger["samples"] = [outcome(identifier, str(index), value)
                         for identifier, value in ((first, 1), (second, -1)) for index in range(10)]
    return ledger, first, second


def test_independent_seed_and_roundtrip(tmp_path):
    path = tmp_path / "v4.json"
    ledger = initialize(path, "a", "EURUSD", 1000)
    assert len(ledger["population"]) == len(ledger["members"]) == 56
    assert {policy: sum(tree["root"]["children"][3]["policy"] == policy for tree in ledger["population"])
            for policy in POLICIES} == dict.fromkeys(POLICIES, 14)
    assert not {"bundles", "retired_bundles", "rr_counter"} & ledger.keys()
    assert read_ledger("a", "EURUSD", path=path) == ledger
    ledger["population_model"] = "legacy"
    path.write_text(json.dumps(ledger))
    with pytest.raises(ValueError, match="migration required"):
        read_ledger("a", "EURUSD", path=path)


def test_per_tree_fitness_needs_no_other_policy():
    samples = [outcome(setup=str(index)) for index in range(10)]
    assert not tree_stats({}, samples[:9], 10000, "version")["ready"]
    assert tree_stats({}, samples, 10000, "version") == {
        "resolved_count": 10, "ready": True, "eligible": True, "mean_r": 1}
    assert tree_stats({}, samples + [samples[0]], 10000, "version")["resolved_count"] == 10


@pytest.mark.parametrize("updates", ({"status": "open"}, {"status": "ambiguous"},
    {"costs": {"verified": False}}, {"strategy_version": "old"}, {"provenance": "old"},
    {"net_r_ex_swap": float("nan")}, {"label_available_at": 10000}))
def test_fitness_rejects_bad_outcomes(updates):
    assert tree_stats({}, [outcome(**updates)], 10000, "version")["resolved_count"] == 0


def test_unfilled_expiry_zero_r_but_market_and_filled_expiry_not_counted():
    sample = outcome(status="expired", reason="entry_expired")
    sample["signal"]["entry_policy"]["policy"] = "stop_trigger_break"
    assert tree_stats({}, [sample], 10000, "version")["mean_r"] == 0
    assert tree_stats({}, [{**sample, "execution": {}}], 10000, "version")["resolved_count"] == 0
    sample["signal"]["entry_policy"]["policy"] = "market"
    assert tree_stats({}, [sample], 10000, "version")["resolved_count"] == 0


def test_evolution_replaces_one_tree_and_keeps_own_entry(monkeypatch, tmp_path):
    ledger, parent, target = evolution_ledger()
    seed = copy.deepcopy(ledger["population"][0])
    expected = with_entry(seed, entry_node("stop_trigger_break", {"wait_bars": 2, "stop_buffer_atr": .05}))
    monkeypatch.setattr("backend.app.forex_htf_v2.mutate", lambda *args: expected)
    assert evolve_v4_population(ledger, 10000, random_seed=1, version="version")
    assert len(ledger["population"]) == 56
    assert ledger["members"][target]["retired_at"] == 10000
    child = ledger["members"][tree_id(expected)]
    assert child["parents"] == [parent] and child["generation"] == 1
    assert child["tree"]["root"]["children"][3]["params"]["wait_bars"] == 2
    assert len(ledger["members"]) == 57 and "bundles" not in ledger
    path = tmp_path / "v4.json"
    path.write_text(json.dumps(ledger))
    assert read_ledger("test", "EURUSD", path=path)["generation"] == 1


def test_duplicate_child_cannot_retire_or_mutate_parent(monkeypatch):
    ledger, parent, _ = evolution_ledger()
    before = copy.deepcopy(ledger)
    monkeypatch.setattr("backend.app.forex_htf_v2.mutate", lambda *args: ledger["members"][parent]["tree"])
    assert not evolve_v4_population(ledger, 10000, random_seed=1, version="version")
    assert ledger == before


@pytest.mark.parametrize("source", ("broker", "shadow"))
def test_evolution_preserves_busy_tree(source):
    ledger, _, target = evolution_ledger()
    if source == "shadow":
        ledger["samples"].append(outcome(target, "busy", status="open"))
    assert not evolve_v4_population(ledger, 10000, version="version",
                                    busy_strategies={target} if source == "broker" else ())


def test_entry_mutation_changes_policy_and_wait_time_without_replicating(monkeypatch):
    monkeypatch.setenv("FOREX_ENTRY_PILOT_ENABLED", "true")
    original = new_ledger("a", "EURUSD", 0)["population"][2]
    before = copy.deepcopy(original)
    children = [mutate(original, random.Random(seed), "EURUSD") for seed in range(300)]
    assert original == before
    for child in children:
        validate_htf2_tree(child)
    nodes = [child["root"]["children"][3] for child in children]
    assert {node["policy"] for node in nodes} == set(POLICIES)
    assert {node["params"].get("wait_bars") for node in nodes} >= {1, 2, 3, 4}


def test_assignment_balances_trees_not_fixed_policy_slots_and_never_remaps(tmp_path):
    ledger = new_ledger("a", "EURUSD", 0)
    trees = ledger["population"][:4]
    def samples(side):
        return [{"strategy": tree_id(tree), "status": "waiting", "side": side,
                 "signal": {"entry_policy": {"policy": tree["root"]["children"][3]["policy"]}}} for tree in trees]
    assigned = [assign_tree(ledger, str(index), samples("long"), index) for index in range(4)]
    assert len(set(assigned)) == 4
    assert assign_tree(ledger, "0", samples("long"), 5) == assigned[0]
    short = assign_tree(ledger, "short", samples("short"), 6)
    assert ledger["members"][short]["tree"]["root"]["children"][3]["policy"] != "limit_setup_mid"
    assert assign_tree(ledger, "invalid", [{**sample, "status": "excluded"} for sample in samples("long")], 7) is None
    path = tmp_path / "v4.json"
    path.write_text(json.dumps(ledger))
    read_ledger("a", "EURUSD", path=path)
    ledger["assignments"]["0"]["policy"] = "fake"
    path.write_text(json.dumps(ledger))
    with pytest.raises(ValueError, match="assignment mismatch"):
        read_ledger("a", "EURUSD", path=path)


def test_summary_exposes_only_individual_progress_and_verified_weighted_stats(monkeypatch):
    ledger, first, second = evolution_ledger()
    monkeypatch.setattr("backend.app.forex_htf_v4.read_ledger", lambda *_args, **kwargs: ledger)
    monkeypatch.setattr("backend.app.forex_htf_v4.logic_version", lambda: "version")
    view = summary("test", "EURUSD")
    assert view["population_model"] == "independent_v1" and "bundles" not in view
    assert "bundle_count" not in view and len(view["trees"]) == 56
    trees = {tree["id"]: tree for tree in view["trees"]}
    assert trees[first]["eligible"] and trees[first]["resolved_count"] == 10
    assert not trees[second]["eligible"] and trees[second]["mean_r"] == -1
    assert all("bundle_id" not in tree for tree in trees.values())
    assert all(isinstance(tree["control_tree_id"], str) for tree in trees.values())


def test_candidates_only_live_assigned(tmp_path, monkeypatch):
    path = tmp_path / "v4.json"
    ledger = initialize(path, "acc", "XAUUSD", 1000)
    version = logic_version()
    base = {"status": "waiting", "strategy_version": version, "provenance": PROVENANCE,
            "recorded_at": 2000, "costs": {"verified": True},
            "signal": {"available_at": 2000, "entry_policy": {"expires_at": 5000}}}
    ledger["samples"] = [{**base, "strategy": "a", "live_assigned": True},
                         {**base, "strategy": "b", "live_assigned": False}]
    import json
    path.write_text(json.dumps(ledger))
    monkeypatch.setattr("backend.app.forex_htf_v4.ledger_path", lambda *_: path)
    assert [s["strategy"] for s in candidates("acc", "XAUUSD", 3000)] == ["a"]


def test_migration_idempotent_and_reinitialize_safe(tmp_path):
    path = tmp_path / "v4-idem.json"
    now = 500000
    l1 = initialize(path, "acc_idem", "EURUSD", now)
    assert len(l1["population"]) == 56
    # Second call returns existing
    l2 = initialize(path, "acc_idem", "EURUSD", now + 100)
    assert l2["since"] == now
    assert l2["cohort_id"] == l1["cohort_id"]


def test_corrupt_ledger_raises(tmp_path):
    path = tmp_path / "v4-bad.json"
    ledger = initialize(path, "acc_bad", "EURUSD", 500000)
    # Corrupt by removing a tree
    ledger["population"].pop()
    path.write_text(json.dumps(ledger))
    with pytest.raises(ValueError, match="V4 population size mismatch"):
        read_ledger("acc_bad", "EURUSD", path=path)


def test_active_start_returns_earliest_open_or_none(tmp_path, monkeypatch):
    from backend.app.forex_htf_v4 import active_start
    p = tmp_path / "ledger.json"
    p.write_text('{"runtime": "forex_htf_ast_v4", "schema": "forex_htf_ast_v4", "account": "acc", "symbol": "XAUUSD", "samples": []}')
    monkeypatch.setattr("backend.app.forex_htf_v4.ledger_path", lambda *_: p)
    monkeypatch.setattr("backend.app.forex_htf_v4.read_ledger", lambda *_: {
        "samples": [
            {"status": "resolved", "signal": {"available_at": 1000, "entry_policy": {"expires_at": 2000}}},
            {"status": "pending", "signal": {"available_at": 3000, "entry_policy": {"expires_at": 4000}}},
            {"status": "waiting", "signal": {"available_at": 2000, "entry_policy": {"expires_at": 3000}}},
        ]
    })
    assert active_start("acc", "XAUUSD", 3500) == 2000
    # When all resolved: returns None (no M1 needed)
    monkeypatch.setattr("backend.app.forex_htf_v4.read_ledger", lambda *_: {
        "samples": [{"status": "resolved", "signal": {"available_at": 1000, "entry_policy": {"expires_at": 2000}}}]
    })
    assert active_start("acc", "XAUUSD", 3500) is None


def test_shadow_trade_projects_fills_exits_and_verified_simulated_money():
    import copy

    from backend.app.forex_htf_v4 import shadow_trade

    sample = {"strategy": "hast4_test", "setup_id": "setup", "side": "short", "status": "open",
              "signal": {"available_at": 900, "stop": 161, "target": 157,
                         "entry_policy": {"policy": "stop_trigger_break", "stop_price": 160}},
              "costs": {"verified": True, "slippage_bps": 1, "commission_per_unit": .0001},
              "condition": {"account_currency": "USD", "contract_size": 100000,
                            "quote_to_account_loss": .006, "quote_to_account_gain": .0059,
                            "conversion": {"verified": True}},
              "execution": {"entry": 160, "entry_time": 1800, "risk": 1, "net_r": .4,
                            "legs": [{"role": "tp1", "volume": ".02", "weight": .5, "open": False,
                                      "stop_loss_price": "161", "take_profit_price": "159"},
                                     {"role": "runner", "volume": ".02", "weight": .5, "open": True,
                                      "stop_loss_price": "160", "take_profit_price": "157"}],
                            "management_history": [{"at": 2700, "action": "target", "role": "tp1", "price": 159},
                                                   {"at": 3600, "action": "protect_stop", "stop": 160}]}}
    before = copy.deepcopy(sample)
    view = shadow_trade(sample, "USDJPY")
    assert sample == before
    assert view["entry"] == 160 and view["entry_time"] == 1800
    assert view["net_r"] is None
    assert view["valuation"]["risk_usd"] == pytest.approx(24)
    assert view["valuation"]["realized_r"] == .4
    assert view["legs"][1]["stop"] == 160
    assert view["exits"] == [{"time": 2700, "price": 159, "role": "tp1", "reason": "target"}]
    assert json.loads(view["id"]) == ["USDJPY", "hast4_test", "setup", 900]
    sample["condition"]["conversion"]["verified"] = False
    assert shadow_trade(sample, "USDJPY")["valuation"]["risk_usd"] is None


def test_shadow_trade_unfilled_and_legacy_do_not_invent_fills_or_dollars():
    from backend.app.forex_htf_v4 import shadow_trade

    sample = {"strategy": "hast4_test", "side": "long", "status": "waiting",
              "signal": {"available_at": 900, "stop": 1.08, "target": 1.1,
                         "entry_policy": {"policy": "limit_setup_mid", "limit_price": 1.09}}}
    view = shadow_trade(sample, "EURUSD")
    assert view["entry"] is None and view["entry_time"] is None
    assert view["legs"] == [] and view["exits"] == []
    assert view["valuation"]["risk_usd"] is None
    assert view["planned_levels"][0] == {"label": "LIMIT", "price": 1.09}
    sample.update(status="resolved", net_r_ex_swap=1.2, execution={"entry": float("nan"), "resolved_at": 2700})
    view = shadow_trade(sample, "EURUSD")
    assert view["entry"] is None and view["net_r"] == 1.2 and view["resolved_at"] == 2700
    json.dumps(view, allow_nan=False)


def test_shadow_summary_retains_all_active_samples_and_bounds_history(monkeypatch):
    from backend.app.forex_htf_v4 import summary

    def sample(stamp, status):
        return {"strategy": "hast4_test", "side": "long", "status": status,
                "confirm_time": stamp, "signal": {"available_at": stamp + 900}}

    ledger = {"samples": [sample(stamp, "open" if stamp < 3 else "resolved") for stamp in range(110)],
              "population": [], "members": {}, "since": 0}
    monkeypatch.setattr("backend.app.forex_htf_v4.read_ledger", lambda *_: ledger)
    view = summary("account", "EURUSD")["shadow_trades"]
    assert len(view) == 103
    assert sum(trade["status"] == "open" for trade in view) == 3
    assert view[0]["available_at"] == 902
    assert view[3]["available_at"] == 1009
    assert len({trade["id"] for trade in view}) == 103


def test_normalized_shadow_hides_fake_lots_and_dollars():
    from backend.app.forex_htf_v4 import shadow_trade

    sample = {"strategy": "hast4_test", "setup_id": "s", "side": "long", "status": "open",
              "sizing_mode": "normalized_r_v1",
              "signal": {"available_at": 900, "stop": 99, "target": 103, "entry_policy": {"policy": "market"}},
              "costs": {"verified": True}, "condition": {"account_currency": "USD", "contract_size": 100,
                                                         "quote_to_account_loss": 1, "conversion": {"verified": True}},
              "execution": {"entry": 100, "risk": 1, "legs": [{"role": "tp1", "volume": "0.5", "weight": .5},
                                                               {"role": "runner", "volume": "0.5", "weight": .5}]}}
    view = shadow_trade(sample, "XAUUSD")
    assert view["sizing_mode"] == "normalized_r_v1"
    assert view["valuation"]["risk_usd"] is None
    assert [(leg["volume"], leg["weight"]) for leg in view["legs"]] == [(None, .5), (None, .5)]


def test_candidates_ignore_excluded_samples_without_entry_policy(monkeypatch):
    from backend.app import forex_htf_v4 as v4
    excluded = {"live_assigned": True, "status": "excluded", "signal": {}}
    monkeypatch.setattr(v4, "read_ledger", lambda account, symbol: {"samples": [excluded], "since": 0})
    assert v4.candidates("a", "XAUUSD", 0) == []

