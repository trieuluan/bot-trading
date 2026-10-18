"""Replay 340d MEXC history on the rule_tree population for all 10 pairs and save reports into DB."""
import copy
import json
import logging
import pickle
import time
from multiprocessing import Pool
from pathlib import Path
from uuid import uuid4

from backend.app import futures_rule_tree, futures_strategies, strategy
from backend.app.db import Session
from backend.app.futures_history import replay
from backend.app.futures_learner import (
    evolve,
    read_state,
    save_state,
    sync_seeds,
)
from backend.app.futures_strategies import exit_policy_enabled
from backend.app.futures_symbols import SYMBOLS, symbol_scope
from backend.app.paper import lock_wallet

CACHE = Path("/app/research-output/mexc_10pairs_340d_compare.pkl")
OUTPUT = Path("/app/research-output/futures-rule-tree-340d.json")
KEYS = ("signals", "resolved", "mean_r", "profit_factor")

_orig, _memo = strategy.aggregate, {}

def memo_aggregate(candles, seconds):
    key = (candles[0].time, candles[-1].time, len(candles), seconds) if candles else None
    if key not in _memo:
        if len(_memo) > 200:
            _memo.clear()
        _memo[key] = _orig(candles, seconds)
    return _memo[key]

def setup_memo():
    import backend.app.futures_rules as rules_module
    for module in (strategy, futures_rule_tree, rules_module, futures_strategies):
        if hasattr(module, "aggregate"):
            module.aggregate = memo_aggregate

def run_pair(symbol):
    setup_memo()
    now = int(time.time())
    with symbol_scope(symbol):
        with Session.begin() as session:
            lock_wallet(session)
            state = read_state(session, now)
            sync_seeds(state, now)
            candidates = copy.deepcopy({
                name: candidate
                for name, candidate in state["candidates"].items()
                if exit_policy_enabled(candidate["genome"])
            })
            save_state(session, state)

    start, end, candles, fair, funding = pickle.loads(CACHE.read_bytes())[symbol]
    t0 = time.time()
    report = replay(candidates, candles, fair, funding, start, end)
    duration = time.time() - t0

    job_id = str(uuid4())
    job = {
        "id": job_id,
        "status": "completed",
        "phase": "done",
        "days": 340,
        "started_at": now,
        "updated_at": int(time.time()),
        "completed": report["bars"],
        "total": report["bars"],
        "error": None,
        "report": report,
    }

    with symbol_scope(symbol):
        with Session.begin() as session:
            lock_wallet(session)
            state = read_state(session, int(time.time()))
            state["historical"] = job
            made = evolve(state, int(time.time()), max_children=4, force=True)
            state["historical"]["children"] = made
            save_state(session, state)

    return symbol, duration, report

if __name__ == "__main__":
    t_start = time.time()
    print(f"Starting 340-day replay for {len(SYMBOLS)} pairs on rule_tree population...", flush=True)
    with Pool(4) as pool:
        results = pool.map(run_pair, SYMBOLS)

    out = {}
    print("\nReplay 340d Results Summary:", flush=True)
    print(f"{'Symbol':12} {'Time':>6} {'Candidates':>11} {'Resolved':>9} {'Priors Eligible':>16}", flush=True)
    print("-" * 60, flush=True)
    for sym, dur, rep in results:
        out[sym] = rep
        resolved = sum(r["train"]["resolved"] + r["validation"]["resolved"] for r in rep["strategies"])
        print(f"{sym:12} {dur:5.1f}s {len(rep['strategies']):11} {resolved:9} {rep['eligible_priors']:16}", flush=True)

    OUTPUT.write_text(json.dumps(out, indent=2, allow_nan=False))
    print("-" * 60, flush=True)
    print(f"Completed all 10 pairs in {time.time() - t_start:.1f}s. Report written to {OUTPUT}", flush=True)
