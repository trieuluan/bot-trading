"""Cross-sectional funding study: at each settlement, rank 10 coins by funding (raw and vs own 30d mean),
forward return minus basket mean. Market-neutral, so trend regime does not leak in. Research only.

python -m scripts.funding_xsec
"""
import bisect
import pickle
from collections import defaultdict
from statistics import fmean, pstdev

from backend.app.futures_learner import FEE, SLIP

PATH = "research-output/futures-nov01/mexc_10pairs_340d_compare.pkl"
HORIZONS = (32, 96, 288)  # 8h, 24h, 72h in M15 bars

def tstat(x):
    return fmean(x) / (pstdev(x) / len(x) ** .5) if len(x) > 2 and pstdev(x) else 0.0

def main():
    raw = pickle.loads(open(PATH, "rb").read())
    first = max(v[2][0].time for v in raw.values()); last = min(v[2][-1].time for v in raw.values())
    split = first + int((last - first) * 0.7)
    opens = {s: {r.time: r.open for r in v[2]} for s, v in raw.items()}
    rate = {s: {f["time"]: f["rate"] for f in v[4]} for s, v in raw.items()}
    stamps = sorted(set.intersection(*(set(r) for r in rate.values())))
    hist = defaultdict(list)
    spread = defaultdict(list)  # (signal, h, part) -> bottom2 minus top2 forward return
    for t in stamps:
        if t < first:
            for s in raw: hist[s].append(rate[s][t])
            continue
        sig = {}
        for s in raw:
            past = hist[s][-90:]  # 30d at 8h cycle, strictly before t
            sig[s] = (rate[s][t], rate[s][t] - fmean(past) if len(past) >= 30 else None)
            hist[s].append(rate[s][t])
        part = "train" if t < split else "test"
        for h in HORIZONS:
            end = t + h * 900
            if end > last or any(t not in opens[s] or end not in opens[s] for s in raw):
                continue
            ret = {s: opens[s][end] / opens[s][t] - 1 for s in raw}
            mean = fmean(ret.values())
            for k, name in ((0, "raw"), (1, "vs30d")):
                ranked = sorted((sig[s][k], s) for s in raw if sig[s][k] is not None)
                if len(ranked) < 10 or ranked[0][0] == ranked[-1][0]:
                    continue
                low = fmean(ret[s] for _, s in ranked[:2]) - mean
                high = fmean(ret[s] for _, s in ranked[-2:]) - mean
                spread[name, h, part].append(low - high)  # long low-funding, short high-funding
    cost = 4 * (FEE + SLIP) / 2  # 2 legs per side of the spread, each round trip; per unit gross
    print(f"spread = long 2 lowest funding - short 2 highest, basket-neutral; round-trip cost ~{cost*1e4:.0f}bp")
    for name in ("raw", "vs30d"):
        for h in HORIZONS:
            cells = []
            for part in ("train", "test"):
                x = spread[name, h, part]
                cells.append(f"{part} n={len(x):4} gross={fmean(x)*1e4 if x else 0:+6.1f}bp t={tstat(x):+5.2f} hit={sum(v>0 for v in x)/len(x) if x else 0:.0%}")
            print(f"{name:6} h={h*15//60:3}h | " + " | ".join(cells))

if __name__ == "__main__":
    main()
