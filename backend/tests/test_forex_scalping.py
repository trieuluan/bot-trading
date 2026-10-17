from types import SimpleNamespace

from backend.app.forex_scalping import gate, replay, scalp_signal


def bar(time=1000, close=100.0, open=100.0, high=101.0, low=99.0):
    return SimpleNamespace(time=time, close=close, open=open, high=high, low=low)

def test_scalp_signal_requires_gapless_history_and_cost_edge():
    # 60 bars, 60s step
    history = [bar(time=i * 60, close=100.0 + i * 0.1, open=100.0 + i * 0.1, high=100.5 + i * 0.1, low=99.5 + i * 0.1)
               for i in range(60)]
    candle = bar(time=3600, close=110.0, open=106.0, high=110.5, low=105.5)
    ask = bar(time=3600, close=110.02)
    sig = scalp_signal(candle, history, ask)
    assert sig is not None
    assert sig["side"] == "long"
    assert sig["strategy"] == "scalp_m1_breakout"

def test_scalp_signal_filters_when_risk_less_than_spread_multiplier():
    history = [bar(time=i * 60, close=100.0 + i * 0.1, open=100.0 + i * 0.1, high=100.5 + i * 0.1, low=99.5 + i * 0.1)
               for i in range(60)]
    # close only slightly above stop, but spread large
    candle = bar(time=3600, close=106.1, open=106.0, high=106.2, low=106.0)
    ask = bar(time=3600, close=106.15)  # spread 0.05, 4x spread = 0.20, risk = 0.15 < 0.20
    sig = scalp_signal(candle, history, ask)
    assert sig is None

def test_replay_computes_train_and_validation():
    # 120 bars sequence
    bids = [bar(time=i * 60, close=100.0 + (i % 20) * 0.5, open=100.0 + (i % 20) * 0.5,
                high=101.0 + (i % 20) * 0.5, low=99.0 + (i % 20) * 0.5) for i in range(120)]
    asks = {b.time: bar(time=b.time, close=b.close + 0.02, high=b.high + 0.02, low=b.low + 0.02, open=b.open + 0.02)
            for b in bids}
    res = replay(bids, asks, {"slippage_bps": 1.0, "commission_per_unit": 0.0, "verified": True})
    assert res["candles"] == 120
    assert "train" in res and "validation" in res

def test_gate_flags_insufficient_samples():
    summary = {
        "train": {"resolved": 10, "lower_r": 0.5},
        "validation": {"resolved": 5, "lower_r": 0.2},
    }
    reasons = gate(summary, costs_verified=True)
    assert any("Thiếu mẫu train" in r for r in reasons)
    assert any("Thiếu mẫu validation" in r for r in reasons)

def test_variants_have_unique_ids_and_candidate_uses_train_only():
    from backend.app.forex_scalping import VARIANTS, train_candidate
    assert len({v["id"] for v in VARIANTS}) == len(VARIANTS)
    rows = [{"id": "a", "train": {"resolved": 40, "lower_r": 0.1}, "validation": {"lower_r": 9}},
            {"id": "b", "train": {"resolved": 40, "lower_r": 0.2}, "validation": {"lower_r": -9}},
            {"id": "c", "train": {"resolved": 5, "lower_r": 5}, "validation": {"lower_r": 5}}]
    assert train_candidate(rows) == "b"

def test_scalp_max_positions_env_is_separate_and_bounded(monkeypatch):
    from backend.app.forex_scalping import scalp_max_positions
    monkeypatch.delenv("EXNESS_SCALP_MAX_POSITIONS", raising=False)
    assert scalp_max_positions() == 1
    monkeypatch.setenv("EXNESS_SCALP_MAX_POSITIONS", "10")
    monkeypatch.setenv("EXNESS_DEMO_MAX_POSITIONS", "3")
    assert scalp_max_positions() == 10
    monkeypatch.setenv("EXNESS_SCALP_MAX_POSITIONS", "99")
    assert scalp_max_positions() == 20
    monkeypatch.setenv("EXNESS_SCALP_MAX_POSITIONS", "abc")
    assert scalp_max_positions() == 1
