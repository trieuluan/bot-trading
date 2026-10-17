from dataclasses import replace

from backend.app import futures_rules as research
from backend.app.market import Candle


def bars(count, rising=False):
    return [
        Candle(index * 900, 100, 101, 99, 100 + index if rising else 100, 1)
        for index in range(count)
    ]


def test_mean_reversion_range_reclaim_and_first_confirmation_only(monkeypatch):
    hours = [replace(candle, close=100 + (-1) ** index) for index, candle in enumerate(bars(20))]
    touch = Candle(20 * 3600, 100, 101, 94, 95, 1)
    higher = [replace(candle, close=100 + (-1) ** index) for index, candle in enumerate(bars(21))]
    monkeypatch.setattr(
        research,
        "aggregate",
        lambda candles, seconds: hours + [touch] if seconds == 3600 else higher,
    )
    candles = bars(20) + [Candle(touch.time + 3600, 97, 100, 96, 99, 1)]
    setup = research.mean_reversion(candles)
    assert setup.side == "long" and setup.stop < 94 and setup.target == 100
    assert (
        research.mean_reversion(candles + [replace(candles[-1], time=candles[-1].time + 900)])
        is None
    )
    higher[:] = bars(21, rising=True)
    assert research.mean_reversion(candles) is None


def test_sweep_reference_excludes_latest_bar_and_requires_reclaim(monkeypatch):
    higher = bars(50, rising=True)
    candles = bars(20) + [Candle(20 * 900, 99, 101, 98, 100, 1)]

    def aggregate(history, seconds):
        if seconds == 3600:
            assert history == candles[:-1]
            return bars(20)
        return higher

    monkeypatch.setattr(research, "aggregate", aggregate)
    setup = research.liquidity_sweep(candles)
    assert setup.side == "long" and setup.stop < 98
    assert abs((setup.target - setup.entry) / (setup.entry - setup.stop) - 2) < 1e-10
    candles[-1] = replace(candles[-1], close=98.5)
    assert research.liquidity_sweep(candles) is None


def test_funding_uses_only_strictly_prior_settlement_and_rejects_stale(monkeypatch):
    monkeypatch.setattr(research, "aggregate", lambda candles, seconds: bars(50, rising=True))
    candles = bars(20) + [Candle(40_000, 100, 111, 99, 110, 1)]
    close = candles[-1].time + 900
    prior = {"time": close - 1, "rate": -0.0003, "cycle_hours": 8}
    future = {"time": close, "rate": 0.0003, "cycle_hours": 8}
    setup = research.funding_filter([prior, future])(candles)
    assert setup.side == "long"
    assert research.funding_filter([future])(candles) is None
    assert research.funding_filter([{**prior, "time": close - 28801}])(candles) is None
    assert research.funding_filter([{**prior, "rate": -0.00029}])(candles) is None


def test_short_permission_and_valid_price_geometry(monkeypatch):
    monkeypatch.setattr(research, "aggregate", lambda candles, seconds: bars(50))
    last = Candle(900, 100, 101, 99, 100, 1)
    assert research.setup_at_close(last, -1, 110, 80, "test").side == "short"
    assert research.setup_at_close(last, -1, 90, 80, "test") is None
    assert research.setup_at_close(last, 1, 90, 95, "test") is None
    assert research.setup_at_close(last, -1, 110, -1, "test") is None
    monkeypatch.setattr(research, "ema50", lambda candles: 110)
    candles = bars(20) + [Candle(20 * 900, 101, 102, 99, 100, 1)]
    assert research.liquidity_sweep(candles) is None
    assert research.liquidity_sweep(candles, allow_short=True).side == "short"
