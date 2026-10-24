import pytest

from backend.app.futures_rule_tree import (
    adx_state,
    matches,
    nodes,
    prior_day_range,
    validate_htf_rule,
    validate_rule,
)
from backend.app.futures_strategies import RULE_SEEDS, validate_genome
from backend.app.market import Candle


def c(t, o, h, low, cl, v=100.0):
    return Candle(t, o, h, low, cl, v)

def test_fib_retrace_predicate():
    rule = {"feature": "fib_retrace", "frame": "H1"}
    assert validate_rule(rule) == 1
    # Swing low at index 6 (t=6h, low=50), Swing high at index 14 (t=14h, high=100)
    # Range = 50. Fib 50% = 75, Fib 61.8% = 69.1. Last bar closes at 72 (between 69.1 and 75)
    bars = []
    for i in range(18):
        if i == 6:
            bars.append(c(i * 3600, 60, 62, 50, 61))
        elif i == 14:
            bars.append(c(i * 3600, 95, 100, 93, 98))
        elif i < 6:
            bars.append(c(i * 3600, 70 - i, 72 - i, 68 - i, 69 - i))
        elif i < 14:
            bars.append(c(i * 3600, 60 + (i - 6) * 4.5, 62 + (i - 6) * 4.5, 59 + (i - 6) * 4.5, 61 + (i - 6) * 4.5))
        elif i < 17:
            bars.append(c(i * 3600, 90 - (i - 14) * 5, 92 - (i - 14) * 5, 85 - (i - 14) * 5, 86 - (i - 14) * 5))
        else:
            bars.append(c(i * 3600, 75, 76, 71, 72))
    m15 = [c(b.time + s * 900, b.open, b.high, b.low, b.close, b.volume / 4) for b in bars for s in range(4)]
    assert matches(rule, m15, 1, 20)
    assert not matches(rule, m15, -1, 20)

def test_fvg_predicate():
    rule = {"feature": "fvg", "frame": "H1"}
    assert validate_rule(rule) == 1
    b1 = c(0, 95, 100, 94, 98)
    b2 = c(3600, 102, 110, 102, 109)
    b3 = c(7200, 106, 108, 104, 107)
    b4 = c(10800, 101, 105, 101, 103)
    m15 = [c(b.time + s * 900, b.open, b.high, b.low, b.close, b.volume / 4) for b in (b1, b2, b3, b4) for s in range(4)]
    assert matches(rule, m15, 1, 20)
    assert not matches(rule, m15, -1, 20)

def test_rsi_divergence_predicate():
    rule = {"feature": "rsi_divergence", "frame": "H1"}
    assert validate_rule(rule) == 1
    # 35 H1 bars with higher low prices but dropping RSI (bearish div) or lower low prices with rising RSI (bullish div)
    # Create bullish divergence:
    # Pivot 1 at index 18 (low=70, rsi lower)
    # Pivot 2 at index 28 (low=60, rsi higher)
    bars = [c(i * 3600, 100 - i * 0.8, 102 - i * 0.8, 98 - i * 0.8, 99 - i * 0.8) for i in range(18)]
    bars.append(c(18 * 3600, 75, 76, 70, 72))  # Pivot 1 low = 70
    # Bounce up to 85
    for i in range(19, 26):
        bars.append(c(i * 3600, 72 + (i - 18) * 2, 75 + (i - 18) * 2, 71 + (i - 18) * 2, 74 + (i - 18) * 2))
    # Slow drift down with smaller losses (RSI stays higher), low dips to 65
    for i in range(26, 29):
        bars.append(c(i * 3600, 78 - (i - 25) * 3, 79 - (i - 25) * 3, 76 - (i - 25) * 3, 77 - (i - 25) * 3))
    bars.append(c(29 * 3600, 68, 69, 65, 67))  # Pivot 2 low = 65 < 70
    # Confirm pivot with 2 bars up and last bar bullish
    bars.append(c(30 * 3600, 67, 72, 66, 71))
    bars.append(c(31 * 3600, 71, 75, 70, 74))
    closes = [100 - i for i in range(19)] + [84, 86, 88, 90, 92] + [91, 89, 87, 85, 83, 81] + [83, 85]
    synthetic = [c(i * 3600, close - 0.5, close + 1, close - 1, close, 100)
                 for i, close in enumerate(closes)]
    synthetic_m15 = [c(b.time + s * 900, b.open, b.high, b.low, b.close, b.volume / 4)
                     for b in synthetic for s in range(4)]
    assert matches(rule, synthetic_m15, 1, 20)
    assert not matches(rule, synthetic_m15, -1, 20)

def test_bos_predicate():
    rule = {"feature": "bos", "frame": "H1"}
    assert validate_rule(rule) == 1
    bars = []
    for i in range(15):
        if i == 5:
            bars.append(c(i * 3600, 110, 120, 108, 115))  # Pivot high at 120
        elif i < 14:
            bars.append(c(i * 3600, 100, 105, 95, 100))
        else:
            bars.append(c(i * 3600, 118, 125, 117, 122))  # Breakout close > 120
    m15 = [c(b.time + s * 900, b.open, b.high, b.low, b.close, b.volume / 4) for b in bars for s in range(4)]
    assert matches(rule, m15, 1, 20)
    assert not matches(rule, m15, -1, 20)

def test_session_vwap_predicate():
    rule = {"feature": "session_vwap", "frame": "H1"}
    assert validate_rule(rule) == 1
    day = 86400
    b1 = c(day, 100, 105, 95, 98, 1000.0)      # Close below VWAP
    b2 = c(day + 3600, 98, 103, 97, 102, 1000.0) # Crosses and closes above VWAP
    m15 = [c(b.time + s * 900, b.open, b.high, b.low, b.close, b.volume / 4) for b in (b1, b2) for s in range(4)]
    assert matches(rule, m15, 1, 20)
    assert not matches(rule, m15, -1, 20)

def test_all_signal_seeds_registered_and_valid():
    new_features = {"fib_retrace", "rsi_divergence", "fvg", "bos", "session_vwap", "pdh_pdl", "adx_regime"}
    found = {n["feature"] for s in RULE_SEEDS for _, n in nodes(s["rule"]) if n.get("feature") in new_features}
    assert found == new_features
    assert len(RULE_SEEDS) == 26

def test_pdh_pdl_predicate():
    rule = {"feature": "pdh_pdl", "frame": "H1"}
    day = 86400
    prior = [c(i * 3600, 100, 110 if i == 5 else 105, 95, 100) for i in range(24)]  # PDH = 110
    inside = c(day, 100, 108, 99, 105)
    broke = c(day + 3600, 105, 114, 104, 112)
    m15 = [c(b.time + s * 900, b.open, b.high, b.low, b.close, b.volume / 4) for b in (*prior, inside, broke) for s in range(4)]
    assert matches(rule, m15, 1, 20) and not matches(rule, m15, -1, 20)
    assert not matches(rule, m15[16:], 1, 20)  # incomplete prior day

def test_adx_regime_predicate():
    rule = {"feature": "adx_regime", "frame": "H1"}
    rise = [c(i * 3600, 100 + 2 * i, 102 + 2 * i, 99 + 2 * i, 101 + 2 * i) for i in range(60)]
    flat = [c(i * 3600, 100, 101, 99, 100 + (i % 2) * .1) for i in range(60)]
    def to15(bars):
        return [c(b.time + s * 900, b.open, b.high, b.low, b.close, b.volume / 4) for b in bars for s in range(4)]
    assert matches(rule, to15(rise), 1, 20) and not matches(rule, to15(rise), -1, 20)
    assert not matches(rule, to15(flat), 1, 20)

@pytest.mark.parametrize("feature", ("pdh_pdl", "adx_regime", "pdh_pdl_sweep"))
def test_daily_features_reject_m15(feature):
    with pytest.raises(ValueError, match="Invalid rule predicate"):
        validate_rule({"feature": feature, "frame": "M15"})
    for frame in ("H1", "H4"):
        assert validate_rule({"feature": feature, "frame": frame}) == 1

@pytest.mark.parametrize("seconds,frame", ((3600, "H1"), (14400, "H4")))
def test_pdh_pdl_closed_bars_and_previous_utc_day(seconds, frame):
    rule = {"feature": "pdh_pdl", "frame": frame}
    prior = [c(index * 900, 100, 110, 90, 100) for index in range(96)]
    for direction in (1, -1):
        close = 100 + direction * 15
        current = [c(86400 + index * 900, 100, max(110, close), min(90, close), close)
                   for index in range(seconds // 900)]
        candles = prior + current
        assert matches(rule, candles, direction, 20)
        assert not matches(rule, candles, -direction, 20)
        assert not matches(rule, candles[:-1], direction, 20)
        assert not matches(rule, candles[:30] + candles[31:], direction, 20)
        held = candles + [c(row.time + seconds, row.open, row.high, row.low, row.close)
                          for row in current]
        assert not matches(rule, held, direction, 20)
    assert prior_day_range([], seconds) is None

def test_adx_flat_warmup_then_trend_and_short_history():
    flat = [c(index * 3600, 100, 100, 100, 100) for index in range(42)]
    assert adx_state(flat) == (0.0, 0.0)
    assert adx_state(flat[:-1]) is None
    rise = [c((index + 42) * 3600, 100 + index, 102 + index, 100 + index, 101 + index)
            for index in range(60)]
    strength, spread = adx_state(flat + rise)
    assert strength == pytest.approx(100 * (1 - (13 / 14) ** 60))
    assert spread > 0

def test_new_seeds_are_generation_zero_and_htf_valid():
    from backend.app.futures_learner import new_state
    from backend.app.futures_strategies import genome_name

    state = new_state(1800000000)
    seeds = [seed for seed in RULE_SEEDS if any(
        node.get("feature") in ("pdh_pdl", "adx_regime", "pdh_pdl_sweep")
        for _, node in nodes(seed["rule"]))]
    assert len(seeds) == 3
    for seed in seeds:
        validate_genome(seed)
        validate_htf_rule(seed["rule"])
        candidate = state["candidates"][genome_name(seed)]
        assert candidate["generation"] == 0 and candidate["parents"] == []

def test_pdh_pdl_sweep_predicate():
    rule = {"feature": "pdh_pdl_sweep", "frame": "H1"}
    prior = [c(i * 3600, 100, 115 if i == 5 else 105, 85 if i == 10 else 95, 100)
             for i in range(24)]
    def to15(bars):
        return [c(bar.time + step * 900, bar.open, bar.high, bar.low, bar.close, bar.volume / 4)
                for bar in bars for step in range(4)]
    long = [*prior, c(86400, 90, 96, 82, 95)]
    short = [*prior, c(86400, 110, 120, 107, 108)]
    assert matches(rule, to15(long), 1, 20) and not matches(rule, to15(long), -1, 20)
    assert matches(rule, to15(short), -1, 20) and not matches(rule, to15(short), 1, 20)
    assert not matches(rule, to15([*prior, c(86400, 90, 91, 78, 80)]), 1, 20)
    assert not matches(rule, to15(long)[:-1], 1, 20)
    assert not matches(rule, to15(long)[4:], 1, 20)
    assert not matches(rule, to15([*prior, c(86400, 90, 96, 85, 95)]), 1, 20)
    assert not matches(rule, to15([*prior, c(86400, 80, 96, 78, 95)]), 1, 20)

def test_sweep_oi_seed_requires_known_fresh_oi_and_can_breed():
    import copy
    import random

    from backend.app.futures_rule_tree import HTF_CONTEXT_FEATURES
    from backend.app.futures_strategies import crossover, mutate

    seed = next(seed for seed in RULE_SEEDS if any(
        node.get("feature") == "pdh_pdl_sweep" for _, node in nodes(seed["rule"])))
    frozen = copy.deepcopy(seed)
    assert "pdh_pdl_sweep" in HTF_CONTEXT_FEATURES
    candles = [c(index * 900, 60, 61, 59, 60) for index in range(8 * 96)]
    candles += [c(index * 900, 100, 110, 90, 100) for index in range(8 * 96, 9 * 96)]
    candles += [c(index * 900, 95, 102, 85, 100) for index in range(9 * 96, 9 * 96 + 4)]
    now = candles[-1].time + 900
    context = {"htf": True, "oi_history": [(now - 3600, 1000), (now, 1020)]}
    setup_and_trigger = {"op": "all", "children": seed["rule"]["children"][1:]}
    assert matches(setup_and_trigger, candles, 1, 20, context=context)
    assert not matches(setup_and_trigger, candles, -1, 20, context=context)
    for oi in ([], [(now - 3600, 1000), (now, 1000)],
               [(now - 3600, 1000), (now + 1, 1020)],
               [(now - 3600, 1000), (now - 901, 1020)]):
        assert not matches(setup_and_trigger, candles, 1, 20,
                           context={"htf": True, "oi_history": oi})
    child = crossover(RULE_SEEDS[0], seed)
    assert child["rule"]["children"][1] == seed["rule"]["children"][1]
    for variant in [child, *(mutate(seed, random.Random(index)) for index in range(32))]:
        validate_genome(variant)
        validate_htf_rule(variant["rule"])
    assert seed == frozen
