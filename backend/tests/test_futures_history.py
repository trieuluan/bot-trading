import copy
import math

import pytest
from fastapi.testclient import TestClient

from backend.app import futures_history as history
from backend.app import futures_learner as learner
from backend.app.db import FuturesLearningState, PaperTrade, Session
from backend.app.futures_strategies import SEEDS, genome_name
from backend.app.main import app
from backend.app.market import Candle
from backend.app.mexc import MarketDataError
from backend.app.strategy import Setup

client = TestClient(app)


@pytest.fixture
def replay_data(monkeypatch):
    monkeypatch.setattr(history, "WARMUP", 25)
    monkeypatch.setattr(history, "strategy_setup", lambda *args: Setup(
        side="long", entry=100, stop=90, target=104, reason="history test"))
    candles = [Candle(index * 900, 100, 105, 95, 102, 10) for index in range(225)]
    name = genome_name(SEEDS[0])
    candidates = {name: {"genome": SEEDS[0]}}
    return candidates, candles, copy.deepcopy(candles), [], 25 * 900, 225 * 900


def test_split_excludes_boundary_samples_and_preserves_population(replay_data):
    before = copy.deepcopy(replay_data)
    report = history.replay(*replay_data)
    assert replay_data == before
    assert report["source"] == "historical"
    assert report["train_before"] == 165 * 900
    row = report["strategies"][0]
    assert row["train"]["resolved"] == 139
    assert row["validation"]["resolved"] == 59
    assert row["train"]["excluded"] == row["validation"]["excluded"] == 1
    assert row["prior_eligible"]
    assert len(report["dataset_sha256"]) == 64


def test_no_future_candles_or_funding_seen_by_signal(replay_data, monkeypatch):
    seen = []
    def signal(genome, candles, funding):
        assert all(record["time"] <= candles[-1].time for record in funding)
        seen.append(candles[-1].time)
    monkeypatch.setattr(history, "strategy_setup", signal)
    args = list(replay_data)
    args[3] = [{"time": args[4], "rate": .01}, {"time": args[5] - 900, "rate": .02}]
    history.replay(*args)
    assert seen == list(range(args[4], args[5], 900))


def test_future_change_does_not_change_training_labels(replay_data):
    report = history.replay(*replay_data)
    changed = copy.deepcopy(replay_data)
    for index, bar in enumerate(changed[1]):
        if bar.time >= report["train_before"]:
            changed[1][index] = Candle(bar.time, 100, 103, 85, 90, 10)
    later = history.replay(*changed)
    assert later["strategies"][0]["train"] == report["strategies"][0]["train"]
    assert later["strategies"][0]["validation"] != report["strategies"][0]["validation"]


def test_rejects_missing_and_misaligned_bars(replay_data):
    changed = copy.deepcopy(replay_data)
    changed[1].pop(40)
    with pytest.raises((ValueError, MarketDataError)):
        history.replay(*changed)
    changed = copy.deepcopy(replay_data)
    changed[2].pop()
    with pytest.raises(ValueError, match="coverage"):
        history.replay(*changed)


def test_prior_is_bounded_and_never_qualifies_forward(replay_data):
    report = history.replay(*replay_data)
    name = report["strategies"][0]["strategy"]
    prior = {"report": report}
    cutoff = report["end_time"]
    context = next(iter(report["strategies"][0]["train"]["contexts"]))
    side, regime = context.split(":")
    assert math.isinf(learner.score([], name, side, regime, cutoff - 1, prior))
    actual = learner.score([], name, side, regime, cutoff, prior)
    assert math.isfinite(actual)
    bucket = report["strategies"][0]["train"]["contexts"][context]
    expected = bucket["total_r"] / bucket["resolved"] + math.sqrt(2 * math.log(31) / 30)
    assert actual == pytest.approx(expected)
    report["strategies"][0]["prior_eligible"] = False
    assert math.isinf(learner.score([], name, side, regime, cutoff, prior))
    candidate = next(iter(learner.new_state(1)["candidates"].values()))
    assert not learner.qualification(candidate)["eligible"]


def test_job_updates_preserve_forward_state_and_trade_count():
    state = learner.new_state(1000)
    state["cursor"] = 900
    state["historical"] = {"id": "test", "status": "running", "updated_at": 1000}
    with Session.begin() as session:
        learner.save_state(session, state)
        trades = session.query(PaperTrade).count()
    history.update_job("test", completed=96)
    with Session() as session:
        actual = session.get(FuturesLearningState, 1).payload
        assert {key: value for key, value in actual.items() if key != "historical"} == {
            key: value for key, value in state.items() if key != "historical"}
        assert session.query(PaperTrade).count() == trades
    with pytest.raises(ValueError, match="superseded"):
        history.update_job("wrong-id", completed=192)


def test_api_validation_and_background_job(monkeypatch):
    submitted = []
    def fake_run(*args):
        submitted.append(args)
        history.update_job(args[0], status="completed")
        history.research_lock.release()
    monkeypatch.setattr(history, "run_history", fake_run)
    assert client.get("/api/workspaces/mexc_futures/learning/history").json() is None
    for days in (0, 91, "30", 1.5):
        assert client.post("/api/workspaces/mexc_futures/learning/history",
                           json={"days": days}).status_code == 422
    response = client.post("/api/workspaces/mexc_futures/learning/history", json={"days": 7})
    assert response.status_code == 202
    assert submitted[0][1] == 7
    assert client.get("/api/workspaces/mexc_futures/learning/history").json()["status"] == "completed"


def test_concurrent_research_rejected():
    assert history.research_lock.acquire(blocking=False)
    try:
        assert client.post("/api/workspaces/mexc_futures/learning/history",
                           json={"days": 7}).status_code == 409
    finally:
        history.research_lock.release()


def test_worker_failure_preserves_previous_report(monkeypatch):
    class BrokenAdapter:
        def server_time(self):
            raise ValueError("dataset unavailable")
        def close(self):
            pass
    monkeypatch.setattr(history, "MexcFuturesAdapter", BrokenAdapter)
    state = learner.new_state(1000)
    state["historical"] = {"id": "test", "status": "running", "report": {"old": True}}
    with Session.begin() as session:
        learner.save_state(session, state)
    assert history.research_lock.acquire(blocking=False)
    history.run_history("test", 7, {})
    result = client.get("/api/workspaces/mexc_futures/learning/history").json()
    assert result["status"] == "failed" and result["report"] == {"old": True}
    assert not history.research_lock.locked()
