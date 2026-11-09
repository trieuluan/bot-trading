import gzip
import json
import math
from types import SimpleNamespace

import pytest

from scripts import forex_structure_sweep as sweep


def _bar(stamp, open_, high, low, close):
    return SimpleNamespace(time=stamp, open=open_, high=high, low=low, close=close, tick_volume=100)


def test_regime_allows_all_cases():
    assert sweep.regime_allows("unfiltered", 1, 100, 105, 95, 2.0)
    assert sweep.regime_allows("h4_aligned", 1, 100, 98, 95, 2.0)
    assert not sweep.regime_allows("h4_aligned", 1, 90, 98, 95, 2.0)
    assert sweep.regime_allows("h4_counter", 1, 90, 92, 95, 2.0)
    assert not sweep.regime_allows("h4_counter", 1, 100, 98, 95, 2.0)
    assert sweep.regime_allows("h4_range", 1, 100, 95.1, 95.0, 2.0)
    assert not sweep.regime_allows("h4_range", 1, 100, 96.0, 95.0, 2.0)


def test_extra_match_nr7_and_momentum():
    bars = [_bar(i * 3600, 100, 105, 95, 100) for i in range(10)]
    bars[-1] = _bar(9 * 3600, 100, 101, 100, 100.5)
    assert sweep.extra_match("nr7", bars, 1, 100, 100, 5.0)
    bars[-1] = _bar(9 * 3600, 100, 110, 90, 100)
    assert not sweep.extra_match("nr7", bars, 1, 100, 100, 5.0)

    trend_bars = [_bar(i * 3600, 100 + i, 101 + i, 99 + i, 100.8 + i) for i in range(10)]
    assert sweep.extra_match("momentum6", trend_bars, 1, 105, 104, 2.0)
    assert not sweep.extra_match("momentum6", trend_bars, -1, 105, 104, 2.0)


def test_extra_match_ema_cross():
    bars = [_bar(0, 90, 95, 89, 92), _bar(3600, 92, 105, 91, 103)]
    assert sweep.extra_match("ema_cross", bars, 1, 100, 100, 5.0)
    assert not sweep.extra_match("ema_cross", bars, -1, 100, 100, 5.0)


def test_describe_calculates_stress_and_pf():
    sample = [dict(net_r=1.5, stress_r=1.2, ambiguous=False, symbol="EURUSD", available=1780000000),
              dict(net_r=-1.0, stress_r=-1.3, ambiguous=True, symbol="GBPUSD", available=1780000000)]
    stats = sweep.describe(sample, interval=False)
    assert stats["n"] == 2
    assert stats["mean_r"] == 0.25
    assert stats["pf"] == 1.5
    assert stats["ambiguous"] == 1
    assert stats["symbols"] == 2


def test_signals_do_not_change_when_future_bars_added():
    def series(count, duration):
        return [_bar(index * duration, 100 + math.sin(index / 7), 102 + math.sin(index / 7),
                     98 + math.sin(index / 7), 100.5 + math.sin(index / 7)) for index in range(count)]
    hours, higher = series(600, 3600), series(150, 14400)
    prefix = {("1h", "bid"): hours[:400], ("4h", "bid"): higher[:100]}
    full = {("1h", "bid"): hours, ("4h", "bid"): higher}
    expected = list(sweep.signals("EURUSD", prefix))
    assert expected
    assert expected == [signal for signal in sweep.signals("EURUSD", full) if signal["available"] <= 400 * 3600]
    assert len(sweep.FAMILIES) == 20
    assert len(set(sweep.FAMILIES)) == 20


def test_session_breaks_wait_for_complete_reference_and_first_cross():
    bars = [_bar(index * 3600, 100, 105, 95, 100) for index in range(10)]
    bars[-1] = _bar(9 * 3600, 100, 111, 99, 110)
    assert sweep.extra_match("london_orb_h1", bars, 1, 100, 100, 2)
    assert sweep.extra_match("asia_break", bars, 1, 100, 100, 2)
    assert not sweep.extra_match("asia_break", bars[1:], 1, 100, 100, 2)
    assert not sweep.extra_match("new_york_orb_h1", bars, 1, 100, 100, 2)
    assert not sweep.extra_match("london_orb_h1", bars + [_bar(10 * 3600, 110, 112, 109, 111)], 1, 100, 100, 2)


def test_bollinger_reclaim_not_outside_close():
    bars = [_bar(index * 3600, 100, 101, 99, 100 + (-1) ** index) for index in range(21)]
    bars.append(_bar(21 * 3600, 97, 100, 95, 99))
    assert sweep.extra_match("bollinger_reclaim", bars, 1, 100, 100, 2)
    bars[-1] = _bar(21 * 3600, 97, 100, 95, 96)
    assert not sweep.extra_match("bollinger_reclaim", bars, 1, 100, 100, 2)


def test_audit_rejects_overlap_and_premature_m15_fill(tmp_path):
    path = tmp_path / "records.gz"
    trade = dict(symbol="EURUSD", config="sweep|h4_aligned|m15_sweep|2.0R|all", available=10000,
                 release=13000, entry_time=10900, exit_time=11800, status="resolved", fold="discovery",
                 net_r=1.9, stress_r=1.7)
    with gzip.open(path, "wt") as stream:
        stream.write(json.dumps(trade) + "\n")
    assert sweep.audit_records(path)[0]["resolved"] == 1
    with gzip.open(path, "at") as stream:
        stream.write(json.dumps(trade) + "\n")
    with pytest.raises(ValueError, match="Overlapping"):
        sweep.audit_records(path)
    with gzip.open(path, "wt") as stream:
        stream.write(json.dumps({**trade, "entry_time": 10000}) + "\n")
    with pytest.raises(ValueError, match="M15 confirmation"):
        sweep.audit_records(path)
