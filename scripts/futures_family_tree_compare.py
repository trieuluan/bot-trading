"""Replay 12 legacy families vs their rule_tree conversions on cached real MEXC history. Read-only."""
import json
import pickle
import sys
from multiprocessing import Pool
from pathlib import Path

from backend.app import strategy
from backend.app.futures_history import replay
from backend.app.futures_strategies import LEGACY_SEEDS as SEEDS, genome_name, to_rule_tree
from backend.app.futures_symbols import SYMBOLS

DAYS = int(sys.argv[1]) if len(sys.argv) > 1 else 340
CACHE = Path(f"/tmp/mexc_10pairs_{DAYS}d_compare.pkl")
OUTPUT = Path(f"research-output/futures-family-tree-compare-{DAYS}d.json")
KEYS = ("signals", "resolved", "mean_r", "profit_factor")
_orig, _memo = strategy.aggregate, {}

def memo_aggregate(candles, seconds):
    key = (candles[0].time, candles[-1].time, len(candles), seconds) if candles else None
    if key not in _memo:
        if len(_memo) > 200:
            _memo.clear()
        _memo[key] = _orig(candles, seconds)
    return _memo[key]

def run(symbol):
    import backend.app.futures_rule_tree as tree_module
    import backend.app.futures_rules as rules_module
    for module in (strategy, tree_module, rules_module):  # same pure function, memoised
        if hasattr(module, "aggregate"):
            module.aggregate = memo_aggregate
    import backend.app.futures_strategies as strategies_module
    strategies_module.aggregate = memo_aggregate
    start, end, candles, fair, funding = pickle.loads(CACHE.read_bytes())[symbol]
    legacy = {genome_name(s): {"genome": s} for s in SEEDS if "exit_policy" not in s}
    trees = {genome_name(to_rule_tree(s)) + "|" + s["family"]: {"genome": to_rule_tree(s)}
             for s in SEEDS if "exit_policy" not in s}
    old = {r["genome"]["family"]: r for r in replay(legacy, candles, fair, funding, start, end)["strategies"]}
    result = {}
    for row in replay(trees, candles, fair, funding, start, end)["strategies"]:
        family = row["strategy"].split("|")[1]
        result[family] = {"legacy": {p: {k: old[family][p][k] for k in KEYS} for p in ("train", "validation")},
                          "tree": {p: {k: row[p][k] for k in KEYS} for p in ("train", "validation")}}
    return symbol, result

if __name__ == "__main__":
    with Pool(5) as pool:
        out = dict(pool.map(run, SYMBOLS))
    OUTPUT.write_text(json.dumps(out, indent=2, allow_nan=False))
    print(f"{'family':16} {'side':6} {'n':>5} {'meanR':>8} {'identical_pairs':>16}")
    for family in out[SYMBOLS[0]]:
        for side in ("legacy", "tree"):
            n = sum(out[s][family][side][p]["resolved"] for s in SYMBOLS for p in ("train", "validation"))
            total = sum((out[s][family][side][p]["mean_r"] or 0) * out[s][family][side][p]["resolved"]
                        for s in SYMBOLS for p in ("train", "validation"))
            same = sum(out[s][family]["legacy"] == out[s][family]["tree"] for s in SYMBOLS)
            print(f"{family:16} {side:6} {n:5} {total / n if n else float('nan'):8.3f} {same if side == 'tree' else '':>16}")
