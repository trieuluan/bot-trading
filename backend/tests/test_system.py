import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.app.db import BotState, Session, Signal
from backend.app.main import app
from backend.app.market import Candle, DemoAdapter
from backend.app.strategy import Setup, breakout, retest
from bot.worker import tick


def test_breakout_uses_previous_bars_and_risk():
    history = [Candle(i, 95, 100, 90, 96, 10) for i in range(20)]
    current = Candle(20, 96, 110, 95, 105, 10)
    setup = breakout(history + [current])
    assert setup.entry == 105 and setup.stop == 90 and setup.target == 135
    # Current high is not accidentally included in breakout resistance.
    assert breakout(history + [Candle(20, 96, 110, 95, 99, 10)]) is None
    assert breakout(history) is None


def test_default_stopped_heartbeat_and_persistent_control():
    with TestClient(app) as client:
        assert client.get("/api/health").status_code == 200
        before = client.get("/api/dashboard").json()
        assert not before["bot"]["enabled"]
        tick()
        assert client.get("/api/dashboard").json()["bot"]["last_bar"] == 60
        assert client.post("/api/bot/control", json={"enabled": True}).status_code == 200
        tick()
        after = client.get("/api/dashboard").json()
        assert after["bot"]["enabled"] and after["bot"]["online"]
        assert after["bot"]["last_bar"] == 61
        client.post("/api/bot/control", json={"enabled": False})
        tick()
        assert client.get("/api/dashboard").json()["bot"]["last_bar"] == 61
        # New session observes persisted stopped state.
        with Session() as session:
            assert not session.get(BotState, 1).enabled


def test_replay_records_unique_signals_and_stops_at_end():
    with Session.begin() as session:
        session.get(BotState, 1).enabled = True
    for _ in range(100):
        tick()
    with Session() as session:
        signals = session.scalars(select(Signal)).all()
        assert len(signals) > 0
        assert len({s.candle_time for s in signals}) == len(signals)
        assert all(s.stop < s.entry < s.target for s in signals)
    with Session.begin() as session:
        session.get(BotState, 1).last_bar = 2000
    tick()
    with Session() as session:
        assert not session.get(BotState, 1).enabled


def test_live_mode_is_rejected(monkeypatch):
    from backend.app import settings

    monkeypatch.setattr(settings, "TRADING_MODE", "live")
    with pytest.raises(RuntimeError, match="Live trading is disabled"):
        settings.validate_mode()


def test_invalid_control_and_stable_replay():
    with TestClient(app) as client:
        assert (
            client.post("/api/bot/control", json={"enabled": True, "mode": "live"}).status_code
            == 422
        )
    assert DemoAdapter().candles(end=80)[-1] == DemoAdapter().candles(end=81)[-2]


def test_retest_waits_for_retest_and_confirmation():
    h = [Candle(i, 95, 100, 90, 96, 10) for i in range(20)]
    brk = Candle(20, 96, 110, 95, 105, 10)
    ret = Candle(21, 105, 106, 99, 101, 10)  # touches 100, holds, bearish
    ok = Candle(22, 101, 108, 100, 107, 10)
    assert retest(h + [brk]) is None and retest(h + [brk, ret]) is None
    s = retest(h + [brk, ret, ok])
    assert s.side == "long" and s.entry == 107 and s.stop == 99 and s.target == 123
    assert retest(h + [brk, ret, Candle(22, 101, 102, 97, 98, 10)]) is None  # lost zone
    assert retest(h + [brk, ret, ok, Candle(23, 107, 109, 106, 108, 10)]) is None  # no repeat


def test_retest_short_expiry_and_no_lookahead():
    h = [Candle(i, 95, 100, 90, 96, 10) for i in range(20)]
    brk = Candle(20, 94, 95, 80, 85, 10)  # closes below 90
    ret = Candle(21, 85, 91, 84, 89, 10)  # touches 90, closes back below
    ok = Candle(22, 89, 89, 80, 82, 10)
    s = retest(h + [brk, ret, ok], allow_short=True)
    assert s.side == "short" and s.entry == 82 and s.stop == 91 and s.target == 64
    assert retest(h + [brk, ret, ok]) is None  # long-only ignores shorts
    # Later candles must not change an earlier verdict (no lookahead).
    assert retest((h + [brk, ret, ok, Candle(23, 82, 120, 70, 110, 10)])[:23], allow_short=True)
    # Retest after expiry is ignored.
    up = [Candle(20, 96, 110, 95, 105, 10)] + [
        Candle(21 + i, 105, 108, 102, 106, 10) for i in range(10)
    ]
    late = [Candle(31, 106, 106, 99, 101, 10), Candle(32, 101, 108, 100, 107, 10)]
    assert retest(h + up + late) is None


def test_walkforward_criteria():
    from scripts.walkforward import evaluate

    def win(pnl):
        return {
            "trades": [{"net_pnl": pnl}] * 50,
            "net_pnl": pnl,
            "max_drawdown_percent": 3,
            "return_percent": pnl,
        }

    assert evaluate([win(5), win(5), win(-1)])["pass"]
    assert not evaluate([win(1), win(-1)])["pass"]  # PF 1.0
    assert not evaluate([win(5)])["pass"]  # < 100 trades


def test_trend_filter_blocks_counter_trend_setups():
    from scripts.walkforward import with_trend

    up = [Candle(i, 100 + i, 101 + i, 99 + i, 100 + i, 1) for i in range(30)]

    def flat(*_a, **_k):
        return Setup("long", 1, 0, 2, "x")

    assert with_trend(flat, 10)(up) is not None  # close above rising EMA
    down = [Candle(i, 200 - i, 201 - i, 199 - i, 200 - i, 1) for i in range(30)]
    assert with_trend(flat, 10)(down) is None  # long against falling EMA


def test_combined_signal_uses_fixed_priority_and_no_duplicate_order():
    from scripts.walkforward import first_of

    long_setup = Setup("long", 100, 90, 120, "first")
    short_setup = Setup("short", 100, 110, 80, "second")

    def first(candles, allow_short=False):
        return long_setup if candles else None

    def second(candles, allow_short=False):
        return short_setup if allow_short else None

    assert first_of(first, second)([1], allow_short=True) is long_setup
    assert first_of(first, second)([], allow_short=True) is short_setup
    assert first_of(first, second)([]) is None


def test_rotation_chooses_previous_window_not_current_or_future():
    from scripts.walkforward import rotate_previous

    def window(gain):
        return {"trades": [{"net_pnl": gain}, {"net_pnl": -1}]}

    results = {
        "breakout": [window(3), window(-100), window(1000)],
        "retest": [window(2), window(5), window(-100)],
        "breakout+ema200": [window(1), window(2), window(2000)],
    }
    picks, rows = rotate_previous(results)
    assert picks == ["breakout", "retest"]
    assert rows[0] is results["breakout"][1]
    assert rows[1] is results["retest"][2]
    results["breakout+ema200"][2] = window(1000000)
    assert rotate_previous(results)[0] == picks


def test_mtf_needs_trend_breakout_pullback_and_confirmation():
    from backend.app.strategy import aggregate, mtf_retest

    def bar(i, o, h, low, c):
        return Candle(i * 900, o, h, low, c, 1)

    base = [
        bar(i, 100 + i / 100, 100.5 + i / 100, 99.5 + i / 100, 100 + i / 100) for i in range(1200)
    ]
    brk = [bar(1200 + i, 119, 121, 119, 120) for i in range(4)]  # 1h close above 112.49
    touch = bar(1204, 120, 120, 112, 113)  # pulls back into zone, holds above
    confirm = bar(1205, 113, 118, 112.6, 117)
    s = mtf_retest(base + brk + [touch, confirm])
    assert (s.side, s.entry, s.stop, s.target) == ("long", 117, 112, 127)
    assert mtf_retest(base + brk + [touch]) is None  # no confirmation yet
    assert mtf_retest(base + brk) is None  # breakout alone
    assert mtf_retest(base + brk + [touch, confirm, bar(1206, 117, 119, 116, 118)]) is None
    drop = [bar(1200 + i, 80, 81, 79, 80) for i in range(4)]
    assert mtf_retest(base + drop, allow_short=True) is None  # no short against 4h uptrend
    assert [c.time for c in aggregate(base[:7], 3600)] == [0]  # partial hour dropped

    mirrored = [
        Candle(c.time, 200 - c.open, 200 - c.low, 200 - c.high, 200 - c.close, c.volume)
        for c in base + brk + [touch, confirm]
    ]
    short = mtf_retest(mirrored, allow_short=True)
    assert (short.side, short.entry, short.stop, short.target) == ("short", 83, 88, 73)
    assert mtf_retest(mirrored) is None
    invalidated = base + brk + [bar(1204, 120, 120, 110, 111), touch, confirm]
    assert mtf_retest(invalidated) is None
    delayed = base + brk + [bar(1204 + index, 120, 121, 119, 120) for index in range(48)]
    delayed += [bar(1252, 120, 120, 112, 113), bar(1253, 113, 118, 112.6, 117)]
    assert mtf_retest(delayed) is None


def test_higher_timeframe_excludes_unclosed_and_incomplete_buckets():
    from backend.app.strategy import aggregate

    candles = [Candle(index * 900, 100, 101, 99, 100, 1) for index in range(20)]
    assert aggregate(candles[:15], 14400) == []
    expected = aggregate(candles[:16], 14400)
    assert len(expected) == 1
    candles[-1] = Candle(candles[-1].time, 100, 10000, 1, 5000, 1)
    assert aggregate(candles, 14400) == expected
    assert aggregate(candles[1:16], 14400) == []
    assert aggregate([candles[0], candles[0], candles[2], candles[3]], 3600) == []
    with pytest.raises(ValueError):
        aggregate(candles, 900)
