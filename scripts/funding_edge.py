"""Funding-state edge study on 340d MEXC data. Research only.

python -m scripts.funding_edge
A) raw forward return after funding settlement by funding bucket (long side, net of round-trip cost)
B) price_action_replay setups (h1_swing stop, 2R) split by funding at signal time (last settled rate)
"""
import bisect
import pickle
from collections import defaultdict
from statistics import fmean, pstdev

from backend.app.futures_learner import FEE, SLIP
from scripts.price_action_replay import replay, stats

PATH = "research-output/futures-nov01/mexc_10pairs_340d_compare.pkl"
COST = 2 * (FEE + SLIP)
BUCKETS = (("<=-3bp", -1, -0.0003), ("-3..-1bp", -0.0003, -0.0001), ("-1..0.5bp", -0.0001, 0.00005), (">=0.5bp", 0.00005, 1))

def bucket_of(rate):
    return next(name for name, lo, hi in BUCKETS if lo < rate <= hi or (lo == -1 and rate <= hi))

def main():
    raw = pickle.loads(open(PATH, "rb").read())
    first = max(v[2][0].time for v in raw.values()); last = min(v[2][-1].time for v in raw.values())
    split = first + int((last - first) * 0.7)
    fwd = defaultdict(list)
    for symbol, v in raw.items():
        rows, funding = v[2], v[4]
        times = [r.time for r in rows]
        for rec in funding:
            i = bisect.bisect_left(times, rec["time"])
            if i >= len(rows) - 100 or rows[i].time != rec["time"] or rec["time"] < first:
                continue
            for h in (32, 96):  # 8h, 24h
                ret = rows[i + h].close / rows[i].open - 1
                part = "train" if rec["time"] < split else "test"
                fwd[bucket_of(rec["rate"]), h, part].append(ret - COST)
    print("A) LONG net return per settlement (bps), by funding bucket")
    for name, *_ in BUCKETS:
        for h in (32, 96):
            cells = []
            for part in ("train", "test"):
                x = fwd[name, h, part]
                cells.append(f"{part} n={len(x):5} mean={fmean(x)*1e4 if x else 0:+7.1f}bp t={fmean(x)/(pstdev(x)/len(x)**.5) if len(x)>2 else 0:+5.2f}")
            print(f"  {name:10} h={h*15//60:3}h | " + " | ".join(cells))
    print("B) price-action R (h1_swing, 2R) by funding at signal, side-aligned")
    out = defaultdict(list)
    for symbol, v in raw.items():
        rows = [r for r in v[2] if r.time >= first]
        funding = v[4]
        ft = [f["time"] for f in funding]
        for kind, gate, stop_mode, stamp, side, results, _ in replay(rows):
            if gate != "all" or stop_mode != "h1_swing" or kind == "random_ctrl":
                continue
            j = bisect.bisect_right(ft, stamp + 900) - 1
            if j < 0:
                continue
            rate = funding[j]["rate"]
            d = 1 if side == "long" else -1
            state = "crowd_against" if d * rate < -0.00005 else ("crowd_with" if d * rate > 0.00005 else "neutral")
            part = "train" if stamp < split else "test"
            out[state, part].append(results[2.0])
            if state == "crowd_against" and abs(rate) >= 0.0002:
                out["strong_against", part].append(results[2.0])
    for state in ("crowd_against", "strong_against", "neutral", "crowd_with"):
        a, b = stats(out[state, "train"]), stats(out[state, "test"])
        if a["n"] and b["n"]:
            print(f"  {state:15} train n={a['n']:5} {a['mean_r']:+.3f} | test n={b['n']:5} {b['mean_r']:+.3f} win={b['win']:.1%} pf={b['pf'] or 0:.2f}")

if __name__ == "__main__":
    main()
