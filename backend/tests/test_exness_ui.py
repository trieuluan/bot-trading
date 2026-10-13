import json

import pytest
from fastapi.testclient import TestClient

from backend.app import main

def test_research_missing_report_is_explicit(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "RESEARCH_DIRECTORY", tmp_path)
    response = TestClient(main.app).get("/api/workspaces/exness_forex/research")
    assert response.status_code == 200
    assert response.json() == {"available": False, "reports": [], "error": None}

def test_research_projects_only_fixed_exness_reports(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "RESEARCH_DIRECTORY", tmp_path)
    report = {
        "version": "exness_mtf_replay_v1", "source": "exness_forex", "symbol": "XAUUSD",
        "as_of": 2000, "start": 0, "end": 1800, "dataset_sha256": "a" * 64,
        "processed_bars": 2, "evaluated_bars": 1,
        "summary": {"confirmations": 0, "resolved": 0, "win_rate": None, "mean_r_ex_swap": None,
                    "sum_r_ex_swap": 0, "profit_factor_ex_swap": None, "closed_sample_drawdown_r": 0},
        "assumptions": {"slippage_bps_each_side": 0},
        "by_side": {side: {"resolved": 0, "sum_r_ex_swap": 0, "win_rate": None} for side in ("long", "short")},
        "by_month": {}, "samples": [{"private_field": "do not expose"}],
    }
    (tmp_path / "exness-mtf-replay.json").write_text(json.dumps(report))
    (tmp_path / "unrelated.json").write_text("do not expose")
    data = TestClient(main.app).get("/api/workspaces/exness_forex/research").json()
    assert data["available"] is True
    assert len(data["reports"]) == 1
    assert "samples" not in data["reports"][0]
    assert "do not expose" not in json.dumps(data)
    report["summary"]["sum_r_ex_swap"] = "invalid metric"
    (tmp_path / "exness-mtf-replay.json").write_text(json.dumps(report))
    assert TestClient(main.app).get("/api/workspaces/exness_forex/research").json()["available"] is False

@pytest.mark.parametrize("content", ['{"source":"mexc_futures"}', '{"invalid":', '[]'])
def test_research_wrong_or_corrupt_report_does_not_leak_contents(tmp_path, monkeypatch, content):
    monkeypatch.setattr(main, "RESEARCH_DIRECTORY", tmp_path)
    (tmp_path / "exness-mtf-replay.json").write_text(content)
    data = TestClient(main.app).get("/api/workspaces/exness_forex/research").json()
    assert data["available"] is False
    assert data["error"]
    assert data["reports"] == []
