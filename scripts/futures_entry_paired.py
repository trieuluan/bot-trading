"""Paired body50-vs-market analysis on saved g0-entry samples. Research only."""
import json
import random
import sys
from collections import defaultdict
from pathlib import Path

FEE = 8 / 10000
def value(s, maker):
    if s["status"] != "resolved":
        return 0.0
    r = s["net_r"] * s["risk"] / s["reference_risk"]
    return r + s["entry"] * (FEE - maker) / s["reference_risk"] * (s["arm"] != "market")

def main(path, arm="body50"):
    by = defaultdict(dict)
    for f in Path(path).glob("*_USDT.json"):
        for s in json.loads(f.read_text())["samples"]:
            if s["status"] in ("resolved", "cancelled"):
                by[(s["symbol"], s["candle_time"], s["template"])][s["arm"]] = s
    keys = [k for k, v in by.items() if "market" in v and arm in v and v["market"]["status"] == "resolved"]
    for maker in (FEE, 0.0002, 0.0):
        for part in ("train", "validation"):
            ks = [k for k in keys if by[k]["market"]["part"] == part]
            d = [(k, value(by[k][arm], maker) - value(by[k]["market"], FEE)) for k in ks]
            cl = defaultdict(list)
            for k, x in d:
                cl[k[1] // 14400].append(x)
            groups = list(cl.values())
            random.seed(1)
            boots = sorted(sum(sum(random.choice(groups)) for _ in groups) / len(d) for _ in range(2000))
            sym = defaultdict(list)
            for k, x in d:
                sym[k[0]].append(x)
            pos = sum(sum(v) > 0 for v in sym.values())
            print(f"maker={maker:.4f} {part:10} n={len(d)} diff/signal={sum(x for _, x in d)/len(d):+.4f} "
                  f"ci95=[{boots[50]:+.3f},{boots[1950]:+.3f}] symbols_better={pos}/10")

if __name__ == "__main__":
    main(*sys.argv[1:])
