import pytest
from backend.app.forex_learner import bucket, choose, context
from backend.app.forex_strategies import STRATEGIES

def bar(time=1000, close=100.0, open=100.0, high=101.0, low=99.0):
    from types import SimpleNamespace
    return SimpleNamespace(time=time, close=close, open=open, high=high, low=low)

def test_learner_feature_extraction_without_future():
    candle = bar(900, close=100, open=99, high=101, low=98)
    ask = bar(900, close=100.5)
    hours = [bar(i * 3600, close=100) for i in range(50)]
    ctx = context(candle, hours, ask)
    assert ctx["spread"] == 0.5
    assert ctx["body"] == 1.0
    assert ctx["range"] == 3.0
    assert ctx["trend"] in ("up", "down", "unknown")

def test_learner_abstains_when_insufficient_samples():
    signal = {"strategy": "trend_pullback", "side": "long"}
    selected, scored = choose([signal], {"trend": "up", "utc_session": 1}, model={})
    assert selected is None
    assert scored[0]["count"] == 0

def test_learner_selects_positive_lower_bound_when_sample_size_met():
    signal = {"strategy": "trend_pullback", "side": "long"}
    features = {"trend": "up", "utc_session": 1}
    key = bucket(signal, features)
    model = {key: {"count": 40, "mean_r": 0.8, "lower_r": 0.3}}
    selected, scored = choose([signal], features, model)
    assert selected == signal
    assert scored[0]["lower_r"] == 0.3

def test_rules_include_new_hypotheses():
    for name in ("donchian_breakout", "liquidity_sweep", "squeeze_breakout"):
        assert name in STRATEGIES
