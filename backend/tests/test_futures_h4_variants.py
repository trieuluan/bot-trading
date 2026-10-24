from backend.app.strategy import Candle, aggregate
from scripts.futures_h4_variants import aligned_share, allowed, build_states, h4_state, summarize


def state(close=105, ema=100, ema_old=99, atr=4, adx=(30, 5), highs=(110, 112), lows=(95, 97)):
    return {
        "close": close,
        "ema": ema,
        "ema_old": ema_old,
        "atr": atr,
        "adx": adx,
        "high_piv": list(highs),
        "low_piv": list(lows),
    }


def test_gates_follow_declared_rules_for_both_sides():
    up = state()
    shares = aligned_share([up, up, state(close=95)])
    assert allowed("baseline", -1, None)
    assert not allowed("C_dist", 1, None)
    assert allowed("C_dist", 1, up) and not allowed("C_dist", 1, state(close=101))
    assert allowed("I_market", 1, up, up, shares) and not allowed("I_market", -1, up, up, shares)
    assert allowed("B_structure", 1, up) and not allowed("B_structure", -1, up)
    assert allowed("A_slope", 1, up) and not allowed("A_slope", 1, state(ema_old=101))
    assert allowed("G_adx", 1, up) and not allowed("G_adx", 1, state(adx=(20, 5)))
    assert not allowed("G_adx", -1, up)
    assert allowed("K_support", 1, state(close=99)) and not allowed(
        "K_support", 1, state(close=105)
    )


def test_summary_counts_overlapping_candidates_as_one_event():
    rows = [
        ("BNB_USDT", "baseline", "train", "a", 900, "long", -1.0, "stop"),
        ("BNB_USDT", "baseline", "train", "b", 900, "long", -2.0, "stop"),
        ("BTC_USDT", "baseline", "train", "a", 900, "short", 2.0, "target"),
    ]
    result = summarize(rows)
    assert result["trades"] == 3 and result["events"] == 2
    assert result["event_mean_r"] == 0.25


def test_h4_snapshot_uses_closed_contiguous_bars_without_future_leakage():
    candles = [
        Candle(
            index * 900,
            100 + index * 0.01,
            101 + index * 0.01,
            99 + index * 0.01,
            100 + index * 0.01,
            10,
        )
        for index in range(1650)
    ]
    stamp = candles[1610].time
    before = build_states({"BNB_USDT": candles[:1611]})["BNB_USDT"][stamp]
    candles[-1] = Candle(candles[-1].time, 10000, 10001, 9999, 10000, 10)
    after = build_states({"BNB_USDT": candles})["BNB_USDT"][stamp]
    assert before == after == h4_state(aggregate(candles[11:1611], 14400))
    assert before["close"] == candles[1599].close
