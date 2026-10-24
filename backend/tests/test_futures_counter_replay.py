import pytest

from backend.app import futures_strategies
from backend.app.futures_exits import discretionary_exit
from backend.app.futures_rule_tree import is_htf_rule
from backend.app.futures_symbols import SYMBOLS
from backend.app.strategy import Candle
from scripts import futures_counter_replay
from scripts.futures_counter_replay import candidates, summary


def test_arms_share_h1_m15_and_exit_settings():
    population = candidates()
    assert len(population) == 12
    for name in "ABC":
        counter = population[f"counter:{name}"]["genome"]
        assert counter == population[f"aligned:{name}"]["genome"]
        assert is_htf_rule(counter["rule"])
        assert counter["exit_policy"] == "adaptive"
        mr = population[f"counter_mr:{name}"]["genome"]
        assert mr == population[f"aligned_mr:{name}"]["genome"]
        assert mr["rule"] == counter["rule"] and mr["exit_policy"] == "time_stop"


def test_summary_separates_arms():
    payload = {"rows": [("BTC_USDT", "counter", "validation", "A", 900, "long", 1.0, "target"),
                        ("BTC_USDT", "aligned", "validation", "A", 900, "long", -1.0, "stop")],
               "report": {"strategies": []}}
    result = summary([payload])
    assert result["counter"]["validation"]["mean_r"] == 1
    assert result["aligned"]["validation"]["mean_r"] == -1


def test_h4_gate_requires_low_adx_and_correct_relation_to_ema(monkeypatch):
    rows = [Candle(index * 14400, 100, 103, 99, 101, 1) for index in range(50)]
    monkeypatch.setattr(futures_counter_replay, "adx_state", lambda _: (19.9, 1))
    monkeypatch.setattr(futures_counter_replay, "ema50", lambda _: 100)
    assert futures_counter_replay.h4_gate(rows, -1, opposed=True)
    assert not futures_counter_replay.h4_gate(rows, 1, opposed=True)
    assert futures_counter_replay.h4_gate(rows, 1, opposed=False)
    assert not futures_counter_replay.h4_gate(rows, -1, opposed=False)
    for invalid in (None, (20, 1), (30, 1)):
        monkeypatch.setattr(futures_counter_replay, "adx_state", lambda _, value=invalid: value)
        assert not futures_counter_replay.h4_gate(rows, -1, opposed=True)
    assert not futures_counter_replay.h4_gate(rows[:49], -1, opposed=True)


@pytest.mark.parametrize("failure", (False, True))
def test_root_override_keeps_htf_context_and_restores_hooks(monkeypatch, tmp_path, failure):
    candles = [Candle(index * 900, 100, 102, 99, 101, 1) for index in range(1601)]
    raw = {symbol: (1600 * 900, 1601 * 900, candles, candles, []) for symbol in SYMBOLS}
    monkeypatch.setattr(futures_counter_replay, "load", lambda _: (raw, 0, 1600 * 900, list))
    monkeypatch.setattr(futures_counter_replay, "setup_memo", lambda: None)
    calls = []

    def original(rule, history, _direction, _lookback, _anchor, context):
        calls.append(rule["frame"])
        assert context["htf"] and history == candles[1:]
        return True

    def gate(rows, direction, opposed):
        assert rows[-1].time + 14400 <= candles[-1].time + 900
        return direction == (-1 if opposed else 1)

    def replay(population, *_args, **_kwargs):
        for name, candidate in population.items():
            root = candidate["genome"]["rule"]
            direction = -1 if name.startswith("counter") else 1
            assert futures_strategies.matches(root, candles[1:], direction, 20,
                                              context={"htf": True})
            assert not futures_strategies.matches(root, candles[1:], -direction, 20,
                                                  context={"htf": True})
            for _ in range(2):
                futures_counter_replay.futures_history.historical_stats([])
        if failure:
            raise RuntimeError("test replay failure")
        return {"strategies": []}

    monkeypatch.setattr(futures_strategies, "matches", original)
    monkeypatch.setattr(futures_counter_replay, "h4_gate", gate)
    monkeypatch.setattr(futures_counter_replay.futures_history, "replay", replay)
    stats = futures_counter_replay.futures_history.historical_stats
    task = ("BTC_USDT", "unused", str(tmp_path))
    if failure:
        with pytest.raises(RuntimeError, match="test replay failure"):
            futures_counter_replay.run_pair(task)
    else:
        assert futures_counter_replay.run_pair(task) == "BTC_USDT"
    assert calls == ["H1", "M15"] * 12
    assert futures_strategies.matches is original
    assert futures_counter_replay.futures_history.historical_stats is stats


def test_mean_levels_keep_stop_and_reject_poor_reward(monkeypatch):
    hours = [Candle(index * 3600, 100, 101, 93, 100, 1) for index in range(20)]
    monkeypatch.setattr(futures_counter_replay, "h1_view", lambda _: (hours, 2.0))
    signal = [Candle(72000, 95, 96, 94, 95, 1)]
    assert futures_counter_replay.mr_levels("long", signal) == (92.5, 100)
    signal[0] = Candle(72000, 99, 100, 98, 99, 1)
    assert futures_counter_replay.mr_levels("long", signal) is None
    signal[0] = Candle(72000, 95, 96, 94, 95, 1)
    assert futures_counter_replay.mr_levels("short", signal) is None
    hours[:] = [Candle(index * 3600, 100, 107, 93, 100, 1) for index in range(20)]
    signal[0] = Candle(72000, 105, 106, 104, 105, 1)
    assert futures_counter_replay.mr_levels("short", signal) == (107.5, 100)
    monkeypatch.setattr(futures_counter_replay, "h1_view", lambda _: (hours[:19], 2.0))
    assert futures_counter_replay.mr_levels("long", signal) is None


@pytest.mark.parametrize("failure", (False, True))
def test_mean_target_uses_own_levels_before_legacy_filter_and_restores(monkeypatch, failure):
    def legacy(*_args):
        return None

    def own(*_args):
        return (94, 100)

    def real(genome, history, funding, **kwargs):
        if failure:
            raise RuntimeError("levels failure")
        return futures_strategies.adaptive_levels("long", history)

    monkeypatch.setattr(futures_strategies, "adaptive_levels", legacy)
    monkeypatch.setattr(futures_counter_replay, "mr_levels", own)
    wrapped = futures_counter_replay.mean_target(real)
    if failure:
        with pytest.raises(RuntimeError, match="levels failure"):
            wrapped({"exit_policy": "time_stop"}, [], [])
    else:
        assert wrapped({"exit_policy": "time_stop"}, [], []) == (94, 100)
        assert wrapped({"exit_policy": "adaptive"}, [], []) is None
    assert futures_strategies.adaptive_levels is legacy


def test_mean_exit_time_stop_is_eight_hours_and_conditional():
    position = {"side": "long", "entry": 100, "risk": 10, "entry_time": 0,
                "exit_policy": "time_stop", "exit_params": futures_counter_replay.MR_EXIT}
    assert discretionary_exit(position, [Candle(27000, 100, 105, 99, 104, 1)]) is None
    assert discretionary_exit(position, [Candle(27900, 100, 105, 99, 104, 1)]) == "time_stop"
    assert discretionary_exit(position, [Candle(27900, 100, 106, 99, 105, 1)]) is None
