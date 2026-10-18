"""Evolve adaptive rule_trees on 340d replay: select on train only, report held-out validation."""
import json
import pickle
import random
import time
from multiprocessing import Pool
from pathlib import Path

from backend.app.db import Session
from backend.app.futures_history import replay
from backend.app.futures_learner import read_state
from backend.app.futures_strategies import crossover, genome_name, mutate
from backend.app.futures_symbols import SYMBOLS, symbol_scope
from scripts.futures_replay_340d import CACHE, setup_memo

OUTPUT = Path("/app/research-output/futures-evolve-adaptive.json")
GENERATIONS, CHILDREN, MIN_TRAIN, TOP_N = 4, 16, 30, 5

def sound(row, part, factor):
    stat = row[part]
    return (stat["resolved"] >= MIN_TRAIN and (stat["mean_r"] or 0) > 0
            and (stat.get("no_losses", False) or (stat["profit_factor"] or 0) > factor))

def top_candidates(rows, n=TOP_N):
    with_train = [r for r in rows if r["train"]["resolved"] >= 15 and r["train"]["mean_r"] is not None]
    with_val = [r for r in rows if r["validation"]["resolved"] >= 15 and r["validation"]["mean_r"] is not None]
    return {
        "by_train": sorted(with_train, key=lambda r: r["train"]["mean_r"], reverse=True)[:n],
        "by_validation": sorted(with_val, key=lambda r: r["validation"]["mean_r"], reverse=True)[:n],
    }

def training_parents(rows):
    ranked = sorted((row for row in rows if row["train"]["resolved"] >= MIN_TRAIN
                     and row["train"]["mean_r"] is not None),
                    key=lambda row: row["train"]["mean_r"], reverse=True)
    return [row["genome"] for row in ranked[:6]]

def run_pair(symbol):
    setup_memo()
    with symbol_scope(symbol), Session.begin() as session:
        state = read_state(session, int(time.time()))
        report = state["historical"]["report"]
    seen = {row["strategy"]: row for row in report["strategies"]
            if row["genome"].get("exit_policy") == "adaptive"}
    start, end, candles, fair, funding = pickle.loads(CACHE.read_bytes())[symbol]
    rng = random.Random(symbol)
    for _ in range(GENERATIONS):
        # ponytail: train-only fitness, validation never steers selection; add walk-forward folds if edge appears
        parents = training_parents(seen.values())
        children = {}
        for _ in range(CHILDREN * 4):
            if len(children) >= CHILDREN or not parents:
                break
            genome = mutate({**crossover(rng.choice(parents), rng.choice(parents)), "exit_policy": "adaptive"}, rng)
            genome["exit_policy"] = "adaptive"
            name = genome_name(genome)
            if name not in seen and name not in children:
                children[name] = {"genome": genome}
        if not children:
            break
        for row in replay(children, candles, fair, funding, start, end)["strategies"]:
            seen[row["strategy"]] = row
    all_rows = list(seen.values())
    survivors = [r for r in all_rows if sound(r, "train", 1.0) and sound(r, "validation", 1.1)]
    return symbol, len(seen), survivors, top_candidates(all_rows)

if __name__ == "__main__":
    with Pool(4) as pool:
        results = pool.map(run_pair, SYMBOLS)
    out = {}
    for symbol, tested, survivors, top in results:
        out[symbol] = {
            "tested": tested,
            "survivors": survivors,
            "top_train": top["by_train"],
            "top_validation": top["by_validation"],
        }
        print(f"=== {symbol} (tested={tested}, survivors={len(survivors)}) ===", flush=True)
        print("  Top Train:", flush=True)
        for r in top["by_train"][:3]:
            t, v = r["train"], r["validation"]
            pf_t = f"{t['profit_factor']:.2f}" if t.get("profit_factor") is not None else "N/A"
            pf_v = f"{v['profit_factor']:.2f}" if v.get("profit_factor") is not None else "N/A"
            print(f"    {r['strategy'][:28]} Tr: N={t['resolved']} R={t['mean_r']:+.3f} PF={pf_t} | Val: N={v['resolved']} R={v.get('mean_r') or 0:+.3f} PF={pf_v}", flush=True)
        print("  Top Validation:", flush=True)
        for r in top["by_validation"][:3]:
            t, v = r["train"], r["validation"]
            pf_t = f"{t['profit_factor']:.2f}" if t.get("profit_factor") is not None else "N/A"
            pf_v = f"{v['profit_factor']:.2f}" if v.get("profit_factor") is not None else "N/A"
            print(f"    {r['strategy'][:28]} Tr: N={t['resolved']} R={t.get('mean_r') or 0:+.3f} PF={pf_t} | Val: N={v['resolved']} R={v['mean_r']:+.3f} PF={pf_v}", flush=True)
    OUTPUT.write_text(json.dumps(out, indent=1, allow_nan=False))
