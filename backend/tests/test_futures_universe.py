import copy
from types import SimpleNamespace

import pytest

from backend.app import futures_draft_replay as screen
from backend.app import futures_drafts as drafts
from backend.app import futures_history as history
from backend.app import futures_learner as learner
from backend.app.db import FuturesLearningState, PaperTrade, Session
from backend.app.futures_strategies import NATIVE_SEEDS, genome_name
from backend.app.futures_symbols import SYMBOLS, symbol_scope
from backend.app.market import Candle
from backend.app.strategy import Setup
from bot import draft_replay_worker as worker

NOW = 400 * 86400
GENOME = NATIVE_SEEDS[1]
NAME = genome_name(GENOME)
WINDOW = {"start": NOW - 340 * 86400, "end": NOW, "warmup_bars": 6000,
          "scope": drafts.UNIVERSE_SCOPE, "symbols": list(SYMBOLS)}

def pair_report():
    def sample(stamp, value):
        return {"strategy": NAME, "status": "resolved", "net_r": value, "side": "long",
                "regime": "trend_up", "candle_time": stamp - 1800, "entry_time": stamp - 900,
                "entry": 100, "risk": 5, "label_available_at": stamp}
    split = WINDOW["start"] + 22848 * 900
    training = [sample(split - 9000 + index * 900, 0.5) for index in range(3)]
    validation = [sample(split + 9000, -0.5), sample(split + 18000, 1)]
    return {"start_time": WINDOW["start"], "end_time": NOW, "train_before": split,
            "bars": 32640, "warmup_bars": 6000, "fee_bps": 8, "slippage_bps": 2,
            "dataset_sha256": "a" * 64, "signal_inputs": {}, "strategies": [{
                "strategy": NAME, "genome": copy.deepcopy(GENOME), "prior_eligible": False,
                "train": history.historical_stats(training),
                "validation": history.historical_stats(validation),
                "samples": {"train": training, "validation": validation}}]}

def reports():
    return {symbol: pair_report() for symbol in SYMBOLS}

def test_pool_keeps_same_time_trades_on_different_pairs_and_weights_by_trade_count():
    pairs = reports()
    extra = copy.deepcopy(pairs[SYMBOLS[0]]["strategies"][0]["samples"]["train"][0])
    extra.update(net_r=3, candle_time=extra["candle_time"] - 900,
                 label_available_at=extra["label_available_at"] - 900)
    first = pairs[SYMBOLS[0]]["strategies"][0]
    first["samples"]["train"].append(extra)
    first["train"] = history.historical_stats(first["samples"]["train"])
    report = screen.pool_reports(pairs)
    row = report["strategies"][0]
    assert row["train"]["resolved"] == 31
    assert row["train"]["mean_r"] == pytest.approx(18 / 31)
    assert row["validation"]["resolved"] == 20
    assert row["validation"]["max_drawdown_r"] == 5
    assert row["validation"]["max_pair_drawdown_r"] == 0.5
    assert row["validation"]["contributing_symbols"] == 10
    assert drafts.gate(report)[0] == "waiting_slot"
    assert all("samples" not in item for pair in report["per_symbol"].values()
               for item in pair["strategies"])
    assert "not independent" in report["screening_note"]

@pytest.mark.parametrize("change", ["missing", "window", "split", "genome", "fees"])
def test_pool_rejects_partial_or_incompatible_inputs(change):
    pairs = reports()
    last = pairs[SYMBOLS[-1]]
    if change == "missing":
        pairs.pop(SYMBOLS[-1])
    elif change == "genome":
        last["strategies"][0]["genome"]["lookback"] += 5
    else:
        key = {"window": "end_time", "split": "train_before", "fees": "fee_bps"}[change]
        last[key] += 1
    with pytest.raises(screen.ReplayDataError):
        screen.pool_reports(pairs)

def test_universe_checkpoints_pairs_and_restarts_without_replaying_completed_pairs(monkeypatch, tmp_path):
    calls = []
    fail = {"enabled": True}
    def run_pair(symbol, candidates, window, *args, **kwargs):
        assert window == WINDOW and candidates[NAME]["genome"] == GENOME
        calls.append(symbol)
        if symbol == SYMBOLS[2] and fail["enabled"]:
            raise screen.ReplayDataError("missing real fair-price bars")
        return pair_report()
    monkeypatch.setattr(screen, "run_pair", run_pair)
    candidates = {NAME: {"genome": GENOME}}
    with pytest.raises(screen.ReplayDataError):
        screen.run_universe(candidates, WINDOW, cache_dir=tmp_path)
    fail["enabled"] = False
    report = screen.run_universe(candidates, WINDOW, cache_dir=tmp_path)
    assert calls.count(SYMBOLS[0]) == calls.count(SYMBOLS[1]) == 1
    assert calls.count(SYMBOLS[2]) == 2
    assert len(report["per_symbol"]) == 10
    before = list(calls)
    assert screen.run_universe(candidates, WINDOW, cache_dir=tmp_path) == report
    assert calls == before

def test_native_claim_is_340_days_and_retry_keeps_frozen_window():
    genome = {**GENOME, "lookback": 25}
    state = learner.new_state(NOW)
    draft_id = drafts.enqueue(state, genome, NOW, (NAME,))
    draft = state["drafts"][draft_id]
    token = drafts.claim(draft, NOW + 900)
    assert draft["window"] == WINDOW
    drafts.complete(draft, token, NOW + 1000, error="timeout")
    drafts.claim(draft, NOW + 1000 + drafts.BACKOFF_SECONDS)
    assert draft["window"] == WINDOW

def test_native_completion_rejects_single_pair_or_wrong_genome_report():
    genome = {**GENOME, "lookback": 25}
    state = learner.new_state(NOW)
    draft_id = drafts.enqueue(state, genome, NOW, (NAME,))
    draft = state["drafts"][draft_id]
    token = drafts.claim(draft, NOW)
    assert drafts.complete(draft, token, NOW, report=screen.pool_reports(reports()))
    assert draft["status"] == "error"
    assert draft["reason"] == "Invalid frozen native universe report"

def test_native_parent_selection_uses_universe_train_not_validation_or_local_history():
    state = learner.new_state(NOW)
    report = screen.pool_reports(reports())
    row = report["strategies"][0]
    row["train"].update(resolved=40, mean_r=-0.5)
    row["validation"].update(resolved=50, mean_r=4)
    state["breeding_history"] = {"report": report}
    assert learner.breeding_performance(state, NOW)[NAME] == (40, -0.5)
    assert learner.evolve(state, NOW, force=True) == 0
    row["train"]["mean_r"] = 0.3
    assert learner.evolve(state, NOW, force=True) == 1
    assert len(state["samples"]) == 0
    row["genome"] = {**GENOME, "lookback": 25}
    assert learner.breeding_performance(state, NOW)[NAME] == (0, 0)

def test_strict_replay_returns_only_audited_closed_samples(monkeypatch):
    monkeypatch.setattr(history, "WARMUP", 25)
    monkeypatch.setattr(history, "NATIVE_WARMUP", 25)
    monkeypatch.setattr(history, "strategy_setup", lambda *args, **kwargs:
                        Setup("long", 100, 90, 104, "test"))
    candles = [Candle(index * 900, 100, 150, 95, 102, 10) for index in range(225)]
    report = history.replay({NAME: {"genome": GENOME}}, candles, candles, [],
                            25 * 900, 225 * 900, strict_samples=True, include_samples=True)
    row = report["strategies"][0]
    for partition in ("train", "validation"):
        assert len(row["samples"][partition]) == row[partition]["resolved"]
        assert all(sample["entry"] and sample["risk"] > 0 for sample in row["samples"][partition])

def test_parent_worker_refresh_preserves_forward_and_retries_only_after_due(monkeypatch):
    report = screen.pool_reports(reports())
    report["strategies"][0]["train"]["resolved"] = 40
    for genome in NATIVE_SEEDS:
        if genome_name(genome) != NAME:
            row = copy.deepcopy(report["strategies"][0])
            row.update(strategy=genome_name(genome), genome=genome)
            report["strategies"].append(row)
    with Session.begin() as session:
        for symbol in SYMBOLS:
            with symbol_scope(symbol):
                state = learner.new_state(NOW)
                state["cursor"] = 123
                learner.save_state(session, state)
        trades = session.query(PaperTrade).count()
    calls = []
    def supervise(lease, symbol, frozen, emit):
        calls.append(frozen)
        emit(phase="BTC_USDT:replay", completed=1, total=32640)
        return report, None, True
    monkeypatch.setattr(worker, "supervise", supervise)
    lease = SimpleNamespace(check=lambda: None)
    assert worker.refresh_native_parents(lease, NOW)
    assert not worker.refresh_native_parents(lease, NOW + 10)
    assert len(calls) == 1 and calls[0]["purpose"] == "native_parents"
    with Session() as session:
        for index, symbol in enumerate(SYMBOLS):
            state = session.get(FuturesLearningState, index + 1).payload
            assert state["cursor"] == 123 and state["samples"] == []
            assert state["breeding_history"]["report"] == report
            assert len(state["drafts"]) == 3
        assert session.query(PaperTrade).count() == trades
