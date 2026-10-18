from datetime import datetime, timezone
from backend.app.macro_calendar import is_macro_safe
from backend.app.futures_rule_tree import matches, validate_rule
from backend.app.market import Candle
from backend.app.futures_context import load_signal_context
from backend.app.futures_symbols import SYMBOLS, symbol_scope
from backend.app.db import FuturesOpenInterest, Session
from backend.app.feed import save_candles

def candle(t, o, h, l, c, v=1.0):
    return Candle(t, o, h, l, c, v)

def test_macro_calendar_blocks_release_window():
    # 2026-10-14 08:30 EDT = 12:30 UTC = 1791981000
    dt = datetime(2026, 10, 14, 12, 30, tzinfo=timezone.utc)
    ts = int(dt.timestamp())
    assert not is_macro_safe(ts)
    assert not is_macro_safe(ts - 2 * 3600)  # 2 hours before
    assert not is_macro_safe(ts + 2 * 3600)  # 2 hours after
    assert is_macro_safe(ts - 5 * 3600)      # 5 hours before: safe
    assert is_macro_safe(ts + 5 * 3600)      # 5 hours after: safe

def test_macro_safe_rule_predicate():
    rule = {"feature": "macro_safe", "frame": "H1"}
    assert validate_rule(rule) == 1
    # Inside CPI window
    ts = int(datetime(2026, 10, 14, 12, 30, tzinfo=timezone.utc).timestamp())
    candles_danger = [candle(ts - (20 - i) * 900, 100, 101, 99, 100) for i in range(21)]
    assert not matches(rule, candles_danger, 1, 20)
    # Outside window
    candles_safe = [candle(ts - 5 * 3600 - (20 - i) * 900, 100, 101, 99, 100) for i in range(21)]
    assert matches(rule, candles_safe, 1, 20)

def test_relative_strength_rule_predicate():
    rule = {"feature": "relative_strength", "frame": "H1"}
    assert validate_rule(rule) == 1
    # Altcoin rises over 100 M15 bars (= 25 H1 bars)
    alt = [candle(i * 900, 100, 100 + i * 0.1, 99, 100 + i * 0.1) for i in range(100)]
    # BTC stays flat
    btc = [candle(i * 900, 60000, 60100, 59900, 60000) for i in range(100)]
    assert matches(rule, alt, 1, 20, context={"btc_candles": btc})
    assert not matches(rule, alt, -1, 20, context={"btc_candles": btc})

def test_funding_aligned_rule_predicate():
    rule = {"feature": "funding_aligned", "frame": "H1"}
    assert validate_rule(rule) == 1
    now = 1700000000
    candles = [candle(now - (20 - i) * 900, 100, 101, 99, 100) for i in range(21)]
    # Negative funding: short pays long -> long is favored
    funding = [{"time": now, "rate": -0.0002}]
    assert matches(rule, candles, 1, 20, context={"funding": funding})
    assert not matches(rule, candles, -1, 20, context={"funding": funding})
    # Positive funding: long pays short -> short is favored
    funding = [{"time": now, "rate": 0.0005}]
    assert not matches(rule, candles, 1, 20, context={"funding": funding})
    assert matches(rule, candles, -1, 20, context={"funding": funding})


def test_oi_surge_rule_predicate():
    rule = {"feature": "oi_surge", "frame": "H1"}
    assert validate_rule(rule) == 1
    end = 1700002800  # hour-aligned close of the last M15 bar
    bull = [candle(end - (21 - i) * 900, 100, 105, 95, 102) for i in range(21)]
    expanded = [(end - 3600, 1000.0), (end - 300, 1020.0)]
    assert matches(rule, bull, 1, 20, context={"oi_history": expanded})
    assert not matches(rule, bull, -1, 20, context={"oi_history": expanded})
    assert not matches(rule, bull, 1, 20, context={"oi_history": [(end - 3600, 1000.0), (end - 300, 1005.0)]})
    # missing or stale OI never fakes a signal
    assert not matches(rule, bull, 1, 20, context={})
    assert not matches(rule, bull, 1, 20, context={"oi_history": []})
    assert not matches(rule, bull, 1, 20, context={"oi_history": [(end - 9000, 1000.0), (end - 7200, 1050.0)]})


def test_macro_revised_release_dates_and_unknown_coverage():
    for release in ("2025-11-20 13:30", "2025-12-18 13:30", "2026-01-13 13:30",
                    "2026-02-11 13:30", "2026-02-13 13:30", "2026-09-11 12:30"):
        timestamp = int(datetime.strptime(release, "%Y-%m-%d %H:%M")
                        .replace(tzinfo=timezone.utc).timestamp())
        assert not is_macro_safe(timestamp)
    assert not is_macro_safe(0)
    assert not is_macro_safe(int(datetime(2027, 2, 1, tzinfo=timezone.utc).timestamp()))


def test_relative_strength_ignores_future_and_rejects_misaligned_benchmark():
    rule = {"feature": "relative_strength", "frame": "H1"}
    alt = [candle(index * 900, 100, 111, 99, 100 + index * 0.1) for index in range(100)]
    btc = [candle(index * 900, 100, 101, 99, 100) for index in range(100)]
    future = [candle(100 * 900, 100, 1000, 99, 1000)]
    assert matches(rule, alt, 1, 20, context={"benchmarks": {"BTC_USDT": btc + future}})
    assert not matches(rule, alt, 1, 20, context={"benchmarks": {"BTC_USDT": btc[:-4]}})
    assert matches(rule, btc, -1, 20, context={"benchmarks": {"ETH_USDT": alt}})


def test_btc_context_loads_nine_peers_and_only_observed_oi():
    rows = [candle(900, 100, 101, 99, 100)]
    with Session.begin() as session:
        for peer in SYMBOLS[1:]:
            with symbol_scope(peer):
                save_candles(session, rows)
        session.add_all([FuturesOpenInterest(symbol="BTC_USDT", time=1000, hold_vol=100),
                         FuturesOpenInterest(symbol="BTC_USDT", time=1801, hold_vol=200)])
    with symbol_scope("BTC_USDT"), Session() as session:
        context = load_signal_context(session, 900, 1800)
    assert set(context["benchmarks"]) == set(SYMBOLS[1:])
    assert context["oi_history"] == [(1000, 100)]


def test_replay_aggregation_cache_does_not_mix_pairs_or_interior_bars():
    from scripts.futures_replay_340d import _memo, memo_aggregate

    first = [candle(index * 900, 100, 101, 99, 100) for index in range(8)]
    second = list(first)
    second[1] = candle(900, 100, 200, 99, 100)
    _memo.clear()
    assert memo_aggregate(first, 3600)[0].high == 101
    assert memo_aggregate(second, 3600)[0].high == 200
    _memo.clear()
