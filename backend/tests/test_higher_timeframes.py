import pytest

from backend.app.market import Candle
from scripts.higher_timeframes import higher_signal


@pytest.mark.parametrize("seconds", [3600, 14400])
def test_signal_only_on_closed_higher_bar_and_excludes_partial_history(seconds):
    received = []

    def signal(candles, allow_short=False):
        received.append((candles, allow_short))
        return "setup" if candles else None

    wrapped = higher_signal(signal, seconds)
    count = seconds // 900
    candles = [Candle(index * 900, 100, 102, 98, 101, 10) for index in range(count * 2)]
    assert wrapped(candles[:-1]) is None and received == []
    assert wrapped(candles, allow_short=True) == "setup"
    closed, allowed = received[-1]
    assert allowed and len(closed) == 2
    assert closed[-1] == Candle(seconds, 100, 102, 98, 101, 10 * count)
    assert wrapped(candles[1:count]) is None
    assert received[-1][0] == []


def test_signal_empty_and_unsupported_timeframe():
    assert higher_signal(lambda candles, allow_short: None, 3600)([]) is None
    with pytest.raises(ValueError):
        higher_signal(lambda candles: None, 60)
