"""Evaluate the ETH wedge adaptive rule on all 10 pairs with/without fee<=0.15R filter. Research only."""
import json
import pickle
from multiprocessing import Pool
from pathlib import Path

from backend.app import futures_history
from backend.app.futures_learner import FEE, SLIP
from backend.app.futures_strategies import regime, strategy_setup
from backend.app.futures_symbols import SYMBOLS
from scripts.futures_replay_340d import CACHE, setup_memo

OUTPUT = Path("/app/research-output/futures-eth-wedge-eval.json")
WEDGE_GENOME = {
    "family": "rule_tree",
    "lookback": 20,
    "target_r": 2.5,
    "stop_buffer": 0.1,
    "max_er": 0.3,
    "funding_threshold": 0.0003,
    "exit_profile": "volume_spike",
    "rule": {
        "op": "all",
        "children": [
            {"feature": "wedge", "frame": "H4"},
            {"feature": "volume", "frame": "M15"},
            {"feature": "candle_direction", "frame": "M15"},
        ],
    },
    "exit_policy": "adaptive",
}

def meets_cost_filter(entry, stop, max_cost_r=0.15):
    risk = abs(entry - stop)
    if risk <= 0:
        return False
    return entry * 2 * (FEE + SLIP) / risk <= max_cost_r

def run_pair(symbol):
    setup_memo()
    real = strategy_setup
    start, end, candles, fair, funding = pickle.loads(CACHE.read_bytes())[symbol]
    res = {}
    for label, max_cost in [("no_filter", None), ("cost<=0.15R", 0.15)]:
        def filtered(genome, history, known_funding):
            setup = real(genome, history, known_funding)
            if setup and max_cost is not None and not meets_cost_filter(setup.entry, setup.stop, max_cost):
                return None
            return setup
        futures_history.strategy_setup = filtered
        rep = futures_history.replay({"wedge": {"genome": WEDGE_GENOME}}, candles, fair, funding, start, end)
        s = rep["strategies"][0]
        res[label] = {"train": s["train"], "validation": s["validation"]}
    return symbol, res

if __name__ == "__main__":
    with Pool(4) as pool:
        results = pool.map(run_pair, SYMBOLS)
    out = {sym: data for sym, data in results}
    OUTPUT.write_text(json.dumps(out, indent=2, allow_nan=False))
    print(f'{"Symbol":12} {"Filter":14} {"Tr N":>5} {"Tr R":>7} {"Tr PF":>6} | {"Val N":>5} {"Val R":>7} {"Val PF":>6}')
    print("-" * 65)
    for sym, d in results:
        for f in ("no_filter", "cost<=0.15R"):
            t, v = d[f]["train"], d[f]["validation"]
            pf_t = f"{t['profit_factor']:.2f}" if t.get("profit_factor") is not None else "N/A"
            pf_v = f"{v['profit_factor']:.2f}" if v.get("profit_factor") is not None else "N/A"
            mr_t = t.get("mean_r") or 0.0
            mr_v = v.get("mean_r") or 0.0
            print(f"{sym:12} {f:14} {t['resolved']:5} {mr_t:+7.3f} {pf_t:>6} | {v['resolved']:5} {mr_v:+7.3f} {pf_v:>6}")
