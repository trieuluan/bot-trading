import random

from backend.app import futures_drafts as drafts
from backend.app import futures_learner as learner
from backend.app.futures_strategies import (
    SEEDS,
    genome_fingerprint,
    genome_name,
    mutate,
)

NOW = 1000000


def unique_child(state, seed):
    rng = random.Random(seed)
    while True:
        draft_id = drafts.enqueue(state, mutate(SEEDS[0], rng), NOW + seed, (genome_name(SEEDS[0]),))
        if draft_id:
            return draft_id


def dummy_report(total=35, val=12, mean_r=0.5, dd=4.0):
    return {
        "start_time": NOW - 90 * 86400,
        "end_time": NOW,
        "train_before": NOW - 27 * 86400,
        "bars": 8640,
        "warmup_bars": 1600,
        "fee_bps": 8,
        "slippage_bps": 2,
        "dataset_sha256": "abc123sha",
        "signal_inputs": {"benchmarks": []},
        "strategies": [{
            "train": {"resolved": total - val, "mean_r": 0.4},
            "validation": {"resolved": val, "mean_r": mean_r, "max_drawdown_r": dd},
        }],
    }


def test_gate_criteria_thresholds():
    # Pass: >=30 total, >=10 val, val mean > 0, val DD < 10
    status, result = drafts.gate(dummy_report(total=30, val=10, mean_r=0.01, dd=9.99))
    assert status == "waiting_slot"
    assert result["checks"]["total_samples_gte_30"] is True
    assert result["checks"]["validation_samples_gte_10"] is True
    assert result["checks"]["validation_mean_gt_0"] is True
    assert result["checks"]["validation_dd_lt_10r"] is True

    # Insufficient evidence: total < 30
    status, result = drafts.gate(dummy_report(total=29, val=10, mean_r=0.5, dd=2.0))
    assert status == "insufficient"
    assert result["checks"]["total_samples_gte_30"] is False

    # Insufficient evidence: val < 10
    status, result = drafts.gate(dummy_report(total=40, val=9, mean_r=0.5, dd=2.0))
    assert status == "insufficient"
    assert result["checks"]["validation_samples_gte_10"] is False

    # Rejected: mean_r <= 0
    status, result = drafts.gate(dummy_report(total=40, val=15, mean_r=0.0, dd=2.0))
    assert status == "rejected"
    assert result["checks"]["validation_mean_gt_0"] is False

    status, result = drafts.gate(dummy_report(total=40, val=15, mean_r=-0.1, dd=2.0))
    assert status == "rejected"

    # Rejected: DD >= 10
    status, result = drafts.gate(dummy_report(total=40, val=15, mean_r=0.5, dd=10.0))
    assert status == "rejected"
    assert result["checks"]["validation_dd_lt_10r"] is False


def test_enqueue_and_queue_limit():
    state = learner.new_state(NOW)
    state["drafts"] = {}
    parent = genome_name(SEEDS[0])
    rng = random.Random(42)

    # Enqueue 3 drafts successfully
    ids = []
    for _ in range(3):
        child = mutate(SEEDS[0], rng)
        while any(genome_fingerprint(child) == genome_fingerprint(c["genome"])
                  for c in state["candidates"].values()):
            child = mutate(SEEDS[0], rng)
        d_id = drafts.enqueue(state, child, NOW, (parent,))
        assert d_id is not None
        ids.append(d_id)

    assert len(state["drafts"]) == 3
    # 4th enqueue fails because MAX_PENDING is 3
    child4 = mutate(SEEDS[0], rng)
    assert drafts.enqueue(state, child4, NOW, (parent,)) is None

    # Completing one as rejected frees a slot
    token = drafts.claim(state["drafts"][ids[0]], NOW)
    drafts.complete(state["drafts"][ids[0]], token, NOW, report=dummy_report(total=40, val=15, mean_r=-0.5))
    assert state["drafts"][ids[0]]["status"] == "rejected"
    assert drafts.enqueue(state, child4, NOW, (parent,)) is not None


def test_insufficient_evidence_retry_after_7_days():
    state = learner.new_state(NOW)
    state["drafts"] = {}
    parent = genome_name(SEEDS[0])
    child = mutate(SEEDS[0], random.Random(1))
    d_id = drafts.enqueue(state, child, NOW, (parent,))
    draft = state["drafts"][d_id]

    token = drafts.claim(draft, NOW)
    drafts.complete(draft, token, NOW, report=dummy_report(total=20, val=5))
    assert draft["status"] == "insufficient"
    assert draft["next_retry_at"] == NOW + 7 * 86400

    # Not ready before 7 days
    assert not drafts.ready(draft, NOW + 86400)
    # Ready at or after 7 days
    assert drafts.ready(draft, NOW + 7 * 86400)


def test_retryable_error_backoff_and_max_attempts():
    state = learner.new_state(NOW)
    state["drafts"] = {}
    parent = genome_name(SEEDS[0])
    child = mutate(SEEDS[0], random.Random(2))
    d_id = drafts.enqueue(state, child, NOW, (parent,))
    draft = state["drafts"][d_id]

    # Attempt 1 fails retryable
    t1 = drafts.claim(draft, NOW)
    assert draft["attempts"] == 1
    drafts.complete(draft, t1, NOW, error="Network timeout", retryable=True)
    assert draft["status"] == "queued"
    assert draft["next_retry_at"] == NOW + drafts.BACKOFF_SECONDS

    # Attempt 2 fails retryable
    t2 = drafts.claim(draft, NOW + 301)
    assert draft["attempts"] == 2
    drafts.complete(draft, t2, NOW + 301, error="Network timeout", retryable=True)
    assert draft["status"] == "queued"

    # Attempt 3 fails retryable -> status becomes error (attempts exhausted)
    t3 = drafts.claim(draft, NOW + 602)
    assert draft["attempts"] == 3
    drafts.complete(draft, t3, NOW + 602, error="Network timeout", retryable=True)
    assert draft["status"] == "error"


def test_non_retryable_error_fails_immediately():
    state = learner.new_state(NOW)
    state["drafts"] = {}
    parent = genome_name(SEEDS[0])
    child = mutate(SEEDS[0], random.Random(3))
    d_id = drafts.enqueue(state, child, NOW, (parent,))
    draft = state["drafts"][d_id]

    token = drafts.claim(draft, NOW)
    drafts.complete(draft, token, NOW, error="Missing OI input", retryable=False)
    assert draft["status"] == "error"


def test_atomic_admission_at_capacity_retires_weak_candidate():
    state = learner.new_state(NOW)
    state["drafts"] = {}
    # Fill candidates to CAP
    parent = genome_name(SEEDS[0])
    weak_genome = {**SEEDS[0], "lookback": 25}
    weak = genome_name(weak_genome)
    learner.add_candidate(state, weak_genome, 0)
    state["candidates"][weak].update(generation=1, promoted=False)

    # Weak has 50 negative forward samples
    state["samples"] = [
        {"strategy": weak, "candle_time": NOW - 60000 + i * 900, "entry_time": NOW - 60000 + i * 900 + 900,
         "entry": 100, "risk": 5, "status": "resolved", "net_r": -0.5, "side": "long", "regime": "trend",
         "label_available_at": NOW - 60000 + i * 900 + 1800}
        for i in range(50)
    ]
    # Set CAP to current candidates count
    cap = len(state["candidates"])
    state_cap = cap

    child = mutate(SEEDS[0], random.Random(4))
    d_id = drafts.enqueue(state, child, NOW, (parent,))
    draft = state["drafts"][d_id]
    token = drafts.claim(draft, NOW)
    drafts.complete(draft, token, NOW, report=dummy_report(total=35, val=12, mean_r=0.4, dd=3.0))
    assert draft["status"] == "waiting_slot"

    # Admission retires weak
    import backend.app.futures_learner as fl
    orig_cap = fl.CAP
    fl.CAP = state_cap
    try:
        admitted = learner.admit_drafts(state, NOW, protected=())
        assert admitted == 1
        assert draft["status"] == "admitted"
        assert draft["admitted_strategy"] in state["candidates"]
        assert weak not in state["candidates"]
        assert weak in state["rule_archive"]
        assert state["rule_archive"][weak]["retirement"]["reason"] == "negative_forward_r"
    finally:
        fl.CAP = orig_cap


def test_admission_fifo_ordering():
    state = learner.new_state(NOW)
    state["drafts"] = {}
    d1, d2 = unique_child(state, 0), unique_child(state, 10)

    t1 = drafts.claim(state["drafts"][d1], NOW)
    drafts.complete(state["drafts"][d1], t1, NOW, report=dummy_report(total=35, val=12, mean_r=0.4))
    t2 = drafts.claim(state["drafts"][d2], NOW + 10)
    drafts.complete(state["drafts"][d2], t2, NOW + 10, report=dummy_report(total=35, val=12, mean_r=0.8))

    # Older draft is admitted first
    admitted = learner.admit_drafts(state, NOW + 20, protected=())
    assert admitted >= 1
    assert state["drafts"][d1]["status"] == "admitted"


def test_admission_blocked_when_no_retirement_candidate():
    state = learner.new_state(NOW)
    state["drafts"] = {}
    parent = genome_name(SEEDS[0])
    child = mutate(SEEDS[0], random.Random(6))
    d_id = drafts.enqueue(state, child, NOW, (parent,))
    draft = state["drafts"][d_id]
    token = drafts.claim(draft, NOW)
    drafts.complete(draft, token, NOW, report=dummy_report(total=35, val=12, mean_r=0.4))
    assert draft["status"] == "waiting_slot"

    import backend.app.futures_learner as fl
    orig_cap = fl.CAP
    fl.CAP = len(state["candidates"])  # Population full, but NO candidate has >=50 samples or bad R
    try:
        admitted = learner.admit_drafts(state, NOW, protected=())
        assert admitted == 0
        assert draft["status"] == "waiting_slot"
        assert draft["reason"] == "no_retirement_candidate"
    finally:
        fl.CAP = orig_cap


def test_summary_includes_drafts_and_worker_status():
    state = learner.new_state(NOW)
    state["drafts"] = {}
    parent = genome_name(SEEDS[0])
    child = mutate(SEEDS[0], random.Random(7))
    d_id = drafts.enqueue(state, child, NOW, (parent,))

    res = learner.summary(state, NOW)
    assert "drafts" in res
    assert len(res["drafts"]) == 1
    assert res["drafts"][0]["id"] == d_id
    assert res["drafts"][0]["strategy"] == genome_name(child)


def test_restart_reuses_window_without_new_attempt():
    state = learner.new_state(NOW)
    state["drafts"] = {}
    draft = state["drafts"][unique_child(state, 3)]
    first = drafts.claim(draft, NOW)
    window = dict(draft["window"])
    second = drafts.claim(draft, NOW + 60)  # worker restarted mid-run
    assert second != first and draft["attempts"] == 1 and draft["window"] == window
    assert not drafts.complete(draft, first, NOW + 70, report=dummy_report())  # stale result ignored
    assert drafts.complete(draft, second, NOW + 80, report=dummy_report())


def test_duplicate_and_known_genomes_are_not_enqueued():
    state = learner.new_state(NOW)
    state["drafts"] = {}
    assert drafts.enqueue(state, SEEDS[0], NOW, (genome_name(SEEDS[0]),)) is None
    draft_id = unique_child(state, 4)
    assert drafts.enqueue(state, state["drafts"][draft_id]["genome"], NOW, ()) is None


def test_cached_candles_fill_only_missing_bars(tmp_path):
    from backend.app import futures_draft_replay as screen
    from backend.app.market import Candle

    class Adapter:
        symbol = "BTC_USDT"
        calls = []
        def history(self, start, end, now, fair=False):
            self.calls.append((start, end))
            return [Candle(t, 1, 2, 1, 1.5, 1) for t in range(start, end, 900)]

    adapter = Adapter()
    rows = screen.cached_candles(adapter, 0, 900 * 10, 10**9, tmp_path, lambda *_: None, fair=True)
    assert len(rows) == 10 and adapter.calls == [(0, 9000)]
    screen.cached_candles(adapter, 0, 900 * 12, 10**9, tmp_path, lambda *_: None, fair=True)
    assert adapter.calls[-1] == (9000, 10800)  # only the two new bars are fetched


def test_missing_oi_history_is_data_error_not_no_signal():
    import pytest

    from backend.app import futures_draft_replay as screen
    with pytest.raises(screen.MissingInputError):
        screen.validate_oi([], 10_000, 20_000)
    screen.validate_oi([(t, 5.0) for t in range(6_400, 20_001, 900)], 10_000, 20_000)
    assert issubclass(screen.MissingInputError, screen.ReplayDataError)
