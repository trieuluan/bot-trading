import random

import pytest

from backend.app.forex_learner import (
    FAMILIES,
    SEEDS,
    bucket,
    choose,
    context,
    evolve,
    genome_name,
    genome_signal,
)
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
    signal = {"strategy": "trend_pullback", "side": "long"}
    assert choose([signal], {"trend": "up", "utc_session": 1}, {}, explore=True)[1] == "explore"
    gen = {"strategy": "gen_x", "side": "long", "generated": True}
    assert choose([gen], {"trend": "up", "utc_session": 1}, {}, explore=True, promoted=())[1] == "abstain"
    assert choose([gen], {"trend": "up", "utc_session": 1}, {}, explore=True, promoted={"gen_x"})[1] == "explore"

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
    samples = [{"status": "resolved", "strategy": genome_name(SEEDS[0]), "confirm_time": 0, "label_available_at": 1, "net_r_ex_swap": 1.0}
               for _ in range(25)]
    population = evolve(samples, SEEDS, 10, 1)
    assert 14 <= len(population) <= 56
    assert len({genome_name(g) for g in population}) == len(population)
    assert {g['family'] for g in population} == set(FAMILIES)

def test_analysis_endpoint_without_candles_is_unavailable():
    from fastapi.testclient import TestClient

    from backend.app.main import app
    assert TestClient(app).get("/api/workspaces/exness_forex/learning/analysis").json()["status"] == "unavailable"

def test_current_analysis_returns_preview_of_learner_state(monkeypatch):
    from backend.app.db import Session, WorkspaceCandle
    from backend.app.exness_feed import WORKSPACE
    from backend.app.forex_learner import current_analysis
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

def test_multi_family_evolution_and_promotion_gate():
    from backend.app.forex_learner import (
        BOUNDS,
        CAP,
        FAMILIES,
        SEEDS,
        crossover,
        evolve,
        genome_name,
        mutate,
        promoted_names,
    )
    assert len(FAMILIES) == 14
    for seed in SEEDS:
        assert seed["family"] in FAMILIES
        child = mutate(seed, random.Random(7))
        assert child["family"] == seed["family"]
        for key, _, low, high in BOUNDS[child["family"]]:
            assert low <= child[key] <= high

    a = {"family": "rsi", "period": 14, "oversold": 30, "overbought": 70, "stop_atr": 1.0, "trend": False}
    b = {"family": "rsi", "period": 9, "oversold": 20, "overbought": 80, "stop_atr": 2.0, "trend": True}
    crossed = crossover(a, b)
    assert crossed["period"] == 14 and crossed["trend"] is True and crossed["stop_atr"] == 2.0

    samples = [
        {"status": "resolved", "strategy": "gen_winner", "confirm_time": 0, "label_available_at": 1000, "net_r_ex_swap": 1.5}
        for _ in range(35)
    ] + [
        {"status": "resolved", "strategy": "gen_winner", "confirm_time": 10000, "label_available_at": 11000, "net_r_ex_swap": 1.2}
        for _ in range(35)
    ]
    promoted = promoted_names(samples, cutoff=10000, now=20000)
    assert "gen_winner" in promoted

    fail_train_only = [
        {"status": "resolved", "strategy": "gen_lucky", "confirm_time": 0, "label_available_at": 1000, "net_r_ex_swap": 2.0}
        for _ in range(35)
    ]
    assert "gen_lucky" not in promoted_names(fail_train_only, cutoff=10000, now=20000)

    next_pop = evolve([], SEEDS, cutoff=100, seed=1)
    assert len(next_pop) <= CAP
    assert len({genome_name(g) for g in next_pop}) == len(next_pop)

def test_session_and_channel_genomes_ignore_future_bars():
    from backend.app.forex_learner import SEEDS, genome_signal
    day = 20 * 86400
    t = day + 8 * 3600
    hours = [bar(t - (60 - i) * 3600, close=100 + i) for i in range(60)]
    flat = [bar(t - 900 * (48 - i), close=100, open=100, high=101, low=99) for i in range(48)]
    fired = {}
    cases = {
        "channel": (bar(t, close=104, open=101, high=105, low=100), flat),
        "session": (bar(t, close=104, open=101, high=105, low=100), flat),
        "squeeze": (bar(t, close=104, open=101, high=105, low=100), flat),
    }
    for seed in SEEDS:
        family = seed["family"]
        if family not in cases:
            continue
        candle, minutes = cases[family]
        signal = genome_signal({**seed, "trend": False}, candle, hours, minutes)
        if signal:
            fired[family] = signal
            future = bar(t + 900, close=1, high=1000, low=0)
            assert signal == genome_signal({**seed, "trend": False}, candle, hours, minutes + [future])
    assert {"channel", "session", "squeeze"} <= set(fired)

@pytest.mark.parametrize("family", [family for family in FAMILIES if family not in ("orb", "vsa", "mss", "vwap")])
def test_all_genome_families_produce_a_signal_without_future_data(family):
    from backend.app.forex_learner import genome_signal

    stamp = 20 * 86400 + 8 * 3600
    hours = [bar(stamp - (60 - index) * 3600, close=100 + index,
                 high=101 + index, low=99 + index) for index in range(60)]
    history = [bar(stamp - (60 - index) * 900) for index in range(60)]
    higher = [bar(stamp - (50 - index) * 14400, close=100 + index,
                  high=101 + index, low=99 + index) for index in range(50)]
    candle = bar(stamp, open=100, close=104, high=105, low=99)
    if family in ("pullback", "router"):
        candle = bar(stamp, open=99, close=101, high=102, low=98)
    elif family == "range":
        hours = [bar(stamp - (60 - index) * 3600, high=110, low=90) for index in range(60)]
        candle = bar(stamp, open=109, close=105, high=110, low=104)
    elif family == "sweep":
        candle = bar(stamp, open=99, close=100, high=101, low=98)
    elif family == "rsi":
        history = [bar(stamp - (60 - index) * 900, close=200 - index,
                       open=201 - index, high=202 - index, low=199 - index) for index in range(60)]
        candle = bar(stamp, open=141, close=145, high=146, low=140)
    elif family == "fvg":
        history[-3:] = [bar(stamp - 2700, close=100, high=101, low=99),
                        bar(stamp - 1800, open=100, close=105, high=106, low=100),
                        bar(stamp - 900, open=105, close=106, high=107, low=103)]
        candle = bar(stamp, open=104, close=105, high=106, low=102)
    elif family == "retest":
        hours = [bar(stamp - (60 - index) * 3600, high=110, low=90) for index in range(60)]
        hours[-1] = bar(stamp - 3600, open=100, close=120, high=121, low=99)
        candle = bar(stamp, open=111, close=112, high=113, low=109)
    genome = {**next(seed for seed in SEEDS if seed["family"] == family), "trend": False}
    signal = genome_signal(genome, candle, hours, history, higher)
    assert signal is not None
    assert signal["stop"] < candle.close if signal["side"] == "long" else signal["stop"] > candle.close
    future = bar(stamp + 900, close=1, high=99999, low=0)
    assert signal == genome_signal(genome, candle, hours + [future], history + [future], higher + [future])
    assert genome_signal(genome, candle, hours, history[:-1], higher) is None

def test_recursive_evolution_diversity_lineage_and_retirement():
    from collections import Counter

    from backend.app.forex_learner import CAP, evolve, mutate, validate_genome

    population = SEEDS
    for generation in range(5):
        population = evolve([], population, 10000, generation)
        counts = Counter(genome["family"] for genome in population)
        assert set(counts) == set(FAMILIES)
        assert all(2 <= count <= 4 for count in counts.values())
        assert len(population) <= CAP
        assert len({genome_name(genome) for genome in population}) == len(population)
        for genome in population:
            validate_genome(genome)
    child = mutate(mutate(SEEDS[0], random.Random(1)), random.Random(2))
    assert child["generation"] == 2
    assert child["parents"]
    loser = genome_name(SEEDS[0])
    results = [{"status": "resolved", "strategy": loser, "label_available_at": 1000,
                "net_r_ex_swap": -1} for _ in range(40)]
    assert loser not in {genome_name(genome) for genome in evolve(results, SEEDS, 10000, 1)}
    assert loser not in {genome_name(genome) for genome in evolve([], SEEDS, 10000, 1, retired=[loser])}
    future = [{**sample, "label_available_at": 11000} for sample in results]
    assert evolve(future, SEEDS, 10000, 1) == evolve([], SEEDS, 10000, 1)

def test_genome_identity_ignores_order_and_ancestry_and_rejects_invalid_parameters():
    from backend.app.forex_learner import mutate, validate_genome

    for seed in SEEDS:
        assert genome_name(seed) == genome_name(dict(reversed(list(seed.items()))))
        assert genome_name(seed) == genome_name({**seed, "generation": 99, "parents": ["gen_old"]})
        with pytest.raises(ValueError):
            validate_genome({**seed, "stop_atr": float("nan")})
        with pytest.raises(ValueError):
            validate_genome({**seed, "trend": "false"})
        genome = seed
        for index in range(50):
            genome = mutate(genome, random.Random(index))
            validate_genome(genome)

def test_promotion_excludes_boundary_crossing_and_not_yet_closed_labels():
    from backend.app.forex_learner import promoted_names

    train = [{"status": "resolved", "strategy": "gen_test", "confirm_time": 0,
              "label_available_at": 1000, "net_r_ex_swap": 1} for _ in range(30)]
    validation = [{"status": "resolved", "strategy": "gen_test", "confirm_time": 10000,
                   "label_available_at": 11000, "net_r_ex_swap": 1} for _ in range(30)]
    assert promoted_names(train + validation, 10000, 20000) == ["gen_test"]
    assert promoted_names(train + validation[:29], 10000, 20000) == []
    assert promoted_names(train + [{**row, "confirm_time": 0} for row in validation], 10000, 20000) == []
    assert promoted_names(train + [{**row, "label_available_at": 20000} for row in validation], 10000, 20000) == []
    assert promoted_names(train + [{**row, "net_r_ex_swap": -1} for row in validation], 10000, 20000) == []

def test_unapproved_genome_cannot_bypass_gate_by_omitting_generated_flag():
    features = {"trend": "up", "utc_session": 1}
    signal = {"strategy": "gen_unsafe", "side": "long"}
    model = {bucket(signal, features): {"count": 100, "mean_r": 2, "lower_r": 1}}
    selected, mode, scores = choose([signal], features, model, explore=True)
    assert selected is None and mode == "abstain" and scores[0]["shadow_only"]

def test_chart_analysis_uses_each_genome_parameters_and_never_invents_levels():
    from backend.app.forex_chart_analysis import chart_analysis
    minutes = [bar(i * 900, close=100 + i % 3, high=101 + i % 5, low=99 - i % 2) for i in range(60)]
    candle, history = bar(60 * 900), minutes
    small = chart_analysis({"family": "channel", "lookback": 10}, candle, [], history)
    large = chart_analysis({"family": "channel", "lookback": 40}, candle, [], history)
    assert small["levels"][0]["from"] > large["levels"][0]["from"]  # window follows the genome
    assert chart_analysis({"family": "fvg", "min_gap": 0}, candle, [], [bar(i * 900) for i in range(60)])["levels"] == []
    assert chart_analysis({"family": "rsi", "period": 8, "oversold": 20, "overbought": 80}, candle, [], history)["oscillator"]["label"] == "RSI8"
    assert chart_analysis({"family": "channel", "lookback": 20}, candle, [], [])["note"]

def test_shadow_report_reasons():
    from backend.app.forex_learner import shadow_report
    rows = [{"status": "resolved", "strategy": "gen_a", "confirm_time": 0, "label_available_at": 1, "net_r_ex_swap": 1.0}] * 5
    r = shadow_report(rows, 10000, 20000, ["gen_a", "gen_b"], [], ["gen_b"], False)
    assert r[0]["status"] == "shadow" and "Thiếu mẫu train 5/30" in r[0]["reasons"]
    assert r[1]["status"] == "retired" and r[1]["reasons"][0].startswith("Đã loại")

def test_forward_summary_groups_by_version(tmp_path, monkeypatch):
    import json

    from backend.app import forex_learner as fl
    monkeypatch.setattr(fl, "FORWARD", tmp_path / "forward.json")
    p = fl.forward_path("demo-1")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({
        "account": "demo-1", "since": 1000,
        "samples": [
            {"strategy": "strat_a", "strategy_version": "v1", "status": "resolved", "net_r_ex_swap": 1.5,
             "costs": {"verified": True}},
            {"strategy": "strat_a", "strategy_version": "v1", "status": "resolved", "net_r_ex_swap": 0.5,
             "costs": {"verified": True}},
            {"strategy": "strat_a", "strategy_version": "v2", "status": "open", "net_r_ex_swap": None,
             "costs": {"verified": True}},
        ]
    }))
    summary = fl.forward_summary("demo-1")
    assert summary["signals"] == 3
    assert len(summary["strategies"]) == 2
    strat_v1 = next(s for s in summary["strategies"] if s["version"] == "v1")
    assert strat_v1["resolved"] == 2 and strat_v1["mean_r"] == 1.0

def test_evolve_ranks_by_lower_bound_not_lucky_mean():
    lucky, proven = SEEDS[0], SEEDS[1]
    samples = [{"status": "resolved", "strategy": genome_name(lucky), "label_available_at": 1, "net_r_ex_swap": 5.0} for _ in range(3)]
    samples += [{"status": "resolved", "strategy": genome_name(proven), "label_available_at": 1,
                 "net_r_ex_swap": 0.4 + 0.1 * (i % 2)} for i in range(30)]
    kept = [genome_name(g) for g in evolve(samples, SEEDS, 10, 1) if g["family"] == "channel"]
    assert kept[0] == genome_name(proven)

def test_target_r_evolves_in_bounds_and_drives_signal():
    from backend.app.forex_learner import crossover, mutate, validate_genome
    genome = {**SEEDS[0], "target_r": 2.5}
    validate_genome(genome)
    assert "_tp2.5_" in genome_name(genome) and genome_name(genome) != genome_name(SEEDS[0])
    for index in range(50):
        genome = mutate(genome, random.Random(index))
        validate_genome(genome)
        assert 1.5 <= genome["target_r"] <= 3.0
    assert crossover(SEEDS[0], {**SEEDS[1], "target_r": 3.0})["target_r"] == 3.0
    with pytest.raises(ValueError):
        validate_genome({**SEEDS[0], "target_r": 9})
    with pytest.raises(ValueError):
        validate_genome({**SEEDS[0], "partial_be": "yes"})

def test_generated_signal_uses_genome_target_r():
    from decimal import Decimal

    from backend.app.forex_buffer_study import exit_target
    signal = {"side": "long", "generated": True, "target_r": 2.5, "exit_variant": "k0.25_3r"}
    target, reason = exit_target(signal, Decimal("2600"), Decimal("2590"), Decimal("0.001"))
    assert target == Decimal("2625.000") and reason == "gen_fixed_2.5r"

def test_full_hold_genome_has_single_runner_and_no_breakeven_management():
    from decimal import Decimal

    from backend.app.exness_management import split_bodies
    cond = {"volume_min": "0.01", "volume_step": "0.01", "point_digits": 3}
    body = {"volume": "0.10", "stop_loss_price": "2590", "take_profit_price": "2620"}
    assert len(split_bodies(body, cond, Decimal("2600"), partial_be=True)) == 2
    assert [leg["role"] for leg in split_bodies(body, cond, Decimal("2600"), partial_be=False)] == ["runner"]
    assert genome_name({**SEEDS[0], "partial_be": False}) != genome_name(SEEDS[0])

def test_genome_exit_ignores_learned_variant_and_rejects_bad_target():
    from decimal import Decimal

    from backend.app.exness import ExnessError
    from backend.app.forex_buffer_study import exit_target, select_buffer
    signal = {"side": "long", "generated": True, "target_r": 2.5}
    assert select_buffer(signal, {"trend": "up", "utc_session": 1}, {})["exit_variant_mode"] == "genome"
    with pytest.raises(ExnessError):
        exit_target({**signal, "target_r": float("nan")}, Decimal("2600"), Decimal("2590"), Decimal("0.001"))


def vbar(time, open=100.0, close=100.0, high=101.0, low=99.0, volume=100):
    row = bar(time, close=close, open=open, high=high, low=low)
    row.tick_volume = volume
    return row

def test_extra_families_signal_without_future_data_and_expose_levels():
    from backend.app.forex_chart_analysis import chart_analysis
    from backend.app.forex_extra_families import extra_setup

    day = 20 * 86400
    london = {"family": "orb", "session": "london", "range_bars": 2, "trade_bars": 8, "stop_atr": 1.5, "trend": False}
    zone = __import__("zoneinfo").ZoneInfo("Europe/London")
    opening = int(__import__("datetime").datetime.fromtimestamp(day + 12 * 3600, zone).replace(
        hour=8, minute=0, second=0, microsecond=0).timestamp())
    history = [vbar(opening + i * 900, high=101, low=99) for i in range(-4, 2)]
    candle = vbar(opening + 2 * 900, open=101, close=103, high=104, low=100)
    assert extra_setup(london, candle, history)[0] == "long"
    assert extra_setup(london, candle, history + [vbar(candle.time + 900, close=1, high=999, low=0)])[0] == "long"
    assert any(label == "Opening range high" for label, *_ in extra_setup(london, candle, history)[1])

    vsa = {"family": "vsa", "lookback": 20, "vol_mult": 1.5, "wick_ratio": 0.6, "stop_atr": 1, "trend": False}
    history = [vbar(day + i * 900) for i in range(30)]
    candle = vbar(day + 30 * 900, open=100, close=101, high=101.2, low=96, volume=250)
    assert extra_setup(vsa, candle, history)[0] == "long"
    candle.tick_volume = None
    assert extra_setup(vsa, candle, history)[0] is None
    candle.tick_volume = 250

    vwap = {"family": "vwap", "lookback": 30, "dev_mult": 1.5, "stop_atr": 1, "trend": False}
    history = [vbar(day + i * 900, open=100, close=100 + (i % 3 - 1) * 0.2, high=101, low=99) for i in range(40)]
    history[20] = vbar(history[20].time, open=100, close=90, high=100, low=89)
    candle = vbar(day + 40 * 900, open=98, close=99.5, high=100, low=88)
    assert extra_setup(vwap, candle, history)[0] == "long"
    assert chart_analysis(vwap, candle, [], history)["levels"]
    candle.tick_volume = None
    assert extra_setup(vwap, candle, history[:-1] + [vbar(history[-1].time, volume=None)])[0] is None

    mss = {"family": "mss", "lookback": 5, "wait_bars": 4, "retrace": 0.5, "stop_atr": 1, "trend": False}
    history = [vbar(day + i * 900, open=100, close=100, high=101, low=99) for i in range(12)]
    history[8] = vbar(history[8].time, open=100, close=99.5, high=100.5, low=95)
    history[9] = vbar(history[9].time, open=99.5, close=103, high=103.5, low=99)
    history[10] = vbar(history[10].time, open=103, close=102.5, high=103, low=100.5)
    history[11] = vbar(history[11].time, open=102.5, close=102, high=102.7, low=100.2)
    candle = vbar(day + 12 * 900, open=100.2, close=101.5, high=102, low=98.8)
    assert extra_setup(mss, candle, history)[0] == "long"
