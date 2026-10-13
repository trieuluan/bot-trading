import csv
import io

import pytest
from fastapi.testclient import TestClient

from backend.app.db import ForexPaperTrade, Session
from backend.app.exness_feed import WORKSPACE
from backend.app.forex_reporting import trade_report
from backend.app.main import app

def test_empty_report_does_not_invent_performance():
    report = trade_report([], 10000)
    assert report["expectancy_r"] is None
    assert report["profit_factor"] is None
    assert report["profit_factor_status"] == "no_sample"
    assert report["max_closed_drawdown_usd"] == 0

def test_metrics_use_exit_order_realized_drawdown_and_exclude_non_closed():
    rows = [
        {"status": "resolved", "confirm_time": 1, "exit_time": 30, "side": "long", "pnl": 50, "net_r": 0.5},
        {"status": "resolved", "confirm_time": 2, "exit_time": 10, "side": "short", "pnl": 100, "net_r": 1},
        {"status": "resolved", "confirm_time": 3, "exit_time": 20, "side": "long", "pnl": -200, "net_r": -2},
        {"status": "censored", "confirm_time": 4, "pnl": -99999},
        {"status": "skipped", "confirm_time": 5, "pnl": 99999},
    ]
    report = trade_report(rows, 10000)
    assert report["expectancy_r"] == pytest.approx(-0.5 / 3)
    assert report["profit_factor"] == 0.75
    assert report["max_closed_drawdown_usd"] == 200
    assert report["max_closed_drawdown_percent"] == pytest.approx(200 / 10100 * 100)
    assert report["commission_unverified_count"] == 3
    assert report["groups"]["side"]["long"]["count"] == 2
    assert report["status_counts"]["censored"] == 1

def test_csv_contains_all_journals_but_only_requested_channel_and_blocks_formulas():
    with Session.begin() as session:
        for index in range(25):
            session.add(ForexPaperTrade(workspace_id=WORKSPACE, channel="live", confirm_time=index, snapshot={
                "confirm_time": index, "status": "skipped", "side": "long", "secret": "not-exported",
                "signal": {"reason": "=1+1", "trend": "up"},
            }))
        session.add(ForexPaperTrade(workspace_id=WORKSPACE, channel="replay", confirm_time=100, snapshot={"confirm_time": 100}))
    client = TestClient(app)
    response = client.get("/api/workspaces/exness_forex/paper/trades.csv?channel=live")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    rows = list(csv.DictReader(io.StringIO(response.text)))
    assert len(rows) == 25
    assert all(row["channel"] == "live" for row in rows)
    assert rows[0]["signal.reason"] == "'=1+1"
    assert "not-exported" not in response.text
    assert client.get("/api/workspaces/exness_forex/paper/trades.csv?channel=other").status_code == 422
