from backend.app.market import Candle
from scripts.full_candle_edge_study import efficiency_ratio, sim_trade_m15, summarize


def test_efficiency_ratio_bounds_and_straight_line():
    closes = [100 + i for i in range(25)]
    er = efficiency_ratio(closes, 20)
    assert len(er) == 25
    assert er[19] == 0.0
    assert abs(er[24] - 1.0) < 1e-12


def test_sim_trade_m15_charges_costs_and_respects_stop_first():
    rows = [
        Candle(0, 100.0, 105.0, 97.5, 102.0, 1.0),
        Candle(900, 102.0, 103.0, 101.0, 102.5, 1.0),
    ]
    res = sim_trade_m15(rows, rows, [], [], 0, 1, 98.0, "2R", 1.0, 4, 2)
    assert res is not None
    assert res["reason"] == "stop"
    assert res["net_r"] < -1.0
    assert res["cost_r"] > 0.0
    st = summarize([res])
    assert st["n"] == 1 and st["mean_r"] == res["net_r"]
