"""Replay rule_tree candidates on 340d of 15m MEXC history, all pairs. Read-only; no orders, no state writes."""
import json, time
from pathlib import Path
from backend.app.futures_history import WARMUP, load_history, replay
from backend.app.futures_learner import new_state
from backend.app.futures_strategies import is_rule_tree
from backend.app.futures_symbols import SYMBOLS
from backend.app.mexc import MexcFuturesAdapter

DAYS = 340
cands = {n: {"genome": c["genome"]} for n, c in new_state(int(time.time()))["candidates"].items() if is_rule_tree(c["genome"])}
out = {}
for sym in SYMBOLS:
    try:
        a = MexcFuturesAdapter(symbol=sym)
        now = a.server_time(); end = now // 900 * 900; start = end - DAYS * 86400
        c = load_history(a, start - WARMUP * 900, end, now)
        f = load_history(a, start - WARMUP * 900, end, now, fair=True)
        fund = a.funding_history(start - WARMUP * 900 - 2 * 86400, end)
        a.close()
        out[sym] = r = replay(cands, c, f, fund, start, end)
        for row in r["strategies"]:
            fmt = lambda p: f'n={p["resolved"]} meanR={None if p["mean_r"] is None else round(p["mean_r"], 3)} pf={None if p["profit_factor"] is None else round(p["profit_factor"], 2)}'
            print(sym, row["strategy"], "train", fmt(row["train"]), "| val", fmt(row["validation"]), "| eligible", row["prior_eligible"], flush=True)
    except Exception as e:
        print(sym, "ERROR", e, flush=True)
Path("/app/research-output/futures-rule-tree-340d.json").write_text(json.dumps(out, indent=2, allow_nan=False))
print("DONE", flush=True)
