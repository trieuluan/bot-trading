import pytest
import random
from backend.app.forex_learner import SEEDS, bucket, choose, context, evolve, genome_name, genome_signal
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
    selected, mode, scored = choose([signal], {"trend": "up", "utc_session": 1}, model={})
    assert selected is None and mode == "abstain"
    assert scored[0]["count"] == 0

def test_learner_selects_positive_lower_bound_when_sample_size_met():
    signal = {"strategy": "trend_pullback", "side": "long"}
    features = {"trend": "up", "utc_session": 1}
    key = bucket(signal, features)
    model = {key: {"count": 40, "mean_r": 0.8, "lower_r": 0.3}}
    selected, mode, scored = choose([signal], features, model)
    assert selected == signal and mode == "exploit"
    assert scored[0]["lower_r"] == 0.3

def test_rules_include_new_hypotheses():
    for name in ("donchian_breakout", "liquidity_sweep", "squeeze_breakout"):
        assert name in STRATEGIES

def test_explore_picks_untested_candidate_only_when_enabled():
    signal = {"strategy": "gen_x", "side": "long"}
    assert choose([signal], {"trend": "up", "utc_session": 1}, {}, explore=True)[1] == "explore"

def test_genome_signal_uses_closed_bars_only_and_atr_stop():
    hours = [bar(i * 3600, close=100 + i) for i in range(60)]
    t = 60 * 3600
    minutes = [bar(t - 900 * (20 - i), close=100, high=101, low=99) for i in range(20)]
    g = {"mode": "breakout", "lookback": 20, "stop_atr": 1.5, "trend": True}
    signal = genome_signal(g, bar(t, close=105, open=101, high=106, low=100), hours, minutes)
    assert signal["side"] == "long" and signal["stop"] == 105 - 1.5 * 2
    future = bar(t + 900, close=1, high=1000, low=0)
    assert signal == genome_signal(g, bar(t, close=105, open=101, high=106, low=100), hours, minutes + [future])

def test_evolution_keeps_population_bounded_and_unique():
    samples = [{"status": "resolved", "strategy": genome_name(SEEDS[0]), "label_available_at": 1, "net_r_ex_swap": 1.0}
               for _ in range(25)]
    population = evolve(samples, SEEDS, 10, 1)
    assert 6 <= len(population) <= 12
    assert len({genome_name(g) for g in population}) == len(population)
    assert SEEDS[0] in population

def test_analysis_endpoint_without_candles_is_unavailable():
    from fastapi.testclient import TestClient
    from backend.app.main import app
    assert TestClient(app).get("/api/workspaces/exness_forex/learning/analysis").json()["status"] == "unavailable"

def test_current_analysis_returns_preview_of_learner_state(monkeypatch):
    from backend.app.forex_learner import current_analysis
    from backend.app.db import Session, WorkspaceCandle
    from backend.app.exness_feed import WORKSPACE
    with Session.begin() as session:
        base = 200 * 14400
        for frame, step, count in (("4h", 14400, 60), ("1h", 3600, 60), ("15m", 900, 60)):
            for i in range(count):
                t = base - step * (count - 1 - i)
                for side, shift in (("bid", 0), ("ask", 0.3)):
                    session.add(WorkspaceCandle(workspace_id=WORKSPACE, symbol="XAUUSD", timeframe=frame,
                                                price_type=side, time=t, open=2600.0 + shift,
                                                high=2610.0 + shift, low=2590.0 + shift, close=2605.0 + shift))
    analysis = current_analysis(base + 900)
    assert analysis is not None
    assert analysis["source"] == "closed_candle_policy_preview"
    assert "m15_donchian" in analysis["levels"]
    assert "barriers" in analysis["levels"]
    assert "strategies" in analysis
