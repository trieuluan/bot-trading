import pytest

from backend.app.market import Candle
from scripts.htf_portfolio_replay import build_folds, funding_map, simulate_m15_trade


def test_build_folds_partitions_exact_timestamps():
    folds = build_folds(0, 400 * 86400)
    assert len(folds) == 3
    assert folds[0][0] == "discovery"
    assert folds[0][1] == 0
    assert folds[0][2] == folds[1][1]
    assert folds[1][2] == folds[2][1]
    assert folds[2][2] == 400 * 86400


def test_funding_map_aggregates_multiple_settlements():
    funding = [{"time": 1000, "rate": 0.0001}, {"time": 1000, "rate": 0.00005}, {"time": 2000, "rate": -0.0001}]
    fmap = funding_map(funding)
    assert abs(fmap[1000] - 0.00015) < 1e-12
    assert abs(fmap[2000] - (-0.0001)) < 1e-12


def test_simulate_m15_trade_charges_costs_and_hits_target():
    rows = [
        Candle(0, 100.0, 101.0, 99.5, 100.5, 1.0),
        Candle(900, 100.5, 105.0, 100.0, 104.5, 1.0),
    ]
    trade = simulate_m15_trade(rows, rows, {}, 0, 1, 98.0, "2R", 10, 2)
    assert trade is not None
    assert trade["reason"] == "target"
    assert trade["net_r"] > 1.8
    assert trade["cost_r"] > 0.0


def test_portfolio_unlimited_concurrency_and_reconciliation():
    from scripts.htf_portfolio_replay import simulate_event_portfolio
    rows = [
        Candle(0, 100.0, 102.0, 99.0, 101.0, 1.0),
        Candle(900, 101.0, 105.0, 100.5, 104.0, 1.0),
        Candle(1800, 104.0, 106.0, 103.0, 105.0, 1.0),
    ]
    fair = rows
    funding = [{"time": 900, "rate": 0.0001}]
    data = {"BTC_USDT": (None, 2700, rows, fair, funding)}
    t1 = simulate_m15_trade(rows, fair, {900: 0.0001}, 0, 1, 98.0, "2R", 2, 3)
    t2 = simulate_m15_trade(rows, fair, {900: 0.0001}, 0, 1, 98.0, "2R", 2, 3)
    assert t1 is not None and t2 is not None
    t1.update(symbol="BTC_USDT", arm="d1_squeeze_2R", seed="d1_squeeze")
    t2.update(symbol="BTC_USDT", arm="h4_sweep_2R", seed="h4_sweep")
    res = simulate_event_portfolio([t1, t2], data, risk_fraction=0.01, initial_equity=10000.0)
    assert res["max_concurrent_positions"] == 2
    assert res["max_positions_same_symbol"] == 2
    assert len(res["ledger"]) == 2
    assert res["final_equity"] > 10000.0


def test_native_seeds_registered_and_valid():
    from backend.app.futures_strategies import NATIVE_SEEDS, validate_genome, strategy_setup
    from backend.app.futures_native import hold_seconds
    for seed in NATIVE_SEEDS:
        validate_genome(seed)
        assert seed["entry_policy"] == "htf_close"
        assert seed["signal_frame"] in ("D1", "H4")
    assert hold_seconds("D1") == 15 * 86400
    assert hold_seconds("H4") == 5 * 86400
    assert hold_seconds(None) == 192 * 900
