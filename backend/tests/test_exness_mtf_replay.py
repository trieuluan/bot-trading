import pytest

from backend.app.db import ForexObservation, Session
from backend.app.exness_feed import FRAMES, sync_page
from backend.app.exness_observation import load_frames
from backend.tests.test_exness_feed import FakeApi
from backend.tests.test_exness_observation import bar, history, NOW
from scripts.exness_mtf_replay import advance, run_replay

def sample(side="long", stop=99):
    return {"status": "pending", "side": side, "stop": stop, "last_candle": 0}

def test_long_ask_entry_bid_exit_spread_once():
    trade = sample()
    advance(trade, bar(900, opening=100, high=104, low=100, close=103), bar(900, opening=101))
    assert trade["entry"] == 101
    assert trade["target"] == 105
    assert trade["status"] == "open"
    advance(trade, bar(1800, opening=103, high=105, low=102, close=104), bar(1800), commission_per_unit=0.5)
    assert trade["exit"] == 105
    assert trade["net_r_ex_swap"] == 1.75

def test_short_bid_entry_ask_stop_and_ambiguous_stop_first():
    trade = sample("short", stop=102)
    advance(trade, bar(900, opening=100, high=101, low=95, close=96), bar(900, opening=101, high=102, low=96, close=97))
    assert trade["entry"] == 100
    assert trade["outcome"] == "stop"
    assert trade["ambiguous_bar"] is True
    assert trade["net_r_ex_swap"] == -1

def test_open_gap_slippage_and_no_fill_across_unknown_gap():
    trade = sample()
    advance(trade, bar(900, opening=100, high=101, low=100), bar(900, opening=100), slippage_bps=1)
    advance(trade, bar(1800, opening=98, high=100, low=97), bar(1800), slippage_bps=1)
    assert trade["outcome"] == "stop_gap"
    assert trade["exit"] < 98
    missing = sample()
    advance(missing, bar(1800), bar(1800))
    assert missing["status"] == "censored"
    assert "entry" not in missing

def test_invalid_next_open_and_timeout():
    invalid = sample(stop=102)
    advance(invalid, bar(900), bar(900))
    assert invalid["status"] == "invalid"
    trade = sample()
    advance(trade, bar(900, opening=100, high=101, low=100, close=100.5), bar(900), horizon_bars=1)
    assert trade["outcome"] == "timeout"
    assert trade["net_r_ex_swap"] == 0.5

def test_replay_native_frames_causal_confirm_next_open_and_no_db_writes():
    hours, higher = history()
    candles = [bar(NOW - 900), bar(NOW, opening=103, high=104, low=100, close=102),
               bar(NOW + 900, opening=102, high=105, low=101, close=104),
               bar(NOW + 1800, opening=104, high=113, low=103, close=112)]
    frames = {}
    for frame, rows in (("15m", candles), ("1h", hours), ("4h", higher)):
        frames[frame] = []
        for row in rows:
            for side in ("bid", "ask"):
                copied = bar(row.time, **{field if field != "open" else "opening": getattr(row, field) + (0.25 if side == "ask" else 0) for field in ("open", "high", "low", "close")})
                copied.price_type = side
                frames[frame].append(copied)
    for rows in frames.values():
        for row in rows:
            row.high = max(row.high, row.open, row.close)
            row.low = min(row.low, row.open, row.close)
    report = run_replay(frames, now=NOW + 2700)
    assert report["summary"]["confirmations"] == 1
    assert report["samples"][0]["entry_time"] == NOW + 1800
    assert report["samples"][0]["entry"] == 104.25
    assert report["samples"][0]["net_r_ex_swap"] == 2
    prefix = run_replay(frames, now=NOW + 1800)
    assert prefix["samples"][0]["status"] == "censored"
    assert prefix["samples"][0]["setup"] == report["samples"][0]["setup"]
    with Session() as session:
        assert session.query(ForexObservation).count() == 0

def test_replay_refuses_invalid_data_costs_and_insufficient_warmup():
    for frame in FRAMES:
        sync_page(FakeApi(), frame, days=1, now=NOW)
    with Session() as session:
        frames = load_frames(session, NOW)
        with pytest.raises(ValueError, match="warmup"):
            run_replay(frames, NOW)
        with pytest.raises(ValueError, match="Costs"):
            run_replay(frames, NOW, slippage_bps=float("nan"))
        frames["15m"].pop()
        with pytest.raises(ValueError, match="history"):
            run_replay(frames, NOW)
