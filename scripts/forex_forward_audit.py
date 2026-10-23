"""Read-only Forex forward audit. Usage: python forex_forward_audit.py research-output > report.json"""
import glob
import json
import statistics as st
import sys
import time
from collections import Counter, defaultdict

root = sys.argv[1] if len(sys.argv) > 1 else "research-output"
now = int(time.time())

def stats(v):
    if not v:
        return {"n": 0}
    w = [x for x in v if x > 0]
    loss = [-x for x in v if x < 0]
    return {"n": len(v), "mean_r": round(st.mean(v), 4), "total_r": round(sum(v), 3),
            "win": round(len(w) / len(v), 3), "pf": round(sum(w) / sum(loss), 3) if loss else None,
            "lower95": round(st.mean(v) - 1.96 * st.stdev(v) / len(v) ** .5, 4) if len(v) > 1 else None}

out = {"as_of": now, "ledgers": {}}
for kind in ("ast3", "ast4-entry"):
    rows, per_symbol = [], {}
    for path in sorted(glob.glob(f"{root}/exness-htf-{kind}-shadow-*-*.json")):
        d = json.load(open(path))
        sym = d.get("symbol") or path.rsplit("-", 1)[1][:-5]
        S = d["samples"]
        for s in S:
            s["_sym"] = sym
        rows += S
        per_symbol[sym] = {"since": d.get("since"), "last_capture": d.get("last_capture"),
                           "generation": d.get("generation"), "trees": len(d.get("population", [])),
                           "samples": len(S)}
    res = [s for s in rows if s["status"] == "resolved"]
    def r(s): return s["net_r_ex_swap"]
    # independent setups: unique (symbol, confirm_time, side)
    setups = {(s["_sym"], s["confirm_time"], s["side"]) for s in res}
    uniq = defaultdict(list)
    for s in res:
        uniq[(s["_sym"], s["confirm_time"], s["side"])].append(r(s))
    setup_mean = [st.mean(v) for v in uniq.values()]
    days = sorted({s["confirm_time"] // 86400 for s in res})
    def group(key):
        g = defaultdict(list)
        for s in res:
            g[key(s)].append(r(s))
        return {str(k): stats(v) for k, v in sorted(g.items(), key=lambda x: -len(x[1]))}
    def tree(s):
        return s["tree"]["root"]["children"]
    fam = {}
    if kind == "ast3":
        fam = {"h1_setup": group(lambda s: tree(s)[1]["family"]), "m15_trigger": group(lambda s: tree(s)[2]["family"]),
               "exit": group(lambda s: tree(s)[3]["policy"])}
    by_tree = defaultdict(list)
    for s in res:
        by_tree[s["strategy"]].append(r(s))
    out["ledgers"][kind] = {
        "samples": len(rows), "status": dict(Counter(s["status"] for s in rows)),
        "versions": dict(Counter(s.get("strategy_version") for s in rows)),
        "provenance": dict(Counter(s.get("provenance") for s in rows)),
        "costs_verified": dict(Counter(bool(s.get("costs", {}).get("verified")) for s in rows)),
        "slippage_bps": dict(Counter(s.get("costs", {}).get("slippage_bps") for s in rows)),
        "non_finite_or_missing_r": sum(1 for s in res if not isinstance(s.get("net_r_ex_swap"), (int, float))),
        "resolved_all": stats([r(s) for s in res]),
        "resolved_unique_setups": len(setups), "setup_avg_stats": stats(setup_mean),
        "active_days": len(days), "first_day": days[0] * 86400 if days else None,
        "stuck_over_48h": sum(1 for s in rows if s["status"] in ("waiting", "pending", "open") and now - s.get("recorded_at", now) > 172800),
        "excluded_reasons": dict(Counter(str(s.get("reason") or s.get("execution_result", {}).get("reason")) for s in rows if s["status"] in ("excluded", "invalid", "skipped"))),
        "by_symbol": group(lambda s: s["_sym"]), "by_side": group(lambda s: s["side"]),
        "by_utc_hour": group(lambda s: time.gmtime(s["confirm_time"]).tm_hour),
        "by_tree_ge10": {k: stats(v) for k, v in by_tree.items() if len(v) >= 10},
        "trees_with_resolved": len(by_tree), "trees_eligible_mean_pos_n10": sum(1 for v in by_tree.values() if len(v) >= 10 and st.mean(v) > 0),
        "families": fam, "per_symbol": per_symbol,
    }
    if kind == "ast4-entry":
        out["ledgers"][kind]["by_entry_policy"] = group(lambda s: tree(s)[3]["policy"])
        out["ledgers"][kind]["by_exit_policy"] = group(lambda s: tree(s)[4]["policy"])
        out["ledgers"][kind]["by_h1_setup"] = group(lambda s: tree(s)[1]["family"])
# slippage journal
try:
    out["slippage_report"] = json.load(open(f"{root}/exness-slippage-report.json"))
    out["slippage_journal_lines"] = sum(1 for _ in open(f"{root}/exness-slippage-journal.jsonl"))
except OSError:
    pass
json.dump(out, sys.stdout, indent=1, default=str)
