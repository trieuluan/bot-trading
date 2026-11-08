from backend.app.mexc import Candle
from scripts.candle_ml_decision import eligible, features, fit, predict


def test_features_ignore_future_and_labels_require_availability():
    candles = [Candle(index * 900, 100, 102, 98, 101, 10) for index in range(300)]
    expected = features(candles, 270 * 900, 'long', 3)
    candles[271:] = [Candle(index * 900, 1000, 2000, 1, 1500, 100000) for index in range(271, 300)]
    assert features(candles, 270 * 900, 'long', 3) == expected
    rows = [dict(time=0, available=1800), dict(time=900, available=2700)]
    assert eligible(rows, 1800) == rows[:1]


def test_ridge_learns_linear_signal_and_handles_constant_columns():
    rows = [[float(index), 1.0] for index in range(20)]
    model = fit(rows, [2 * row[0] - 3 for row in rows], 0.000001)
    assert abs(predict(model, [12.0, 1.0]) - 21) < 0.001
