from datetime import datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from backend.app.db import (
    ForexObservation,
    MarketCandle,
    PaperWallet,
    Session,
    WorkspaceCandle,
    WorkspaceFeedState,
)
from backend.app.exness_feed import FRAMES, WORKSPACE, sync_page
from backend.app.exness_observation import (
    audit_frames,
    observation_summary,
    observation_tick,
    observe_forex,
)
from backend.tests.test_exness_feed import FakeApi

NOW = 720000

def bar(stamp, opening=100, high=101, low=99, close=100):
    return SimpleNamespace(time=stamp, open=opening, high=high, low=low, close=close)

def history(short=False):
    higher = [bar(index * 14400, close=200 - index * 2 if short else 50 + index)
              for index in range(50)]
    hours = [bar(NOW - (21 - index) * 3600) for index in range(21)]
    hours[-1] = bar(NOW - 3600, close=97 if short else 103)
    return hours, higher

@pytest.mark.parametrize("short", [False, True])
def test_native_frames_breakout_retest_then_separate_confirmation(short):
    hours, higher = history(short)
    initial = observe_forex(bar(NOW - 900), hours, higher)
    assert initial["status"] == "waiting_retest"
    assert initial["side"] == ("short" if short else "long")
    touch = bar(NOW, opening=97, high=100, low=96, close=98) if short else bar(NOW, opening=103, high=104, low=100, close=102)
    waiting = observe_forex(touch, hours, higher, initial)
    assert waiting["status"] == "waiting_confirm"
    assert waiting["confirm_time"] is None
    confirm = bar(NOW + 900, opening=98, high=99, low=95, close=96) if short else bar(NOW + 900, opening=102, high=105, low=101, close=104)
    confirmed = observe_forex(confirm, hours, higher, waiting)
    assert confirmed["status"] == "confirmed"
    assert confirmed["stop"] == 100
    assert confirmed["target"] == (88 if short else 112)
    assert confirmed["range_start"] == initial["range_start"]

def test_no_future_h1_h4_or_breakout_bar_retest():
    hours, higher = history()
    assert observe_forex(bar(NOW - 1800), hours, higher)["status"] == "warming_up"
    future_h4 = bar(NOW, close=1)
    future_h1 = bar(NOW, close=1)
    initial = observe_forex(bar(NOW - 900, low=90), hours + [future_h1], higher + [future_h4])
    assert initial["status"] == "waiting_retest"
    assert initial["touch_time"] is None
    assert initial["closed_4h_time"] == NOW - 14400

def test_gap_reversal_expiry_and_trend_cancel_frozen_setup():
    hours, higher = history()
    initial = observe_forex(bar(NOW - 900), hours, higher)
    assert observe_forex(bar(NOW + 900), hours, higher, initial)["status"] == "cancelled"
    assert observe_forex(bar(NOW, close=98), hours, higher, initial)["status"] == "cancelled"
    expired = {**initial, "expires_at": NOW}
    assert observe_forex(bar(NOW), hours, higher, expired)["status"] == "expired"
    descending = [bar(index * 14400, close=200 - index * 2) for index in range(50)]
    assert observe_forex(bar(NOW), hours, descending, initial)["status"] == "cancelled"

def test_quality_detects_unpaired_crossed_prices_and_unknown_gaps():
    frames = {frame: [] for frame in FRAMES}
    for frame, (_, step) in FRAMES.items():
        for stamp in (0, step * 3):
            for side in ("bid", "ask"):
                row = bar(stamp)
                row.price_type = side
                frames[frame].append(row)
    quality = audit_frames(frames)
    assert quality["status"] == "checked"
    assert quality["session_calendar_verified"] is False
    assert all(row["gap_count"] == 1 for row in quality["frames"])
    frames["15m"].pop()
    assert audit_frames(frames)["status"] == "blocked"
    frames["1h"][1].close = 99
    assert audit_frames(frames)["frames"][1]["crossed_bars"] == 1

def test_worker_cursor_restart_catchup_idempotency_and_mexc_isolation():
    for frame in FRAMES:
        sync_page(FakeApi(), frame, days=1, now=NOW)
    assert observation_tick(NOW)["status"] == "warming_up"
    observation_tick(NOW)
    with Session() as session:
        assert session.query(ForexObservation).count() == 1
        assert session.get(PaperWallet, 1).balance == 10000
    for frame in FRAMES:
        sync_page(FakeApi(), frame, days=1, now=NOW + 3600)
    observation_tick(NOW + 3600)
    with Session() as session:
        rows = session.scalars(select(ForexObservation).order_by(ForexObservation.candle_time)).all()
        assert len(rows) == 5
        assert rows[-1].candle_time == NOW + 2700
        assert rows[1].snapshot["provenance"] == "catch_up"
    with Session.begin() as session:
        session.get(WorkspaceFeedState, (WORKSPACE, "XAUUSD", "1h")).error = "sync failed"
    assert observation_tick(NOW + 3600)["status"] == "blocked"
    summary = observation_summary(NOW + 3600)
    assert summary["status"] == "data_paused"
    assert summary["snapshot"]["candle_time"] == NOW + 2700

@pytest.mark.parametrize("offline_hours", [6, 30])
def test_sleep_resume_matches_continuous_observation_and_reports_backlog(offline_hours):
    start = 80 * 14400
    end = start + offline_hours * 3600

    class TrendingApi(FakeApi):
        def candles(self, symbol, code, begin, finish, now=None):
            rows = super().candles(symbol, code, begin, finish, now)
            for row in rows:
                stamp = int(datetime.fromisoformat(row["time"]).timestamp())
                if code == "H4":
                    close, low = 100 + stamp // 14400, 99 + stamp // 14400
                elif stamp == start - (3600 if code == "H1" else 900):
                    close, low = 103, 102
                elif code == "M15" and stamp == start:
                    close, low = 102, 100
                elif code == "M15" and stamp > start:
                    close, low = 104, 103
                else:
                    close, low = 100, 99
                for side, spread in (("bid", 0), ("ask", 1)):
                    row[side] = {"open": close + spread - 0.5, "close": close + spread,
                                 "low": low + spread, "high": close + spread + 1}
            return rows

    api = TrendingApi()
    for frame in FRAMES:
        sync_page(api, frame, days=10, now=start)
    assert observation_tick(start)["status"] == "waiting_retest"
    for current in range(start + 900, end + 1, 900):
        for frame in FRAMES:
            sync_page(api, frame, days=10, now=current)
        observation_tick(current)
    with Session() as session:
        expected = [dict(row.snapshot) for row in session.scalars(
            select(ForexObservation).order_by(ForexObservation.candle_time)
        )]
    assert expected[1]["status"] == "waiting_confirm"
    assert expected[2]["status"] == "confirmed"

    with Session.begin() as session:
        session.query(ForexObservation).filter(ForexObservation.candle_time >= start).delete()
        session.query(WorkspaceCandle).filter(WorkspaceCandle.time >= start).delete()
        for frame, (_, step) in FRAMES.items():
            session.get(WorkspaceFeedState, (WORKSPACE, "XAUUSD", frame)).checked_until = start // step * step
    assert observation_summary(end)["status"] == "data_paused"
    api.candle_limit = 48
    for frame in FRAMES:
        while sync_page(api, frame, days=10, now=end)["status"] == "backfill":
            assert observation_tick(end)["status"] == "blocked"

    progress = observation_summary(end)
    assert progress["status"] == "catching_up"
    assert progress["catch_up"]["pending_bars"] == offline_hours * 4
    assert progress["catch_up"]["last_processed_candle"] == start - 900
    assert progress["catch_up"]["latest_closed_candle"] == end - 900
    observation_tick(end)
    assert observation_summary(end)["catch_up"]["pending_bars"] == max(0, offline_hours * 4 - 96)
    while observation_summary(end)["catch_up"]["pending_bars"]:
        observation_tick(end)
    observation_tick(end)
    with Session() as session:
        actual = [dict(row.snapshot) for row in session.scalars(
            select(ForexObservation).order_by(ForexObservation.candle_time)
        )]
        assert len(actual) == len(expected)
        assert actual[1]["provenance"] == "catch_up"
        assert actual[-1]["provenance"] == "live"
        assert session.query(MarketCandle).count() == 0
        assert session.get(PaperWallet, 1).balance == 10000
    for snapshots in (expected, actual):
        for snapshot in snapshots:
            snapshot.pop("provenance")
    assert actual == expected
    assert observation_summary(end)["status"] != "catching_up"
