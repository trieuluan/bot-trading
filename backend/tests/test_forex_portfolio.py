import pytest

from backend.tests.test_exness_observation import NOW, bar, history
from scripts import exness_mtf_replay as replay


def frames_for(candles):
    hours, higher = history()
    frames = {}
    for frame, rows in (("15m", candles), ("1h", hours), ("4h", higher)):
        frames[frame] = []
        for original in rows:
            for side in ("bid", "ask"):
                spread = 0.2 if side == "ask" else 0
                candle = bar(original.time, opening=original.open + spread,
                             high=original.high + spread, low=original.low + spread,
                             close=original.close + spread)
                candle.price_type = side
                candle.high = max(candle.high, candle.open, candle.close)
                candle.low = min(candle.low, candle.open, candle.close)
                frames[frame].append(candle)
    return frames


@pytest.fixture
def confirmations(monkeypatch):
    def observe(candle, hours, higher, previous):
        return {"status": "confirmed", "side": "long", "stop": 98,
                "candle_time": candle.time, "reason": "test"}
    monkeypatch.setattr(replay, "observe_forex", observe)


def test_one_position_next_open_compounding_and_costs(confirmations):
    candles = [bar(NOW, opening=100, high=101, low=99, close=100),
               bar(NOW + 900, opening=100, high=101, low=99, close=100),
               bar(NOW + 1800, opening=100, high=105, low=99, close=104),
               bar(NOW + 2700, opening=100, high=101, low=99, close=100)]
    report = replay.portfolio(frames_for(candles), "breakout_retest", slippage_bps=0,
                              commission_per_unit=0.5, now=NOW + 3600)
    first = report["samples"][0]
    assert first["entry_time"] == NOW + 900
    assert first["entry"] == pytest.approx(100.2)
    assert first["target"] == pytest.approx(104.6)
    assert first["units"] == pytest.approx(100 / 2.2)
    assert first["pnl"] == pytest.approx((4.4 - 0.5) * 100 / 2.2)
    assert report["samples"][1]["reason"] == "position_already_active"
    assert report["samples"][2]["allocated_risk"] == pytest.approx(first["balance_after"] * 0.01)
    assert report["samples"][2]["status"] == "open"
    assert report["status"] == "open" and report["equity_verified"] is False
    assert report["trades"] == 1


def test_entered_gap_excludes_trade_and_wallet_continues(confirmations):
    candles = [bar(NOW, opening=100, high=101, low=99, close=100),
               bar(NOW + 900, opening=100, high=101, low=99, close=100),
               bar(NOW + 2700, opening=100, high=101, low=99, close=100),
               bar(NOW + 3600, opening=100, high=101, low=99, close=100)]
    report = replay.portfolio(frames_for(candles), "breakout_retest", now=NOW + 4500)
    assert report["samples"][0]["status"] == "excluded"
    assert report["blocked_at"] is None and report["excluded_trades"] >= 1
    assert report["final_balance"] == 10000 and report["trades"] == 0
    assert "unresolved_gap" not in report["rejection_reasons"]

def test_pending_gap_has_no_fill_and_future_candles_never_enter(confirmations):
    candles = [bar(NOW, opening=100, high=101, low=99, close=100),
               bar(NOW + 1800, opening=100, high=101, low=99, close=100)]
    frames = frames_for(candles)
    prefix = replay.portfolio(frames, "breakout_retest", now=NOW + 900)
    assert prefix["signals"] == 1
    assert "entry" not in prefix["samples"][0]
    report = replay.portfolio(frames, "breakout_retest", now=NOW + 2700)
    assert report["samples"][0]["status"] == "excluded"
    assert "entry" not in report["samples"][0]
    assert report["status"] == "open" and report["accepted"] == 2


@pytest.mark.parametrize("costs", [{"slippage_bps": float("nan")}, {"commission_per_unit": -1},
                                  {"slippage_bps": 10000}, {"balance": 0}, {"risk": float("inf")}])
def test_portfolio_refuses_bad_parameters(costs):
    with pytest.raises(ValueError):
        replay.portfolio({}, "breakout_retest", **costs)


def test_portfolio_refuses_unpaired_and_duplicate_candles(confirmations):
    frames = frames_for([bar(NOW, opening=100, high=101, low=99, close=100)])
    frames["15m"].append(frames["15m"][0])
    with pytest.raises(ValueError, match="Duplicate"):
        replay.portfolio(frames, "breakout_retest", now=NOW + 900)
    frames["15m"] = frames["15m"][:1]
    with pytest.raises(ValueError, match="history"):
        replay.portfolio(frames, "breakout_retest", now=NOW + 900)

def ts(text):
    from datetime import datetime, timezone
    return int(datetime.fromisoformat(text).replace(tzinfo=timezone.utc).timestamp())

def test_regular_session_gaps_only():
    from backend.app.exness_sessions import regular_session_gap as ok
    assert ok(ts("2026-10-07T20:45"), ts("2026-10-07T22:00"))      # summer daily break
    assert ok(ts("2026-01-07T21:45"), ts("2026-01-07T23:00"))      # winter daily break
    assert ok(ts("2026-10-09T20:45"), ts("2026-10-11T22:00"))      # weekend
    assert not ok(ts("2026-09-07T18:15"), ts("2026-09-07T22:00"))
    assert not ok(ts("2026-10-07T20:45"), ts("2026-10-07T22:15"))  # open candles missing
    assert not ok(ts("2026-10-07T10:00"), ts("2026-10-07T11:00"))  # mid-session hole

def test_session_close_flattens_and_blocks_late_entries():
    from backend.app.forex_paper import _step_trade
    last = ts("2026-10-07T20:45")  # next bucket 21:00 UTC is closed (summer break)
    base = {"side": "long", "stop": 98, "target": 110, "slippage_bps": 0, "commission_per_unit": 0}
    bars = bar(last - 900, opening=100, high=101, low=99, close=100.5)
    trade = {**base, "status": "open", "last_candle": last - 1800, "units": 1, "risk_per_unit": 2,
             "entry": 100, "entry_price_raw": 100, "entry_slippage_usd": 0, "bars": 0}
    _step_trade(trade, bars, bars)
    assert trade["status"] == "resolved" and trade["outcome"] == "session_close"
    assert trade["exit_time"] == last
    assert trade["exit"] == 100.5
    late = {**base, "status": "pending", "last_candle": ts("2026-10-07T20:00"),
            "allocated_risk": 100, "capital": 10000}
    entry_bar = bar(ts("2026-10-07T20:15"), opening=100, high=101, low=99, close=100)
    _step_trade(late, entry_bar, entry_bar)
    assert late["status"] == "skipped" and late["reason"] == "session_close_near"
