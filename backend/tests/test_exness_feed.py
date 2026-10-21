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
    feed_retry_delay,
    feed_summary,
    normalize_rows,
    record_connection_failure,
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
        from backend.app.exness_feed import HISTORY_FRAMES
        step = next(s for c, s in HISTORY_FRAMES.values() if c == code)
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
        assert session.get(WorkspaceFeedState, (WORKSPACE, "XAUUSD", "15m")).checked_until == NOW
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
    assert sync_page(api, "15m", days=1, now=NOW + 61)["bars"] == 0
    assert len(api.calls) == 1
    correction = FakeApi(rows=[candle(NOW - 900, price=4010)])
    sync_page(correction, "15m", days=1, now=NOW + 900)
    with Session() as session:
        assert session.query(WorkspaceCandle).count() == 192
        saved = session.get(WorkspaceCandle, (WORKSPACE, "XAUUSD", "15m", "bid", NOW - 900))
        assert saved.open == 4010

@pytest.mark.parametrize("frame", FRAMES)
def test_synced_frame_waits_for_next_closed_candle(frame):
    api = FakeApi()
    sync_page(api, frame, days=1, now=NOW)
    for elapsed in (61, FRAMES[frame][1] - 1):
        assert sync_page(api, frame, days=1, now=NOW + elapsed)["bars"] == 0
    assert len(api.calls) == 1

def test_rate_limit_cooldown_covers_all_frames_after_restart():
    bad = FakeApi(error=ExnessError("Exness rate limit; waiting before retry", 120, rate_limited=True))
    with pytest.raises(ExnessError):
        sync_page(bad, "15m", days=1, now=NOW)
    restarted = FakeApi()
    for frame in FRAMES:
        assert sync_page(restarted, frame, days=1, now=NOW + 119)["status"] == "cooldown"
    assert not restarted.calls
    assert sync_page(restarted, "15m", days=1, now=NOW + 121)["status"] == "current"

def test_small_page_limit_still_advances_incremental_cursor():
    api = FakeApi()
    sync_page(api, "15m", days=1, now=NOW)
    api.candle_limit = 1
    result = sync_page(api, "15m", days=1, now=NOW + 900)
    assert result["status"] == "current"
    assert api.calls[-1][2:] == (NOW, NOW + 900)


def test_invalid_ask_rolls_back_whole_page_then_retries_from_same_boundary():
    row = candle(NOW - 86400)
    del row["ask"]["low"]
    api = FakeApi(rows=[row])
    with pytest.raises(ExnessError):
        sync_page(api, "15m", days=1, now=NOW)
    with Session() as session:
        state = session.get(WorkspaceFeedState, (WORKSPACE, "XAUUSD", "15m"))
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
    assert error.value.rate_limited
    assert "DO_NOT_LEAK_RESPONSE" not in str(error.value)
    client.close()

def test_client_does_not_send_more_requests_during_rate_limit(monkeypatch):
    clock = [100.0]
    calls = []
    monkeypatch.setattr("backend.app.exness.time.monotonic", lambda: clock[0])
    def respond(request):
        calls.append(request)
        return httpx.Response(429, headers={"Retry-After": "120"})
    client = httpx.Client(transport=httpx.MockTransport(respond))
    api = ExnessReadOnly("key", "00" * 32, "123", "https://api.exness.com", client=client)
    for path in ("/v1/test", "/v1/other"):
        with pytest.raises(ExnessError) as error:
            api.get(path)
        assert error.value.rate_limited
    assert len(calls) == 1
    clock[0] += 121
    with pytest.raises(ExnessError):
        api.get("/v1/test")
    assert len(calls) == 2
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
    assert api.request_interval == 4.05
    api.client.close()


def test_feed_summary_never_labels_disconnected_worker_as_online():
    assert all(not row["worker_online"] for row in feed_summary())


def test_feed_summary_omits_legacy_m1_from_active_catchup_view():
    with Session.begin() as session:
        session.merge(WorkspaceFeedState(workspace_id=WORKSPACE, symbol="XAUUSD", timeframe="1m", checked_until=NOW - 3600))
    timeframes = [row["timeframe"] for row in feed_summary("XAUUSD")]
    assert "1m" not in timeframes
    assert set(timeframes).issubset(set(FRAMES))

def test_connection_cooldown_respects_retry_after_and_never_shortens(monkeypatch):
    monkeypatch.setattr("backend.app.exness_feed.utcnow", lambda: datetime.fromtimestamp(NOW, timezone.utc))
    record_connection_failure(120)
    assert feed_retry_delay() == 120
    record_connection_failure(60)
    assert feed_retry_delay() == 120

def test_worker_keeps_heartbeat_without_api_calls_during_cooldown(monkeypatch):
    from bot import exness_worker

    waits, heartbeats, connections = [], [], []

    class Stop:
        def is_set(self):
            return len(waits) >= 2

        def wait(self, seconds):
            waits.append(seconds)

    monkeypatch.setenv("EXNESS_DATA_ENABLED", "true")
    monkeypatch.setattr(exness_worker.threading, "Event", Stop)
    monkeypatch.setattr(exness_worker.signal, "signal", lambda *args: None)
    monkeypatch.setattr(exness_worker, "validate_mode", lambda: None)
    monkeypatch.setattr(exness_worker, "worker_heartbeat", heartbeats.append)
    monkeypatch.setattr(exness_worker, "feed_retry_delay", lambda *_: 120)
    monkeypatch.setattr(exness_worker, "connect_exness", lambda: connections.append(True))
    exness_worker.main()
    assert waits == [15, 15]
    assert heartbeats == [True, True, False]
    assert connections == []

def test_recheck_gap_persists_empty_response_without_faking_closure(monkeypatch):
    from backend.app.db import WorkspaceCandle
    from backend.app.exness_gaps import gap_audits, recheck_gap
    # Seed 2 candles with a 4-hour gap mid-week
    start = 1791892800  # Wednesday
    with Session.begin() as session:
        for offset in (0, 14400):
            session.add(WorkspaceCandle(workspace_id=WORKSPACE, symbol="XAUUSD", timeframe="15m",
                                       price_type="bid", time=start + offset, open=4000, high=4001,
                                       low=3999, close=4000))
    api = FakeApi(rows=[])
    res = recheck_gap(api, now=start + 28800)
    assert res["status"] == "no_bars_returned"
    with Session() as session:
        audits = gap_audits(session)
        assert len(audits) == 1
        assert audits[0]["status"] == "no_bars_returned"

def test_m1_backfills_older_history_after_catching_up(monkeypatch):
    monkeypatch.setattr("backend.app.exness_feed.utcnow", lambda: datetime.fromtimestamp(NOW, timezone.utc))
    api = FakeApi()
    api.candle_limit = 600
    assert sync_page(api, "1m", days=1, now=NOW)["status"] in ("current", "backfill")
    while sync_page(api, "1m", days=1, now=NOW)["status"] == "backfill":
        pass
    with Session() as session:
        first = session.get(WorkspaceFeedState, (WORKSPACE, "XAUUSD", "1m")).started_at
    results = []
    while True:
        result = sync_page(api, "1m", days=2, now=NOW, older=True)
        results.append(result["status"])
        if result["status"] == "current":
            break
    assert "backfill_history" in results
    with Session() as session:
        state = session.get(WorkspaceFeedState, (WORKSPACE, "XAUUSD", "1m"))
        assert state.started_at == NOW // 60 * 60 - 2 * 86400 < first
        assert state.checked_until == NOW
        assert session.query(WorkspaceCandle).filter_by(timeframe="1m", price_type="bid").count() == 2 * 1440

def test_older_backfill_waits_for_live_cursor_and_rolls_back_invalid_page():
    api = FakeApi()
    api.candle_limit = 60
    sync_page(api, "1m", days=1, now=NOW)
    before_calls = len(api.calls)
    result = sync_page(api, "1m", days=30, now=NOW, older=True)
    assert result["status"] == "waiting_live_sync"
    assert len(api.calls) == before_calls
    with Session.begin() as session:
        feed = session.get(WorkspaceFeedState, (WORKSPACE, "XAUUSD", "1m"))
        feed.checked_until = NOW
        original_start = feed.started_at
    api.rows = [candle(NOW + 60)]
    with pytest.raises(ExnessError, match="candles"):
        sync_page(api, "1m", days=30, now=NOW, older=True)
    with Session() as session:
        feed = session.get(WorkspaceFeedState, (WORKSPACE, "XAUUSD", "1m"))
        assert feed.started_at == original_start
        assert feed.checked_until == NOW

def test_throttle_backoff_grows_and_caps():
    from bot.exness_worker import throttle_delay
    assert [throttle_delay(60, n) for n in (1, 2, 3, 9)] == [60, 120, 240, 900]
    assert throttle_delay(1200, 1) == 1200

def test_current_feed_clears_stale_error_without_api_call():
    api = FakeApi()
    sync_page(api, "15m", days=1, now=NOW)
    with Session.begin() as session:
        feed = session.get(WorkspaceFeedState, (WORKSPACE, "XAUUSD", "15m"))
        feed.error = "stale"
    calls = len(api.calls)
    assert sync_page(api, "15m", days=1, now=NOW)["status"] == "current"
    assert len(api.calls) == calls
    with Session() as session:
        assert session.get(WorkspaceFeedState, (WORKSPACE, "XAUUSD", "15m")).error is None
