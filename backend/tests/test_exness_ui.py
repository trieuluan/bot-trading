import json

import pytest
from fastapi.testclient import TestClient

from backend.app import main

def test_research_missing_report_is_explicit(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "RESEARCH_DIRECTORY", tmp_path)
    response = TestClient(main.app).get("/api/workspaces/exness_forex/research")
    assert response.status_code == 200
    assert response.json() == {"available": False, "reports": [], "comparison": None, "gap_audits": [], "error": None}

@pytest.mark.parametrize("slippage_bps", [0, 2])
def test_research_projects_only_fixed_exness_reports(tmp_path, monkeypatch, slippage_bps):
    monkeypatch.setattr(main, "RESEARCH_DIRECTORY", tmp_path)
    report = {
        "version": "exness_mtf_replay_v1", "source": "exness_forex", "symbol": "XAUUSD",
        "as_of": 2000, "start": 0, "end": 1800, "dataset_sha256": "a" * 64,
        "processed_bars": 2, "evaluated_bars": 1,
        "summary": {"confirmations": 0, "resolved": 0, "win_rate": None, "mean_r_ex_swap": None,
                    "sum_r_ex_swap": 0, "profit_factor_ex_swap": None, "closed_sample_drawdown_r": 0},
        "assumptions": {"slippage_bps_each_side": slippage_bps},
        "by_side": {side: {"resolved": 0, "sum_r_ex_swap": 0, "win_rate": None} for side in ("long", "short")},
        "by_month": {}, "samples": [{"private_field": "do not expose"}],
    }
    (tmp_path / "exness-mtf-replay.json").write_text(json.dumps(report))
    (tmp_path / "unrelated.json").write_text("do not expose")
    data = TestClient(main.app).get("/api/workspaces/exness_forex/research").json()
    assert data["available"] is True
    assert len(data["reports"]) == 1
    assert data["reports"][0]["assumptions"]["slippage_bps_each_side"] == slippage_bps
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


def comparison_report():
    return {
        "version": "exness_portfolio_compare_v1", "source": "exness_forex", "symbol": "XAUUSD",
        "as_of": 2000, "start": 0, "end": 1800, "processed_bars": 2,
        "dataset_sha256": "a" * 64, "assumptions": {"swap_included": False},
        "private_field": "do not expose",
        "results": [{
            "strategy": strategy, "slippage_bps": bps, "signals": 0, "accepted": 0,
            "skipped_busy": 0, "trades": 0, "win_rate": None, "final_balance": 10000,
            "return_pct": 0, "profit_factor": None, "max_drawdown_pct": 0, "mean_r": None,
            "status": "complete", "blocked_at": None, "equity_verified": True,
            "rejection_reasons": {}, "samples": [{"private_field": "do not expose"}],
        } for strategy in ("breakout_retest", "trend_pullback", "range_rejection", "regime_router") for bps in (0, 1, 2)],
    }


def test_comparison_only_report_projects_metrics_without_trades(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "RESEARCH_DIRECTORY", tmp_path)
    (tmp_path / "exness-strategy-compare.json").write_text(json.dumps(comparison_report()))
    data = TestClient(main.app).get("/api/workspaces/exness_forex/research").json()
    assert data["available"] and len(data["comparison"]["results"]) == 12
    assert data["reports"] == []
    assert "do not expose" not in json.dumps(data)


@pytest.mark.parametrize("invalid", ["wrong_source", "duplicate", "infinite", "incomplete", "bad_date", "bad_rejections"])
def test_invalid_comparison_is_safe_and_explicit(tmp_path, monkeypatch, invalid):
    monkeypatch.setattr(main, "RESEARCH_DIRECTORY", tmp_path)
    report = comparison_report()
    if invalid == "wrong_source":
        report["source"] = "mexc_futures"
    elif invalid == "duplicate":
        report["results"][-1] = report["results"][0]
    elif invalid == "infinite":
        report["results"][0]["final_balance"] = float("inf")
    elif invalid == "incomplete":
        report["results"].pop()
    elif invalid == "bad_date":
        report["end"] = "invalid"
    else:
        report["results"][0]["rejection_reasons"] = []
    (tmp_path / "exness-strategy-compare.json").write_text(json.dumps(report))
    response = TestClient(main.app).get("/api/workspaces/exness_forex/research")
    assert response.status_code == 200
    assert response.json()["comparison"] is None and response.json()["error"]


def test_research_replay_saves_atomic_report_without_touching_paper(tmp_path, monkeypatch):
    from backend.app.db import ForexPaperAccount, ForexPaperTrade, Session
    from scripts import exness_mtf_replay
    monkeypatch.setattr(main, "RESEARCH_DIRECTORY", tmp_path)
    monkeypatch.setattr(exness_mtf_replay, "run_comparison", lambda frames, now: comparison_report())
    response = TestClient(main.app).post("/api/workspaces/exness_forex/research/replay")
    assert response.status_code == 200
    data = response.json()
    assert data["available"] is True
    assert len(data["comparison"]["results"]) == 12
    assert (tmp_path / "exness-strategy-compare.json").exists()
    assert not list(tmp_path.glob("*.tmp"))
    with Session() as session:
        assert session.query(ForexPaperAccount).count() == 0
        assert session.query(ForexPaperTrade).count() == 0


def test_failed_replay_preserves_old_report_and_releases_lock(tmp_path, monkeypatch):
    from scripts import exness_mtf_replay

    monkeypatch.setattr(main, "RESEARCH_DIRECTORY", tmp_path)
    path = tmp_path / "exness-strategy-compare.json"
    path.write_text("existing report")
    def insufficient(frames, now):
        raise ValueError("Insufficient closed warmup history")
    monkeypatch.setattr(exness_mtf_replay, "run_comparison", insufficient)
    response = TestClient(main.app).post("/api/workspaces/exness_forex/research/replay")
    assert response.status_code == 422
    assert path.read_text() == "existing report"
    assert not main.FOREX_REPLAY_LOCK.locked()


def test_duplicate_replay_is_rejected_without_unlocking_active_run():
    main.FOREX_REPLAY_LOCK.acquire()
    try:
        response = TestClient(main.app).post("/api/workspaces/exness_forex/research/replay")
        assert response.status_code == 409
        assert main.FOREX_REPLAY_LOCK.locked()
    finally:
        main.FOREX_REPLAY_LOCK.release()
