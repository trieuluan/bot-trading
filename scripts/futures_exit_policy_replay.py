"""Replay 340d: every G0 seed x every evolvable exit policy, report mean R per policy (no DB writes)."""
import json
import pickle
import time
from multiprocessing import Pool
from pathlib import Path

from backend.app import futures_history
from backend.app.futures_exits import EVOLVABLE_EXITS
from backend.app.futures_strategies import RULE_SEEDS, genome_name, validate_genome
from backend.app.futures_symbols import SYMBOLS
import scripts.futures_replay_340d as base

OUTPUT = Path("/app/research-output/futures-exit-policies-340d.json")
PAIR_DIR = Path("/app/research-output/exit-policy-pairs")

def variants():
    result = {}
    for seed in RULE_SEEDS:
        for policy in EVOLVABLE_EXITS:
            genome = {key: value for key, value in seed.items() if key != "exit_policy"}
            if policy != "fixed":
                genome["exit_policy"] = policy
            else:
                genome.update(target_r=2.0, stop_buffer=0.0)
            try:
                validate_genome(genome)
            except ValueError:
                continue
            result[genome_name(genome)] = {"genome": genome}
    return result

def run(symbol):
    base.setup_memo()
    data = pickle.loads(base.CACHE.read_bytes())
    first = max(data[s][2][0].time for s in SYMBOLS)
    last = min(data[s][2][-1].time for s in SYMBOLS)
    clip = lambda rows: [bar for bar in rows if first <= bar.time <= last]
    start, end, candles, fair, funding = data[symbol]
    start, end = max(start, first + futures_history.WARMUP * 900), last + 900
    btc = clip(data["BTC_USDT"][2]) if symbol != "BTC_USDT" else None
    peers = {s: clip(data[s][2]) for s in SYMBOLS if s != "BTC_USDT"} if symbol == "BTC_USDT" else None
    t0 = time.time()
    report = futures_history.replay(variants(), clip(candles), clip(fair), funding, start, end,
                                    btc_candles=btc, benchmarks=peers)
    duration = time.time() - t0
    print(f"{symbol} finished in {duration:.0f}s", flush=True)
    PAIR_DIR.mkdir(parents=True, exist_ok=True)
    (PAIR_DIR / f"{symbol}.json").write_text(json.dumps(report, allow_nan=False))
    out = {}
    for row in report["strategies"]:
        policy = row["genome"].get("exit_policy", "fixed")
        for part in ("train", "validation"):
            stats = out.setdefault(policy, {}).setdefault(part, {"n": 0, "sum_r": 0.0})
            resolved = row[part]["resolved"]
            mean_r = row[part]["mean_r"]
            stats["n"] += resolved
            if resolved > 0 and mean_r is not None:
                stats["sum_r"] += resolved * mean_r
    return symbol, out

if __name__ == "__main__":
    with Pool(4) as pool:
        results = pool.map(run, SYMBOLS)
    total = {}
    for _, out in results:
        for policy, parts in out.items():
            for part, stats in parts.items():
                agg = total.setdefault(policy, {}).setdefault(part, {"n": 0, "sum_r": 0.0})
                agg["n"] += stats["n"]
                agg["sum_r"] += stats["sum_r"]
    OUTPUT.write_text(json.dumps({"total": total, "pairs": dict(results)}, indent=2))
    print(f"{'policy':22}{'train n':>9}{'train R':>9}{'valid n':>9}{'valid R':>9}")
    for policy, parts in sorted(total.items(), key=lambda kv: -kv[1]["validation"]["sum_r"] / max(kv[1]["validation"]["n"], 1)):
        t, v = parts["train"], parts["validation"]
        print(f"{policy:22}{t['n']:9}{t['sum_r'] / max(t['n'], 1):9.3f}{v['n']:9}{v['sum_r'] / max(v['n'], 1):9.3f}")
