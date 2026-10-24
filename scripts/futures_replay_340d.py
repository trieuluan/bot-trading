"""Replay 340d MEXC history on the rule_tree population for all 10 pairs and save reports into DB."""
import copy
import argparse
import json
import logging
import pickle
import time
from multiprocessing import Pool
from pathlib import Path
from uuid import uuid4

from backend.app import futures_history, futures_rule_tree, futures_strategies, strategy
from backend.app.db import Session
from backend.app.futures_history import replay
from backend.app.futures_learner import (
    evolve,
    read_state,
    save_state,
    sync_seeds,
)
from backend.app.futures_strategies import RULE_SEEDS, exit_policy_enabled, genome_name
from backend.app.futures_symbols import SYMBOLS, symbol_scope
from backend.app.paper import lock_wallet

CACHE = Path("/app/research-output/mexc_10pairs_340d_compare.pkl")
OUTPUT = Path("/app/research-output/futures-rule-tree-340d.json")
KEYS = ("signals", "resolved", "mean_r", "profit_factor")

_orig, _memo = strategy.aggregate, {}

def memo_aggregate(candles, seconds):
    key = (tuple(map(id, candles)), seconds)
    if key not in _memo:
        if len(_memo) > 200:
            _memo.clear()
        _memo[key] = (candles, _orig(candles, seconds))
    return _memo[key][1]

def setup_memo():
    _memo.clear()
    import backend.app.futures_rules as rules_module
    for module in (strategy, futures_rule_tree, rules_module, futures_strategies):
        if hasattr(module, "aggregate"):
            module.aggregate = memo_aggregate

def run_pair(symbol, signal_seeds=False):
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
            if not signal_seeds:
                save_state(session, state)

    if signal_seeds:
        candidates = {genome_name(seed): {"genome": seed} for seed in RULE_SEEDS if any(
            node.get("feature") in ("relative_strength", "funding_aligned", "macro_safe", "session_active", "basis_aligned", "oi_surge", "fib_retrace", "rsi_divergence", "fvg", "bos", "session_vwap", "pdh_pdl", "adx_regime", "pdh_pdl_sweep")
            for _, node in futures_rule_tree.nodes(seed["rule"]))}

    data = pickle.loads(CACHE.read_bytes())
    # The cache pulled BTC one bar offset from the other pairs; replay on the common window only.
    first = max(data[sym][2][0].time for sym in SYMBOLS)
    last = min(data[sym][2][-1].time for sym in SYMBOLS)
    def clip(rows):
        return [bar for bar in rows if first <= bar.time <= last]
    start, end, candles, fair, funding = data[symbol]
    start, end = max(start, first + futures_history.WARMUP * 900), last + 900
    candles, fair = clip(candles), clip(fair)
    btc = clip(data["BTC_USDT"][2]) if symbol != "BTC_USDT" else None
    benchmarks = {sym: clip(data[sym][2]) for sym in SYMBOLS if sym != symbol}
    t0 = time.time()
    report = replay(candidates, candles, fair, funding, start, end, btc_candles=btc, benchmarks=benchmarks)
    duration = time.time() - t0
    print(f"{symbol}: replay finished in {duration:.1f}s", flush=True)
    if signal_seeds:
        return symbol, duration, report

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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--signal-seeds", action="store_true",
                        help="Replay signal seeds without replacing learner history")
    args = parser.parse_args()
    output = (OUTPUT.with_name("futures-signal-seeds-340d.json")
              if args.signal_seeds else OUTPUT)
    t_start = time.time()
    print(f"Starting 340-day replay for {len(SYMBOLS)} pairs on rule_tree population...", flush=True)
    with Pool(4) as pool:
        results = pool.starmap(run_pair, [(symbol, args.signal_seeds) for symbol in SYMBOLS])

    out = {}
    print("\nReplay 340d Results Summary:", flush=True)
    print(f"{'Symbol':12} {'Time':>6} {'Candidates':>11} {'Resolved':>9} {'Priors Eligible':>16}", flush=True)
    print("-" * 60, flush=True)
    for sym, dur, rep in results:
        out[sym] = rep
        resolved = sum(r["train"]["resolved"] + r["validation"]["resolved"] for r in rep["strategies"])
        print(f"{sym:12} {dur:5.1f}s {len(rep['strategies']):11} {resolved:9} {rep['eligible_priors']:16}", flush=True)

    output.write_text(json.dumps(out, indent=2, allow_nan=False))
    print("-" * 60, flush=True)
    print(f"Completed all 10 pairs in {time.time() - t_start:.1f}s. Report written to {output}", flush=True)
