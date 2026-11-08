"""Gene-level audit of Futures learner samples and Auto Paper trades. Read-only, local file input.

python -m scripts.futures_gene_audit research-output/gene-audit/vps-learning.json
Input: {"trades", "decisions", "states": {id: {samples, sample_archive, candidates, rule_archive}}}.
"""

import json
import random
import sys
from collections import defaultdict

from backend.app.futures_rule_tree import is_htf_rule

def genes(genome):
    """Flat gene list; HTF slots split so each H4/H1/M15 feature is counted separately."""
    out = [("entry", genome.get("entry_policy", "market")), ("exit", genome.get("exit_policy", "fixed"))]
    rule = genome.get("rule") or {}
    if is_htf_rule(rule):
        h4, h1, m15 = rule["children"]
        for node in h4.get("children", [h4])[1:]:
            out.append(("h4_confirm", node["feature"]))
        for frame, node in (("h1", h1), ("m15", m15)):
            stack = [node]
            while stack:
                item = stack.pop()
                if "feature" in item:
                    out.append((frame, item["feature"]))
                else:
                    stack += item["children"]
    return sorted(set(out))

def interval(rows, seed=7):
    """Mean R and 95% CI, bootstrap by UTC day (overlapping trades share a day)."""
    days = defaultdict(list)
    for stamp, value in rows:
        days[stamp // 86400].append(value)
    groups, rng, means = list(days.values()), random.Random(seed), []
    for _ in range(1000):
        pick = [rng.choice(groups) for _ in groups]
        means.append(sum(map(sum, pick)) / sum(map(len, pick)))
    means.sort()
    return sum(v for _, v in rows) / len(values := [v for _, v in rows]), means[25], means[974]

def collect(payload):
    learn, events, paper = [], defaultdict(list), []
    for state_id, state in payload["states"].items():
        known = {**state["rule_archive"], **state["candidates"]}
        for sample in [*(state.get("sample_archive") or []), *state["samples"]]:
            genome = (known.get(sample["strategy"]) or {}).get("genome")
            if sample["status"] == "resolved" and genome and sample.get("net_r") is not None:
                items = genes(genome)
                learn.append((sample["candle_time"], sample["net_r"], items))
                for gene in items:
                    events[gene, state_id, sample["candle_time"], sample["side"]].append(sample["net_r"])
    dedup = [(stamp, sum(vals) / len(vals), [gene]) for (gene, _, stamp, _), vals in events.items()]
    trades = {row["id"]: row for row in payload["trades"]}
    for row in payload["decisions"]:
        audit = (row["parameters"] or {}).get("audit") or {}
        trade = trades.get(row["trade_id"])
        if not trade or trade["net_pnl"] is None or not audit.get("genome") or not audit.get("risk_budget"):
            continue
        paper.append((row["candle_time"], trade["net_pnl"] / audit["risk_budget"], genes(audit["genome"])))
    return learn, dedup, paper

def report(name, rows, minimum=15):
    if not rows:
        print(f"\n== {name}: no rows")
        return
    mean, low, high = interval([(t, r) for t, r, _ in rows])
    print(f"\n== {name}: n={len(rows)} days={len({t // 86400 for t, _, _ in rows})} "
          f"mean={mean:+.3f}R CI95=[{low:+.3f},{high:+.3f}] win={sum(r > 0 for _, r, _ in rows) / len(rows):.1%}")
    by_gene = defaultdict(list)
    for stamp, value, items in rows:
        for gene in items:
            by_gene[gene].append((stamp, value))
    print(f"{'gene':34} {'n':>5} {'mean':>7} {'CI95':>17} {'others':>7}")
    total, count = sum(r for _, r, _ in rows), len(rows)
    for gene, items in sorted(by_gene.items(), key=lambda kv: -sum(v for _, v in kv[1]) / len(kv[1])):
        if len(items) < minimum or len(items) == count:
            continue
        mean, low, high = interval(items)
        others = (total - sum(v for _, v in items)) / (count - len(items))
        flag = " +" if low > others else " -" if high < others else ""
        print(f"{gene[0] + '=' + gene[1]:34} {len(items):5} {mean:+7.3f} [{low:+.3f},{high:+.3f}] {others:+7.3f}{flag}")

def main():
    learn, dedup, paper = collect(json.load(open(sys.argv[1])))
    report("learner samples (raw)", learn, minimum=20)
    report("learner unique setups per gene (symbol+candle+side)", dedup, minimum=10)
    report("auto paper trades (R = net_pnl / risk_budget)", paper, minimum=10)

if __name__ == "__main__":
    main()
