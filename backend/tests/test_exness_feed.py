from datetime import datetime, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.app.db import MarketCandle, PaperWallet, Session, WorkspaceCandle, WorkspaceFeedState
from backend.app.exness import ExnessError, ExnessReadOnly
from backend.app.exness_feed import (
    FRAMES,
    WORKSPACE,
    connect_exness,
    feed_summary,
    normalize_rows,
    sync_page,
)
from backend.app.main import app

NOW = 1791892800


def candle(timestamp, price=4000):
    return {"time": datetime.fromtimestamp(timestamp, timezone.utc).isoformat(), "volume": 12,
            "bid": {"open": price, "high": price + 2, "low": price - 1, "close": price + 1},
            "ask": {"open": price + 1, "high": price + 3, "low": price, "close": price + 2}}


class FakeApi:
    candle_limit = 1000

    def __init__(self, rows=None, error=None):
        self.rows = rows
        self.error = error
        self.calls = []

    def candles(self, symbol, code, start, end, now=None):
        self.calls.append((symbol, code, start, end))
        if self.error:
            raise self.error
        if self.rows is not None:
            return self.rows
        step = next(step for api_code, step in FRAMES.values() if api_code == code)
        return [candle(stamp) for stamp in range(start, end, step)]


def test_backfill_atomic_pairs_and_independent_timeframes(monkeypatch):
    monkeypatch.setattr("backend.app.exness_feed.utcnow", lambda: datetime.fromtimestamp(NOW, timezone.utc))
    api = FakeApi()
    for frame in FRAMES:
        result = sync_page(api, frame, days=1, now=NOW)
        assert result["status"] == "current"
        assert result["remaining_intervals"] == 0
    with Session() as session:
        assert session.query(WorkspaceCandle).count() == 2 * (96 + 24 + 6)
        assert session.query(MarketCandle).count() == 0
        assert session.get(PaperWallet, 1).balance == 10000
        assert session.get(WorkspaceFeedState, (WORKSPACE, "15m")).checked_until == NOW
    with TestClient(app) as client:
        data = client.get("/api/workspaces/exness_forex/dashboard").json()
        assert data["market"]["bars"] == 96
        assert data["market"]["status"] == "synced"
        assert data["market"]["worker_online"] is False
        rows = client.get("/api/workspaces/exness_forex/candles?limit=1").json()["candles"]
        assert rows[0]["tick_volume"] == 12
        assert "volume" not in rows[0]


def test_empty_session_pages_advance_cursor_and_restart_resumes():
    api = FakeApi(rows=[])
    api.candle_limit = 2
    first = sync_page(api, "15m", days=1, now=NOW)
    second_api = FakeApi(rows=[])
    second_api.candle_limit = 2
    second = sync_page(second_api, "15m", days=1, now=NOW)
    assert first["remaining_intervals"] == 94
    assert second["remaining_intervals"] == 92
    assert second_api.calls[0][2] == api.calls[0][3]
    with Session() as session:
        assert session.query(WorkspaceCandle).count() == 0


def test_overlap_is_idempotent_and_updates_revised_closed_bars():
    api = FakeApi()
    sync_page(api, "15m", days=1, now=NOW)
    assert sync_page(api, "15m", days=1, now=NOW)["bars"] == 0
    assert len(api.calls) == 1
    correction = FakeApi(rows=[candle(NOW - 900, price=4010)])
    sync_page(correction, "15m", days=1, now=NOW + 61)
    with Session() as session:
        assert session.query(WorkspaceCandle).count() == 192
        saved = session.get(WorkspaceCandle, (WORKSPACE, "XAUUSD", "15m", "bid", NOW - 900))
        assert saved.open == 4010


def test_invalid_ask_rolls_back_whole_page_then_retries_from_same_boundary():
    row = candle(NOW - 86400)
    del row["ask"]["low"]
    api = FakeApi(rows=[row])
    with pytest.raises(ExnessError):
        sync_page(api, "15m", days=1, now=NOW)
    with Session() as session:
        state = session.get(WorkspaceFeedState, (WORKSPACE, "15m"))
        assert state.checked_until is None
        assert state.error
        assert session.query(WorkspaceCandle).count() == 0
    assert sync_page(api, "15m", days=1, now=NOW + 1)["status"] == "cooldown"
    assert len(api.calls) == 1
    good = FakeApi()
    sync_page(good, "15m", days=1, now=NOW + 61)
    assert good.calls[0][2] == api.calls[0][2]


def test_retry_after_survives_api_instance_restart():
    bad = FakeApi(error=ExnessError("Exness rate limit; waiting before retry", retry_after=120))
    with pytest.raises(ExnessError):
        sync_page(bad, "15m", days=1, now=NOW)
    good = FakeApi()
    assert sync_page(good, "15m", days=1, now=NOW + 119)["status"] == "cooldown"
    assert not good.calls
    assert sync_page(good, "15m", days=1, now=NOW + 121)["status"] == "current"


@pytest.mark.parametrize("kind", ["nan", "negative_volume", "float_volume", "bad_high", "crossed", "naive", "unclosed", "unaligned", "conflict"])
def test_normalizer_rejects_malformed_or_ambiguous_rows(kind):
    row = candle(NOW - 900)
    rows = [row]
    if kind == "nan":
        row["bid"]["open"] = float("nan")
    elif kind == "negative_volume":
        row["volume"] = -1
    elif kind == "float_volume":
        row["volume"] = 1.5
    elif kind == "bad_high":
        row["ask"]["high"] = 1
    elif kind == "crossed":
        row["ask"]["close"] = 3999
    elif kind == "naive":
        row["time"] = row["time"].replace("+00:00", "")
    elif kind == "unclosed":
        rows = [candle(NOW)]
    elif kind == "unaligned":
        rows = [candle(NOW - 899)]
    else:
        rows.append(candle(NOW - 900, price=4010))
    with pytest.raises(ExnessError):
        normalize_rows(rows, "15m", NOW - 1800, NOW)


def test_duplicates_deduplicate_and_missing_volume_stays_null():
    row = candle(NOW - 900)
    del row["volume"]
    normalized = normalize_rows([row, row], "15m", NOW - 1800, NOW)
    assert len(normalized) == 2
    assert all(item["tick_volume"] is None for item in normalized)


def test_http_rate_limit_sanitized_and_retry_header_used(monkeypatch):
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(
        429, headers={"Retry-After": "120"}, json={"code": "DO_NOT_LEAK_RESPONSE"},
    )))
    api = ExnessReadOnly("key", "00" * 32, "123", "https://api.exness.com", client=client)
    with pytest.raises(ExnessError) as error:
        api.get("/v1/test")
    assert error.value.retry_after == 120
    assert "DO_NOT_LEAK_RESPONSE" not in str(error.value)
    client.close()


def test_request_spacing_obeys_configured_rate(monkeypatch):
    clock = [100.0]
    waits = []

    def sleep(seconds):
        waits.append(seconds)
        clock[0] += seconds

    monkeypatch.setattr("backend.app.exness.time.monotonic", lambda: clock[0])
    monkeypatch.setattr("backend.app.exness.time.sleep", sleep)
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={})))
    api = ExnessReadOnly("key", "00" * 32, "123", "https://api.exness.com", client=client)
    api.get("/v1/test")
    api.get("/v1/test")
    assert waits == pytest.approx([0.55])
    client.close()


def test_connect_uses_account_specific_candle_limits(monkeypatch):
    class ConfigApi:
        request_interval = 0.55

        def __init__(self):
            self.client = httpx.Client()

        def use_access_point(self):
            pass

        def limits(self):
            return {"limits": {"rest": {"global_account_rate": {"limit": 10, "window_seconds": 1},
                    "methods": [{"api_operation_id": "getCandleHistory", "rate_limit": {"limit": 1, "window_seconds": 2},
                                 "limits": {"max_candles_per_request": 300}}]}}}

        def instruments(self):
            return {"instruments": ["XAUUSD"]}

    monkeypatch.setenv("EXNESS_ENVIRONMENT", "demo")
    monkeypatch.setenv("EXNESS_SYMBOL", "XAUUSD")
    monkeypatch.setattr(ExnessReadOnly, "from_env", ConfigApi)
    api = connect_exness()
    assert api.candle_limit == 300
    assert api.request_interval == 2.05
    api.client.close()


def test_feed_summary_never_labels_disconnected_worker_as_online():
    assert all(not row["worker_online"] for row in feed_summary())
