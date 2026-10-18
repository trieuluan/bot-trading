"""Pre-specified entry filters on the 340d adaptive population. Research only: no tuning, no DB writes."""
import json
import pickle
import time
from multiprocessing import Pool
from pathlib import Path

from backend.app import futures_history
from backend.app.db import Session
from backend.app.futures_learner import FEE, SLIP, read_state
from backend.app.futures_strategies import regime, strategy_setup
from backend.app.futures_symbols import SYMBOLS, symbol_scope
from scripts.futures_replay_340d import CACHE, setup_memo

OUTPUT = Path("/app/research-output/futures-entry-filters.json")
VARIANTS = {"baseline": (None, False), "cost<=0.25R": (0.25, False),
            "cost<=0.15R": (0.15, False), "with_trend": (None, True), "cost<=0.25R+trend": (0.25, True)}

def allowed(max_cost_r, with_trend, side, context, entry, stop):
    """Round-trip fee+slippage as a fraction of initial risk; counter-trend entries are rejected."""
    if max_cost_r is not None and entry * 2 * (FEE + SLIP) / abs(entry - stop) > max_cost_r:
        return False
    return not with_trend or (side, context) in (("long", "trend_up"), ("short", "trend_down"))

def run(task):
    symbol, name = task
    max_cost_r, with_trend = VARIANTS[name]
    setup_memo()
    real = strategy_setup

    def filtered(genome, history, funding):
        setup = real(genome, history, funding)
        if setup and not allowed(max_cost_r, with_trend, setup.side, regime(history), setup.entry, setup.stop):
            return None
        return setup

    futures_history.strategy_setup = filtered
    with symbol_scope(symbol), Session.begin() as session:
        report = read_state(session, int(time.time()))["historical"]["report"]
    candidates = {row["strategy"]: {"genome": row["genome"]} for row in report["strategies"]
                  if row["genome"].get("exit_policy") == "adaptive"}
    start, end, candles, fair, funding = pickle.loads(CACHE.read_bytes())[symbol]
    rows = futures_history.replay(candidates, candles, fair, funding, start, end)["strategies"]
    out = {}
    for part in ("train", "validation"):
        n = sum(r[part]["resolved"] for r in rows)
        total = sum((r[part]["mean_r"] or 0) * r[part]["resolved"] for r in rows)
        positive = sum(r[part]["resolved"] >= 30 and (r[part]["mean_r"] or 0) > 0 for r in rows)
        out[part] = {"resolved": n, "total_r": total, "positive": positive}
    return symbol, name, out

if __name__ == "__main__":
    with Pool(4) as pool:
        results = pool.map(run, [(s, v) for v in VARIANTS for s in SYMBOLS])
    summary = {name: {part: {"resolved": 0, "total_r": 0.0, "positive": 0} for part in ("train", "validation")}
               for name in VARIANTS}
    for _, name, out in results:
        for part, stat in out.items():
            for key, value in stat.items():
                summary[name][part][key] += value
    OUTPUT.write_text(json.dumps(summary, indent=1, allow_nan=False))
    print(f'{"variant":20} {"part":10} {"trades":>8} {"mean R":>9} {"cands>0":>8}', flush=True)
    for name, parts in summary.items():
        for part, stat in parts.items():
            mean = stat["total_r"] / stat["resolved"] if stat["resolved"] else 0
            print(f'{name:20} {part:10} {stat["resolved"]:8} {mean:+9.4f} {stat["positive"]:8}', flush=True)
