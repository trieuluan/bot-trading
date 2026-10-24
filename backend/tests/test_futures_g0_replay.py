import copy
import math

from backend.app import futures_rule_tree
from backend.app.futures_rule_tree import validate_htf_rule
from backend.app.futures_strategies import RULE_SEEDS
from backend.app.futures_symbols import SYMBOLS
from backend.app.strategy import Candle, aggregate
from scripts import futures_g0_replay
from scripts.futures_g0_replay import candidates, summary


def test_matched_g0_changes_only_h4_slot_and_keeps_seeds_unchanged():
    original = copy.deepcopy(RULE_SEEDS)
    population = candidates()
    assert len(population) == 2 * len(RULE_SEEDS) == 52
    for index, seed in enumerate(RULE_SEEDS):
        consensus = population[f"consensus:{index:02d}"]["genome"]
        control = copy.deepcopy(population[f"ema_only:{index:02d}"]["genome"])
        validate_htf_rule(consensus["rule"], consensus=True)
        assert consensus == seed
        assert control["rule"]["children"][0] == {"feature": "trend", "frame": "H4"}
        control["rule"]["children"][0] = consensus["rule"]["children"][0]
        assert control == consensus
    assert RULE_SEEDS == original


def test_summary_keeps_arms_separate_and_deduplicates_events():
    payload = {"rows": [
        ("BTC_USDT", "ema_only", "validation", "00", 900, "long", -1.0, "stop"),
        ("BTC_USDT", "consensus", "validation", "00", 900, "long", 2.0, "target"),
        ("BTC_USDT", "consensus", "validation", "01", 900, "long", 1.0, "target"),
    ], "report": {"strategies": []}}
    result = summary([payload])
    assert result["ema_only"]["validation"]["mean_r"] == -1
    assert result["consensus"]["validation"]["trades"] == 2
    assert result["consensus"]["validation"]["events"] == 1
    assert result["consensus"]["validation"]["event_mean_r"] == 1.5
    assert result["consensus"]["train"]["trades"] == 0


def test_replay_cache_matches_runtime_on_fixed_context_and_restores_hooks(monkeypatch, tmp_path):
    candles = [Candle(index * 900, 200, 204 + math.sin(index / 16),
                      196 + math.sin(index / 16), 200 + math.sin(index / 16), 100)
               for index in range(1617)]
    raw = {symbol: (1600 * 900, 1617 * 900, candles, candles, []) for symbol in SYMBOLS}
    monkeypatch.setattr(futures_g0_replay, "load", lambda _: (raw, 0, 1616 * 900, list))
    monkeypatch.setattr(futures_g0_replay, "setup_memo", lambda: None)
    runtime = futures_rule_tree.h4_matches
    calls = []

    def counted(*args):
        calls.append(args[0])
        return runtime(*args)

    def replay(population, *_args, **_kwargs):
        for end in (1600, 1601, 1616):
            history = candles[end - 1599:end + 1]
            rows = aggregate(history, 14400)
            context = {"benchmarks": {symbol: history for symbol in SYMBOLS[1:]}}
            for feature in futures_rule_tree.H4_FEATURES:
                for direction in (1, -1):
                    args = (feature, rows, history, direction, context)
                    expected = runtime(*args)
                    assert futures_rule_tree.h4_matches(*args) == expected
                    assert futures_rule_tree.h4_matches(*args) == expected
        for _ in range(len(population) * 2):
            futures_g0_replay.futures_history.historical_stats([])
        return {"strategies": []}

    monkeypatch.setattr(futures_rule_tree, "h4_matches", counted)
    original_stats = futures_g0_replay.futures_history.historical_stats
    monkeypatch.setattr(futures_g0_replay.futures_history, "replay", replay)
    assert futures_g0_replay.run_pair(("BTC_USDT", "unused", str(tmp_path))) == "BTC_USDT"
    assert len(calls) == 30
    assert futures_rule_tree.h4_matches is counted
    assert futures_g0_replay.futures_history.historical_stats is original_stats
