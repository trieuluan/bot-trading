import pytest
from fastapi.testclient import TestClient

from backend.app.main import app


@pytest.mark.parametrize("method,path", [
    ("get", "/api/workspaces/exness_forex/research"),
    ("post", "/api/workspaces/exness_forex/research/replay"),
])
def test_removed_research_routes(method, path):
    assert getattr(TestClient(app), method)(path).status_code == 404


def test_dashboard_no_longer_loads_legacy_paper(monkeypatch):
    def unexpected_call():
        pytest.fail("Dashboard must not load retired Paper portfolios")

    monkeypatch.setattr("backend.app.forex_paper.forex_paper_summary", unexpected_call)
    response = TestClient(app).get("/api/workspaces/exness_forex/dashboard")
    assert response.status_code == 200
    assert "paper" not in response.json()
