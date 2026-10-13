from dataclasses import replace
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from backend.app import observation
from backend.app.db import (
    MarketState,
    MtfObservationEvent,
    MtfObservationState,
    MtfResearchSample,
    PaperTrade,
    Session,
    Signal,
)
from backend.app.feed import save_candles
from backend.app.main import app
from backend.app.market import Candle
from backend.app.mexc import SOURCE, MarketDataError
from scripts.mtf_historical_replay import run_replay


@pytest.fixture
def candles():
    start = 1_700_000_000 // 14400 * 14400
    rows = [Candle(start + index * 900, 100 + index / 50, 100.5 + index / 50,
                   99.5 + index / 50, 100 + index / 50, 1) for index in range(1600)]
    rows += [Candle(start + (1600 + index) * 900, 140, 142, 139, 141, 1) for index in range(4)]
    rows += [
        Candle(start + 1604 * 900, 141, 141, 132, 133, 1),
        Candle(start + 1605 * 900, 133, 139, 132.5, 138, 1),
        Candle(start + 1606 * 900, 138, 151, 137, 150, 1),
    ]
    return rows

def seed(candles, now):
    with Session.begin() as session:
        market = session.get(MarketState, 1)
        market.source = SOURCE
        market.last_sync = datetime.fromtimestamp(now, timezone.utc)
        save_candles(session, candles)

def test_replay_is_causal_final_bar_resolves_and_provenance_is_separate(candles):
    prefix = run_replay(candles[:-1])
    full = run_replay(candles)
    assert prefix["samples"][0]["evaluation"]["status"] == "pending"
    sample = full["samples"][0]
    assert sample["evaluation"]["label"] == 1
    assert sample["features"] == prefix["samples"][0]["features"]
    assert "provenance" not in sample["features"]
    assert sample["provenance"] == sample["evaluation"]["provenance"] == "historical"
    assert sample["features"]["available_at"] == candles[-2].time + 900
    assert sample["evaluation"]["label_available_at"] == candles[-1].time + 900
    assert prefix["events"] == full["events"][:len(prefix["events"])]
    assert full["processed_bars"] == 8
    with Session() as session:
        assert session.query(MtfResearchSample).count() == 0
        assert session.query(Signal).count() == session.query(PaperTrade).count() == 0

def test_unclosed_final_bar_does_not_label(candles):
    replay = run_replay(candles, now=candles[-1].time + 899)
    assert replay["samples"][0]["evaluation"]["status"] == "pending"

def test_cancelled_setups_are_events_not_losing_samples(candles):
    cancelled = candles[:-1]
    cancelled[-1] = replace(cancelled[-1], low=131, close=131)
    report = run_replay(cancelled)
    assert "cancelled" in [event["status"] for event in report["events"]]
    assert report["samples"] == []

@pytest.mark.parametrize("kind", ["gap", "duplicate", "invalid", "short"])
def test_replay_rejects_invalid_history(candles, kind):
    if kind == "gap":
        rows = candles[:1601] + candles[1602:]
    elif kind == "duplicate":
        rows = candles[:1601] + [candles[1600]] + candles[1601:]
    elif kind == "invalid":
        rows = [replace(candles[0], close=float("nan"))] + candles[1:]
    else:
        rows = candles[:1599]
    with pytest.raises(MarketDataError):
        run_replay(rows)

def test_worker_catches_up_all_steps_matches_replay_and_deduplicates(candles, monkeypatch):
    initial_now = candles[1599].time + 910
    seed(candles[:1600], initial_now)
    observation.observation_tick(now=initial_now)
    now = candles[-1].time + 910
    seed(candles, now)
    monkeypatch.setattr(observation, "CATCHUP_BATCH", 2)
    for _ in range(4):
        observation.observation_tick(now=now)
    report = run_replay(candles)
    with Session() as session:
        state = session.get(MtfObservationState, 1)
        assert state.last_candle == candles[-1].time
        sample = session.get(MtfResearchSample, candles[-2].time)
        assert sample.features == report["samples"][0]["features"]
        assert sample.evaluation["label"] == 1
        assert sample.evaluation["provenance"] == "catch_up"
        assert session.query(MtfObservationEvent).count() == 8
        assert session.query(Signal).count() == session.query(PaperTrade).count() == 0
    observation.observation_tick(now=now)
    with Session() as session:
        assert session.query(MtfObservationEvent).count() == 8
        assert session.query(MtfResearchSample).count() == 1
    with TestClient(app) as client:
        assert len(client.get("/api/observations/mtf/dataset?provenance=catch_up").json()["samples"]) == 1
        assert client.get("/api/observations/mtf/dataset?provenance=live").json()["samples"] == []
        assert client.get("/api/observations/mtf/dataset?provenance=unknown").status_code == 422

def test_missing_history_keeps_cursor_then_can_resume(candles):
    initial_now = candles[1599].time + 910
    seed(candles[:1600], initial_now)
    observation.observation_tick(now=initial_now)
    now = candles[-1].time + 910
    missing = candles[:1601] + candles[1602:]
    seed(missing, now)

    class Missing:
        def history(self, *_):
            return missing

    observation.observation_tick(adapter=Missing(), now=now)
    with Session.begin() as session:
        state = session.get(MtfObservationState, 1)
        assert state.last_candle == candles[1599].time
        assert state.error and state.retry_at
        assert session.query(MtfObservationEvent).count() == 1
        state.retry_at = None
    seed(candles, now)
    observation.observation_tick(now=now)
    with Session() as session:
        assert session.get(MtfObservationState, 1).last_candle == candles[-1].time

def test_failed_batch_rolls_back_cursor_and_samples(candles, monkeypatch):
    initial_now = candles[1599].time + 910
    seed(candles[:1600], initial_now)
    observation.observation_tick(now=initial_now)
    now = candles[-1].time + 910
    seed(candles, now)

    def fail(*_):
        raise RuntimeError("interrupted batch")

    monkeypatch.setattr(observation, "collect_sample", fail)
    with pytest.raises(RuntimeError, match="interrupted batch"):
        observation.observation_tick(now=now)
    with Session() as session:
        assert session.get(MtfObservationState, 1).last_candle == candles[1599].time
        assert session.query(MtfObservationEvent).count() == 1
        assert session.query(MtfResearchSample).count() == 0
