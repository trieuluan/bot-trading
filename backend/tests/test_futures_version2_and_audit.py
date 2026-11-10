"""Tests for versioned H1/M15 coordination, SL/TP geometry, and frozen population audits."""

import copy

import pytest

from backend.app.futures_exits import fill_geometry
from backend.app.futures_learner import admit_drafts
from backend.app.futures_population_audit import (
    AUDIT_DAYS,
    AUDIT_VERSION,
    attach,
    eligible,
    engine_digest,
    freeze,
)
from backend.app.futures_retirement import retirement_candidates
from backend.app.futures_signal_window import consume_event, pending_valid, signal_event
from backend.app.futures_strategies import (
    RULE_SEEDS,
    genome_fingerprint,
    genome_name,
    validate_genome,
)
from backend.app.market import Candle


def make_bars(start, count, step=900, base=100.0, trend=0.1):
    return [
        Candle(
            time=start + i * step,
            open=base + i * trend,
            high=base + i * trend + 1.0,
            low=base + i * trend - 1.0,
            close=base + i * trend + 0.5,
            volume=100.0,
        )
        for i in range(count)
    ]


def test_validate_genome_signal_version_rules():
    base = copy.deepcopy(RULE_SEEDS[0])
    v2 = {**base, "signal_version": 2}
    validate_genome(v2)

    with pytest.raises(ValueError, match="Invalid versioned"):
        validate_genome({**base, "signal_version": 1})

    with pytest.raises(ValueError, match="Invalid versioned"):
        validate_genome({**base, "signal_version": 2, "signal_frame": "H4"})

    non_htf = copy.deepcopy(base)
    non_htf["rule"] = {"feature": "volume", "frame": "M15"}
    with pytest.raises(ValueError, match="Invalid versioned"):
        validate_genome({**non_htf, "signal_version": 2})


def test_fill_geometry_respects_nearby_barrier_and_min_rr():
    candles = make_bars(0, 100, step=900, base=100.0)
    assert fill_geometry("long", 100.0, 98.0, 105.0, candles) == 105.0
    assert fill_geometry("long", 100.0, 101.0, 105.0, candles) is None
    assert fill_geometry("long", 100.0, 98.0, 102.0, candles) is None
    assert fill_geometry("long", float("nan"), 98.0, 105.0, candles) is None
    assert fill_geometry("long", 100.0, -1.0, 105.0, candles) is None
    assert fill_geometry("invalid_side", 100.0, 98.0, 105.0, candles) is None


def test_fill_geometry_slippage_drops_rr_below_1_5():
    candles = make_bars(0, 100, step=900, base=100.0)
    assert fill_geometry("long", 100.1, 98.0, 103.0, candles) is None


def test_consume_event_bounds_and_deduplicates():
    events = {}
    event1 = {"key": "3600:1", "close": 3600}
    event2 = {"key": "3600:1", "close": 3600}
    event_next = {"key": "7200:1", "close": 7200}
    event_short = {"key": "3600:-1", "close": 3600}

    assert consume_event(events, "strat1", event1) is True
    assert consume_event(events, "strat1", event2) is False
    assert consume_event(events, "strat1", event_short) is True
    assert consume_event(events, "strat1", event_next) is True
    assert consume_event(events, "strat1", event1) is False


def test_population_audit_freeze_and_eligibility():
    now = 999999900
    cutoff = now // 900 * 900
    candles = [
        Candle(t, 100, 101, 99, 100, 10) for t in range(cutoff - 1000 * 900, cutoff + 900, 900)
    ]

    genome = copy.deepcopy(RULE_SEEDS[0])
    name = genome_name(genome)
    state = {
        "enabled": True,
        "cursor": cutoff - 900,
        "candidates": {
            name: {
                "genome": genome,
                "born_at": cutoff - 6 * 86400,
                "windows": [],
                "promoted": False,
            }
        },
        "samples": [],
        "sample_archive": [],
    }

    frozen = freeze(state, now, candles)
    assert frozen["version"] == AUDIT_VERSION
    assert name in frozen["candidates"]
    assert frozen["candidates"][name]["forward_coverage"]["complete"] is True

    digest = engine_digest()
    audit = {
        "version": AUDIT_VERSION,
        "engine": digest,
        "status": "sparse",
        "reason": "sparse_signal_not_loss_evidence",
        "coverage_complete": True,
        "fingerprint": genome_fingerprint(genome),
        "born_at": state["candidates"][name]["born_at"],
        "cutoff": cutoff,
        "start": cutoff - AUDIT_DAYS * 86400,
        "end": cutoff,
        "resolved": 2,
        "dataset_sha256": "a" * 64,
        "forward_coverage": frozen["candidates"][name]["forward_coverage"],
    }

    assert eligible(state["candidates"][name], audit, state["cursor"]) is True

    not_sparse = {**audit, "resolved": 5, "status": "keep"}
    assert eligible(state["candidates"][name], not_sparse, state["cursor"]) is False

    short_cov = {
        **audit,
        "forward_coverage": {
            "complete": True,
            "bars": 300,
            "start": cutoff - 3 * 86400,
            "end": cutoff,
        },
    }
    candidate_young = {**state["candidates"][name], "born_at": cutoff - 3 * 86400}
    assert eligible(candidate_young, short_cov, state["cursor"]) is False

    stale_engine = {**audit, "engine": "stale_hash"}
    assert eligible(state["candidates"][name], stale_engine, state["cursor"]) is False


def test_native_protected_from_sparse_retirement():
    cursor = 999999900
    from backend.app.futures_strategies import NATIVE_SEEDS

    native_genome = copy.deepcopy(NATIVE_SEEDS[0])
    candidate = {"genome": native_genome, "born_at": cursor - 10 * 86400}
    audit = {
        "version": AUDIT_VERSION,
        "engine": engine_digest(),
        "status": "sparse",
        "coverage_complete": True,
        "fingerprint": genome_fingerprint(native_genome),
        "born_at": candidate["born_at"],
        "cutoff": cursor + 900,
        "start": cursor + 900 - AUDIT_DAYS * 86400,
        "end": cursor + 900,
        "resolved": 1,
        "dataset_sha256": "b" * 64,
        "forward_coverage": {
            "complete": True,
            "start": candidate["born_at"],
            "end": cursor + 900,
            "bars": 960,
        },
    }
    assert eligible(candidate, audit, cursor) is False


def test_sparse_retirement_only_with_waiting_draft():
    now = 999999900
    cutoff = now + 900
    genome = copy.deepcopy(RULE_SEEDS[0])
    name = genome_name(genome)
    candidate = {"genome": genome, "born_at": cutoff - 960 * 900}
    state = {
        "cursor": now,
        "candidates": {name: candidate},
        "samples": [],
        "sample_archive": [],
        "population_audit": {
            name: {
                "version": AUDIT_VERSION,
                "engine": engine_digest(),
                "status": "sparse",
                "coverage_complete": True,
                "fingerprint": genome_fingerprint(genome),
                "born_at": candidate["born_at"],
                "cutoff": cutoff,
                "start": cutoff - AUDIT_DAYS * 86400,
                "end": cutoff,
                "resolved": 1,
                "dataset_sha256": "c" * 64,
                "forward_coverage": {
                    "complete": True,
                    "start": candidate["born_at"],
                    "end": cutoff,
                    "bars": 960,
                },
            }
        },
    }
    evidence = {name: []}
    res = retirement_candidates(state, evidence, protected=set(), now=now)
    assert len(res) == 1
    assert res[0][0] == name
    assert res[0][1]["reason"] == "sparse_forward_replay"

    res_protected = retirement_candidates(state, evidence, protected={name}, now=now)
    assert len(res_protected) == 0


def test_legacy_btc_barrier_rejection_logic():
    candles = [Candle(t, 80500, 80800, 80400, 80600, 100) for t in range(0, 3600 * 50, 900)]
    candles[20] = Candle(candles[20].time, 80500, 81140, 80400, 81000, 100)
    candles[-1] = Candle(candles[-1].time, 80800, 80950, 80700, 80907.4, 100)

    res = fill_geometry("long", 80907.4, 79773.3, 83000.0, candles)
    assert res is None


def test_admit_drafts_caps_at_3_and_archives_atomically():
    import backend.app.futures_learner as fl

    now = 999999900
    cutoff = now
    state = {
        "enabled": True,
        "cursor": now,
        "candidates": {},
        "samples": [],
        "sample_archive": [],
        "rule_archive": {},
        "drafts": {},
        "population_audit": {},
    }
    # Create 5 weak candidates eligible for sparse retirement
    for i in range(5):
        genome = copy.deepcopy(RULE_SEEDS[i % len(RULE_SEEDS)])
        genome["lookback"] = 20 + i
        name = genome_name(genome)
        candidate = {
            "genome": genome,
            "born_at": cutoff - 960 * 900,
            "generation": 0,
            "parents": [],
            "windows": [],
            "promoted": False,
        }
        state["candidates"][name] = candidate
        state["population_audit"][name] = {
            "version": AUDIT_VERSION,
            "engine": engine_digest(),
            "status": "sparse",
            "coverage_complete": True,
            "fingerprint": genome_fingerprint(genome),
            "born_at": candidate["born_at"],
            "cutoff": cutoff,
            "start": cutoff - AUDIT_DAYS * 86400,
            "end": cutoff,
            "resolved": 1,
            "dataset_sha256": "c" * 64,
            "forward_coverage": {
                "complete": True,
                "start": candidate["born_at"],
                "end": cutoff,
                "bars": 960,
            },
        }

    # Create 4 waiting_slot drafts
    for i in range(4):
        child_genome = copy.deepcopy(RULE_SEEDS[i % len(RULE_SEEDS)])
        child_genome["lookback"] = 30 + i
        child_name = genome_name(child_genome)
        d_id = f"draft_{i}"
        state["drafts"][d_id] = {
            "id": d_id,
            "strategy": child_name,
            "genome": child_genome,
            "fingerprint": genome_fingerprint(child_genome),
            "parents": ["parent"],
            "generation": 1,
            "created_at": now + i,
            "status": "waiting_slot",
            "replay": {"checks": {"passed": True}},
        }

    orig_cap = fl.CAP
    fl.CAP = 5  # Exactly at cap
    try:
        admitted = admit_drafts(state, now, protected=set())
        # Max 3 admissions per call
        assert admitted == 3
        assert len(state["rule_archive"]) == 3
        assert len(state["candidates"]) == 5
    finally:
        fl.CAP = orig_cap


def test_pending_valid_h1_window_and_expiry(monkeypatch):
    rule = RULE_SEEDS[0]["rule"]
    monkeypatch.setattr(
        "backend.app.futures_signal_window.matches",
        lambda node, rows, side, *args, **kwargs: side == 1,
    )
    event = {"key": "3600:1", "close": 3600, "invalidation": 98.0}
    # Valid window 0..3600 sec from close (3600 to 7200)
    bars = [Candle(t, 100, 101, 99, 100, 10) for t in range(0, 3600 + 4 * 900, 900)]
    assert pending_valid(event, rule, bars[:4], 1, 20, {"htf": True}) is True
    assert pending_valid(event, rule, bars[:8], 1, 20, {"htf": True}) is True
    # 5th subsequent M15 bar (time = 7200, close = 8100 > 7200) -> expired
    bars_expired = [Candle(t, 100, 101, 99, 100, 10) for t in range(0, 3600 + 5 * 900, 900)]
    assert pending_valid(event, rule, bars_expired, 1, 20, {"htf": True}) is False

    # Price breaks invalidation level (98.0)
    broken_bars = copy.deepcopy(bars[:6])
    broken_bars[4] = Candle(broken_bars[4].time, 100, 101, 95, 97.5, 10)
    assert pending_valid(event, rule, broken_bars, 1, 20, {"htf": True}) is False


@pytest.mark.parametrize("lag", range(5))
def test_closed_h1_trigger_window_no_lookahead(monkeypatch, lag):
    rule = RULE_SEEDS[0]["rule"]
    close = 3600 + lag * 900
    seen = []

    def match(node, rows, direction, lookback, **kwargs):
        seen.append(rows[-1].time + 900)
        if node == rule["children"][1]:
            return direction == 1 and (rows[-1].time + 900) // 3600 * 3600 == 3600
        return direction == 1

    monkeypatch.setattr("backend.app.futures_signal_window.matches", match)
    monkeypatch.setattr("backend.app.futures_signal_window.breakout_level", lambda *args: 99)
    event = signal_event(rule, make_bars(0, close // 900), 1, 20, {})
    assert event["close"] == 3600
    assert max(seen) <= close
    assert signal_event(rule, make_bars(0, 9), 1, 20, {}) is None
    assert signal_event(rule, make_bars(0, 3), 1, 20, {}) is None


@pytest.mark.parametrize("fault", ["gap", "h4_lost", "opposite", "invalidated"])
def test_event_never_recovers_after_cancellation(monkeypatch, fault):
    rule = RULE_SEEDS[0]["rule"]
    rows = make_bars(0, 8)

    def match(node, history, direction, lookback, **kwargs):
        close = history[-1].time + 900
        if node == rule["children"][0]:
            return direction == 1 and not (fault == "h4_lost" and close == 4500)
        if node == rule["children"][1]:
            return (
                direction == (-1 if fault == "opposite" and close == 7200 else 1) and close <= 3600
            )
        return direction == 1

    if fault == "opposite":
        original = match

        def match(node, history, direction, lookback, **kwargs):
            if node == rule["children"][1] and direction == -1 and history[-1].time + 900 == 7200:
                return True
            return original(node, history, direction, lookback, **kwargs)

    monkeypatch.setattr("backend.app.futures_signal_window.matches", match)
    monkeypatch.setattr("backend.app.futures_signal_window.breakout_level", lambda *args: 99)
    if fault == "gap":
        rows.pop(4)
    if fault == "invalidated":
        rows[4] = Candle(3600, 100, 101, 97, 98, 10)
    assert signal_event(rule, rows, 1, 20, {}) is None


def test_pair_audit_and_missing_inputs_never_write_forward(monkeypatch):
    from backend.app import futures_population_audit as audit_module
    from backend.app.futures_draft_replay import MissingInputError

    genome = copy.deepcopy(RULE_SEEDS[0])
    name = genome_name(genome)
    state = {
        "cursor": 900000,
        "samples": [],
        "candidates": {name: {"genome": genome, "born_at": 0}},
    }
    frozen = freeze(state, 900900, make_bars(0, 1001))
    before = copy.deepcopy(state)

    def pair(symbol, arms, window, *args, **kwargs):
        assert len(arms) == 2
        assert window["end"] - window["start"] == 90 * 86400
        assert kwargs["diagnostics"]
        return {
            "dataset_sha256": "a" * 64,
            "strategies": [
                {
                    "strategy": key,
                    "genome": row["genome"],
                    "train": {"resolved": 2},
                    "validation": {"resolved": 1},
                    "funnel": {"filled": 3},
                }
                for key, row in arms.items()
            ],
        }

    monkeypatch.setattr("backend.app.futures_draft_replay.run_pair", pair)
    result = audit_module.run_audit("BTC_USDT", frozen)
    assert result[name]["resolved"] == 3 and len(result[name]["comparison"]) == 2
    assert state == before

    def missing(*args, **kwargs):
        raise MissingInputError("Missing real OI")

    monkeypatch.setattr("backend.app.futures_draft_replay.run_pair", missing)
    result = audit_module.run_audit("BTC_USDT", frozen)
    assert result[name]["status"] == "insufficient_data"
    assert not eligible(state["candidates"][name], result[name], state["cursor"])
    attach(state, result)
    assert state["samples"] == []

def test_summary_reports_population_audit_status_and_job():
    from backend.app.futures_learner import summary
    candidate = {"genome": copy.deepcopy(RULE_SEEDS[0]), "born_at": 1000,
                 "parents": [], "generation": 0, "windows": [], "promoted": False}
    state = {
        "enabled": True, "auto_mode": "learner", "cursor": 2000, "updated_at": 2000,
        "error": None, "samples": [], "sample_archive": [], "last_decision": None,
        "candidates": {"rule_tree:target": candidate},
        "drafts": {}, "rule_archive": {},
        "population_audit_job": {"status": "running", "strategy": "rule_tree:target"},
        "population_audit": {},
    }
    res = summary(state, 2000, {})
    assert res["population_audit_job"]["status"] == "running"
    assert res["strategies"][0]["population_audit_status"] == "running"
