from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from backend.app.db import MtfResearchSample, PaperTrade, Session, Signal
from backend.app.main import app
from backend.app.market import Candle
from backend.app.mtf_research import advance, collect_sample, extract_features


def pending(side="long"):
    return {
        "status": "pending",
        "label": None,
        "last_candle": 0,
        "side": side,
        "stop": 90 if side == "long" else 110,
        "target": 120 if side == "long" else 80,
        "horizon_bars": 96,
        "fee_bps": 5,
        "slippage_bps": 2,
    }


@pytest.mark.parametrize("side", ["long", "short"])
def test_entry_next_open_costs_and_conservative_ambiguous_exit(side):
    state = pending(side)
    resolved = advance(state, Candle(900, 100, 125, 75, 100, 1))
    assert resolved["outcome"] == "stop" and resolved["label"] == 0
    assert resolved["net_r_ex_funding"] < -1
    assert resolved["label_available_at"] == 1800
    assert state["status"] == "pending"
    assert advance(resolved, Candle(1800, 100, 130, 70, 100, 1)) == resolved


def test_gaps_invalid_entry_timeout_and_target():
    state = pending()
    assert advance(state, Candle(1800, 100, 101, 99, 100, 1))["status"] == "censored"
    assert advance(state, Candle(900, 85, 100, 80, 95, 1))["status"] == "invalid"
    assert advance(state, Candle(900, 125, 126, 100, 110, 1))["status"] == "invalid"
    opened = advance(state, Candle(900, 100, 101, 99, 100, 1))
    assert opened["status"] == "open" and opened["label"] is None
    assert advance(opened, Candle(900, 100, 130, 80, 100, 1)) == opened
    target = advance(opened, Candle(1800, 100, 121, 99, 120, 1))
    assert target["outcome"] == "target" and target["label"] == 1
    assert 0 < target["net_r_ex_funding"] < 2
    stop_gap = advance(opened, Candle(1800, 80, 85, 75, 82, 1))
    assert stop_gap["outcome"] == "stop_gap" and stop_gap["net_r_ex_funding"] < -1
    for index in range(2, 97):
        opened = advance(opened, Candle(index * 900, 100, 101, 99, 100, 1))
    assert opened["outcome"] == "timeout" and opened["bars"] == 96


def test_frozen_features_persistence_export_and_no_orders():
    candles = [Candle(index * 900, 100, 102, 98, 101, 10) for index in range(1600)]
    last = candles[-1]
    snapshot = {
        "strategy": "mtf_observer_v1",
        "parameters": {"target_r": 2},
        "status": "confirmed",
        "confirm_time": last.time,
        "side": "long",
        "breakout_time": last.time - 5400,
        "touch_time": last.time - 900,
        "stop": 90,
        "target": 120,
        "range_high": 102,
        "range_low": 98,
    }
    features = extract_features(candles, snapshot)
    assert features["atr_15m_14"] == 4 and features["confirmation_volume_ratio"] == 1
    assert features["available_at"] == last.time + 900 and features["funding_rate"] is None
    with pytest.raises(ValueError):
        extract_features(candles + [replace(last, time=last.time + 900)], snapshot)
    with Session.begin() as session:
        collect_sample(session, candles, snapshot)
        session.flush()
        collect_sample(session, candles, snapshot)
        assert session.query(MtfResearchSample).count() == 1
        assert session.get(MtfResearchSample, last.time).evaluation["status"] == "pending"
    with Session.begin() as session:
        future = Candle(last.time + 900, 100, 121, 99, 120, 15)
        collect_sample(session, candles + [future], {"status": "waiting_breakout"})
        sample = session.get(MtfResearchSample, last.time)
        assert sample.features == features
        assert sample.evaluation["label"] == 1
        assert session.query(Signal).count() == session.query(PaperTrade).count() == 0
    with TestClient(app) as client:
        response = client.get("/api/observations/mtf/dataset?limit=1").json()
        assert response["samples"][0]["features"] == features
        assert response["funding_included"] is False and response["next_after"] is None
        assert client.get("/api/observations/mtf/dataset?limit=0").status_code == 422
        assert (
            client.get(f"/api/observations/mtf/dataset?after={last.time}").json()["samples"] == []
        )
