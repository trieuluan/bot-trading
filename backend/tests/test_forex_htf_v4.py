import json
import tempfile
import time
from pathlib import Path

import pytest

from backend.app.forex_entry_policy import POLICIES, make_v4_g0_bundle, tree_id
from backend.app.forex_htf_v4 import (
    PROVENANCE,
    bundle_stats,
    candidates,
    evolve_v4_population,
    initialize,
    logic_version,
    read_ledger,
)


def test_v4_g0_bundle_shape():
    pop, bundles = make_v4_g0_bundle("XAUUSD")
    assert len(pop) == 56
    assert len(bundles) == 14
    for b_id, b_meta in bundles.items():
        assert len(b_meta["tree_ids"]) == 4
        for pol in POLICIES:
            assert pol in b_meta["tree_ids"]
    assert len({tree_id(t) for t in pop}) == 56


def test_v4_initialize_and_read():
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "v4-test.json"
        now = int(time.time())
        ledger = initialize(path, "test_account", "EURUSD", now)
        assert ledger["schema"] == "forex_htf_ast_v4"
        assert len(ledger["population"]) == 56
        assert len(ledger["bundles"]) == 14
        assert len(ledger["members"]) == 56

        # Read back
        loaded = read_ledger("test_account", "EURUSD", path=path)
        assert loaded["since"] == now
        assert len(loaded["population"]) == 56


def test_bundle_stats_ready_threshold():
    now = 1000000
    version = "ver1"
    b_info = {"id": "bundle_00", "born_at": 500000, "trees": {"market": "t_mkt", "limit_setup_mid": "t_lim", "stop_trigger_break": "t_stp", "limit_or_stop": "t_hyb"}}
    
    samples_by_strat = {}
    # Create 9 matched resolved samples
    for i in range(9):
        sid = f"setup_{i}"
        for pol, tid in b_info["trees"].items():
            samples_by_strat.setdefault(tid, []).append({
                "setup_id": sid, "side": "long", "confirm_time": 900000 + i * 900,
                "status": "resolved", "provenance": "live_forward_htf_ast4_v2",
                "costs": {"verified": True}, "strategy_version": version,
                "net_r_ex_swap": 1.0, "label_available_at": 999900
            })
    stats = bundle_stats("bundle_00", b_info, samples_by_strat, now, version)
    assert stats["matched_count"] == 9
    assert stats["ready"] is False
    assert stats["eligible"] is False

    # Add 10th
    for pol, tid in b_info["trees"].items():
        samples_by_strat[tid].append({
            "setup_id": "setup_9", "side": "long", "confirm_time": 900000 + 9 * 900,
            "status": "resolved", "provenance": "live_forward_htf_ast4_v2",
            "costs": {"verified": True}, "strategy_version": version,
            "net_r_ex_swap": 1.0, "label_available_at": 999900
        })
    stats10 = bundle_stats("bundle_00", b_info, samples_by_strat, now, version)
    assert stats10["matched_count"] == 10
    assert stats10["ready"] is True
    assert stats10["eligible"] is True
    assert stats10["mean_r"] == 1.0


def test_evolve_v4_keeps_population_size_and_bundle_count():
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "v4-evo.json"
        now = 1000000
        ledger = initialize(path, "acc_evo", "XAUUSD", now)
        version = logic_version()

        # Seed 10 winning samples for bundle_00 and 10 losing samples for bundle_01
        b0 = ledger["bundles"]["bundle_00"]
        b1 = ledger["bundles"]["bundle_01"]
        for i in range(10):
            sid = f"win_{i}"
            for pol, tid in b0["trees"].items():
                ledger["samples"].append({
                    "strategy": tid, "setup_id": sid, "side": "long",
                    "confirm_time": now + i * 60, "status": "resolved",
                    "provenance": "live_forward_htf_ast4_v2", "costs": {"verified": True},
                    "strategy_version": version, "net_r_ex_swap": 1.5,
                    "label_available_at": now + 2000
                })
        for i in range(10):
            sid = f"lose_{i}"
            for pol, tid in b1["trees"].items():
                ledger["samples"].append({
                    "strategy": tid, "setup_id": sid, "side": "short",
                    "confirm_time": now + i * 60, "status": "resolved",
                    "provenance": "live_forward_htf_ast4_v2", "costs": {"verified": True},
                    "strategy_version": version, "net_r_ex_swap": -1.0,
                    "label_available_at": now + 2000
                })

        # Try evolve before 3600s
        assert evolve_v4_population(ledger, now + 1000, version=version) is False

        # Evolve at now + 4000
        evo_now = now + 4000
        did_evolve = evolve_v4_population(ledger, evo_now, random_seed=42, version=version)
        assert did_evolve is True
        assert ledger["generation"] == 1
        assert len(ledger["population"]) == 56
        assert len(ledger["bundles"]) == 14
        assert len(ledger["members"]) == 60
        assert "bundle_01" not in ledger["bundles"] and "bundle_01" in ledger["retired_bundles"]
        child = ledger["evolution_history"][0]["added_bundle"]
        assert ledger["bundles"][child]["generation"] == 1
        assert ledger["bundles"][child]["parents"] == ["bundle_00"]
        pols = [t["root"]["children"][3]["policy"] for t in ledger["population"]]
        assert all(pols.count(p) == 14 for p in POLICIES)


def test_assignment_round_robin_persisted_and_validated(tmp_path):
    path = tmp_path / "v4.json"
    ledger = initialize(path, "acc", "XAUUSD", 1000)
    ledger["assignments"] = {f"s{i}": {"policy": POLICIES[i % 4]} for i in range(5)}
    ledger["rr_counter"] = 5
    import json
    path.write_text(json.dumps(ledger))
    assert read_ledger("acc", "XAUUSD", path=path)["assignments"]["s4"]["policy"] == "market"
    ledger["assignments"]["s4"]["policy"] = "limit_or_stop"  # tampered: no silent fallback
    path.write_text(json.dumps(ledger))
    with pytest.raises(ValueError):
        read_ledger("acc", "XAUUSD", path=path)


def test_candidates_only_live_assigned(tmp_path, monkeypatch):
    path = tmp_path / "v4.json"
    ledger = initialize(path, "acc", "XAUUSD", 1000)
    version = logic_version()
    base = {"status": "waiting", "strategy_version": version, "provenance": "live_forward_htf_ast4_v2",
            "recorded_at": 2000, "costs": {"verified": True},
            "signal": {"available_at": 2000, "entry_policy": {"expires_at": 5000}}}
    ledger["samples"] = [{**base, "strategy": "a", "live_assigned": True},
                         {**base, "strategy": "b", "live_assigned": False}]
    import json
    path.write_text(json.dumps(ledger))
    monkeypatch.setattr("backend.app.forex_htf_v4.ledger_path", lambda *_: path)
    assert [s["strategy"] for s in candidates("acc", "XAUUSD", 3000)] == ["a"]


def test_bundle_stats_excludes_unverified_open_ambiguous_and_wrong_version():
    from backend.app.forex_entry_policy import POLICIES
    now = 1000000
    version = logic_version()
    b_info = {"trees": {pol: f"t_{pol}" for pol in POLICIES}, "born_at": 500000}
    
    samples_by_strat = {tid: [] for tid in b_info["trees"].values()}
    # Add 10 setups but with defects:
    # 1: status open, 2: unverified costs, 3: wrong version, 4: net_r nan, 5: label_available_at >= now
    samples_by_strat["t_market"].extend([
        {"setup_id": "s1", "side": "long", "confirm_time": 600000, "status": "open",
         "provenance": PROVENANCE, "costs": {"verified": True}, "strategy_version": version,
         "net_r_ex_swap": 1.0, "label_available_at": 700000},
        {"setup_id": "s2", "side": "long", "confirm_time": 600000, "status": "resolved",
         "provenance": PROVENANCE, "costs": {"verified": False}, "strategy_version": version,
         "net_r_ex_swap": 1.0, "label_available_at": 700000},
        {"setup_id": "s3", "side": "long", "confirm_time": 600000, "status": "resolved",
         "provenance": PROVENANCE, "costs": {"verified": True}, "strategy_version": "old_ver",
         "net_r_ex_swap": 1.0, "label_available_at": 700000},
        {"setup_id": "s4", "side": "long", "confirm_time": 600000, "status": "resolved",
         "provenance": PROVENANCE, "costs": {"verified": True}, "strategy_version": version,
         "net_r_ex_swap": float("nan"), "label_available_at": 700000},
        {"setup_id": "s5", "side": "long", "confirm_time": 600000, "status": "resolved",
         "provenance": PROVENANCE, "costs": {"verified": True}, "strategy_version": version,
         "net_r_ex_swap": 1.0, "label_available_at": now + 100},
    ])
    stats = bundle_stats("b_test", b_info, samples_by_strat, now, version)
    assert stats["matched_count"] == 0
    assert stats["ready"] is False


def test_evolve_blocks_busy_bundle():
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "v4-busy.json"
        now = 1000000
        ledger = initialize(path, "acc_busy", "XAUUSD", now)
        version = logic_version()

        b0 = ledger["bundles"]["bundle_00"]
        b1 = ledger["bundles"]["bundle_01"]
        for i in range(10):
            sid = f"win_{i}"
            for pol, tid in b0["trees"].items():
                ledger["samples"].append({
                    "strategy": tid, "setup_id": sid, "side": "long",
                    "confirm_time": now + i * 60, "status": "resolved",
                    "provenance": PROVENANCE, "costs": {"verified": True},
                    "strategy_version": version, "net_r_ex_swap": 2.0,
                    "label_available_at": now + 2000
                })
        for i in range(10):
            sid = f"lose_{i}"
            for pol, tid in b1["trees"].items():
                ledger["samples"].append({
                    "strategy": tid, "setup_id": sid, "side": "short",
                    "confirm_time": now + i * 60, "status": "resolved",
                    "provenance": PROVENANCE, "costs": {"verified": True},
                    "strategy_version": version, "net_r_ex_swap": -1.0,
                    "label_available_at": now + 2000
                })

        # Mark b1 as busy via open sample
        b1_market_tid = b1["trees"]["market"]
        ledger["samples"].append({
            "strategy": b1_market_tid, "setup_id": "active_now", "side": "short",
            "confirm_time": now + 3000, "status": "open", "provenance": PROVENANCE,
            "costs": {"verified": True}, "strategy_version": version,
            "label_available_at": now + 5000
        })

        assert evolve_v4_population(ledger, now + 4000, random_seed=42, version=version) is False
        # If passed as busy_strategies argument, also blocked
        ledger["samples"].pop()
        assert evolve_v4_population(ledger, now + 4000, random_seed=42, version=version,
                                   busy_strategies={b1_market_tid}) is False


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


def test_partial_tp_then_sl_nets_positive():
    # Setup where leg 0 (weight 0.5) hits TP at +2R, leg 1 hits SL at -1R
    # net_r = 0.5 * 2.0 + 0.5 * (-1.0) = +0.5R
    # If commission is high, net_r becomes negative and bundle_stats correctly excludes/penalizes
    now = 1000000
    version = logic_version()
    b_info = {"trees": {pol: f"t_{pol}" for pol in POLICIES}, "born_at": 500000}
    samples_by_strat = {tid: [] for tid in b_info["trees"].values()}
    
    for i in range(10):
        sid = f"s_{i}"
        for pol, tid in b_info["trees"].items():
            samples_by_strat[tid].append({
                "setup_id": sid, "side": "long", "confirm_time": 600000 + i * 900,
                "status": "resolved", "provenance": PROVENANCE, "costs": {"verified": True},
                "strategy_version": version, "net_r_ex_swap": 0.5, # Partial TP saved the net
                "label_available_at": 700000 + i * 900
            })
    stats = bundle_stats("b_partial", b_info, samples_by_strat, now, version)
    assert stats["ready"] is True
    assert stats["eligible"] is True
    assert stats["mean_r"] == 0.5

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


def test_summary_reports_bundle_progress(tmp_path, monkeypatch):
    from backend.app.forex_htf_v4 import summary
    ledger = {"samples": [{"strategy": "t1", "status": "open", "setup_id": "s1", "side": "long",
                           "confirm_time": 1, "signal": {"available_at": 901}}],
              "population": [], "members": {}, "since": 0,
              "bundles": {"bundle_00": {"trees": {"market": "t1"}, "born_at": 0}}}
    monkeypatch.setattr("backend.app.forex_htf_v4.read_ledger", lambda *_: ledger)
    assert summary("a", "EURUSD")["bundles"]["bundle_00"] == {
        "matched_count": 0, "ready": False, "mean_r": None, "running_setups": 1}
