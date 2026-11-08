"""H4 trend filter + M15 sweep entry vs production, $100 account, 10x vs per-pair max leverage.

Run: /tmp/btv/bin/python -m scripts.mtf_m15_replay
Research only. Reused history: exploratory, not proof.
ponytail: cross margin, no liquidation/margin-tier model; per-pair cap = lowest max leverage (200x).
"""

import bisect
import json
from collections import defaultdict

from scripts import fee_edge_replay as edge
from scripts import htf_portfolio_replay as native
from scripts import small_capital_replay as sc
from scripts.price_action_replay import atr, bucket, ema


def mtf_signals(symbol, rows, lookback=16, min_stop_atr=0.5):
    h4 = bucket(rows, 14400)
    closes_at = [b.time + 14400 for b in h4]
    fast, slow = ema([b.close for b in h4], 20), ema([b.close for b in h4], 50)
    highs, lows, ranges = [b.high for b in rows], [b.low for b in rows], atr(rows)
    for i in range(240, len(rows) - 1):
        bar, stamp = rows[i], rows[i].time + 900
        k = bisect.bisect_right(closes_at, stamp) - 1
        if k < 50:
            continue
        for d in (1, -1):
            if d * (h4[k].close - slow[k]) <= 0 or d * (fast[k] - slow[k]) <= 0 or d * (bar.close - bar.open) <= 0:
                continue
            hi, lo = max(highs[i - lookback:i]), min(lows[i - lookback:i])
            if not (bar.low < lo < bar.close if d == 1 else bar.close < hi < bar.high):
                continue
            swing = min(lows[i - 4:i + 1]) if d == 1 else max(highs[i - 4:i + 1])
            dist = max(d * (bar.close - swing) + 0.25 * ranges[i], min_stop_atr * ranges[i])
            yield dict(symbol=symbol, seed="mtf_m15_sweep", entry_idx=i + 1, entry_time=stamp,
                       direction=d, stop=bar.close - d * dist, max_m15_bars=32)


def main():
    data, first, end, _ = native.load_data()
    fees = json.loads(edge.FEES.read_text())["fees"]
    folds, days = native.build_folds(first, end), (end - first) / 86400
    sigs = [x for s, p in sorted(data.items()) for x in mtf_signals(s, p[2])]
    print("signals", len(sigs))
    for tp, mode in ((1.5, "fixed"), (2, "fixed"), (3, "fixed"), (2, "be")):
        t = edge.replay(data, sigs, fees, folds, tp, mode, 1, "all")
        m, s = edge.metrics(t), edge.metrics(edge.replay(data, sigs, fees, folds, tp, mode, 1, "all", stress=2))
        fr = [round(edge.metrics([x for x in t if x["fold"] == f])["mean_r"] or 0, 3) for f, _, _ in folds]
        print(f"tp{tp}_{mode} n={m['n']} R={m['mean_r']:.3f} costR={m['mean_fee_r']+m['mean_funding_r']:.3f} "
              f"2xR={s['mean_r']:.3f} folds={fr}")
        for cap in (10, 200):
            sc.LEVERAGE_CAP = cap
            print("   cap", cap, "x $100:", sc.account(t, 100, days))


if __name__ == "__main__":
    main()
