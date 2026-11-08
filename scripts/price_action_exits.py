"""bos_retest + H1-swing stop under several exits, vs random control. Research only, no DB/bot writes.

python -m scripts.price_action_exits --history research-output/price-action/vps-history.pkl \
    --recent research-output/price-action/vps-recent.json
"""

import argparse
import random
from collections import defaultdict

from backend.app.futures_learner import FEE, SLIP
from scripts.price_action_replay import load, replay

KINDS = ("bos_retest", "random_ctrl")
EXITS = ("fixed2", "fixed3", "be1_t2", "half1_t3", "trail")
from scripts.price_action_replay import MAX_BARS

def leg(entry, price, direction):
    exit_price = price * (1 - direction * SLIP)
    return direction * (exit_price - entry) - (entry + exit_price) * FEE

def exit_r(rows, index, direction, stop, mode):
    """Net R per unit initial risk. Stop checked before target/trail on a shared bar."""
    entry = rows[index].open * (1 + direction * SLIP)
    risk = direction * (entry - stop)
    if risk <= 0:
        return None
    one_r = entry + direction * risk
    target = {"fixed2": 2, "fixed3": 3, "be1_t2": 2, "half1_t3": 3}.get(mode)
    end = min(len(rows) - 1, index + MAX_BARS - 1)
    live, banked, size, best = stop, 0.0, 1.0, entry
    moved = False
    for cursor in range(index, end + 1):
        row = rows[cursor]
        if row.low <= live if direction == 1 else row.high >= live:
            return banked + size * leg(entry, live, direction) / risk
        if target and (row.high >= entry + direction * target * risk if direction == 1
                       else row.low <= entry + direction * target * risk):
            return banked + size * leg(entry, entry + direction * target * risk, direction) / risk
        reached = row.high >= one_r if direction == 1 else row.low <= one_r
        if reached and not moved and mode in ("be1_t2", "half1_t3", "trail"):
            moved, live = True, entry
            if mode == "half1_t3":
                banked, size = 0.5 * leg(entry, one_r, direction) / risk, 0.5
        best = max(best, row.high) if direction == 1 else min(best, row.low)
        if mode == "trail" and moved:
            live = max(live, best - risk) if direction == 1 else min(live, best + risk)
    return banked + size * leg(entry, rows[end].close, direction) / risk

def interval(values, stamps, seed=1):
    """Mean and 95% CI, bootstrap by UTC day to respect overlapping trades."""
    days = defaultdict(list)
    for value, stamp in zip(values, stamps):
        days[stamp // 86400].append(value)
    groups, rng, means = list(days.values()), random.Random(seed), []
    for _ in range(1000):
        pick = [rng.choice(groups) for _ in groups]
        total = sum(len(g) for g in pick)
        means.append(sum(sum(g) for g in pick) / total)
    means.sort()
    return sum(values) / len(values), means[25], means[974]

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history", required=True)
    parser.add_argument("--recent")
    args = parser.parse_args()
    data = load(args.history, args.recent)
    first, last = min(r[0].time for r in data.values()), max(r[-1].time for r in data.values())
    split = first + int((last - first) * 0.7)
    rows_out = defaultdict(list)
    for symbol, rows in data.items():
        for kind, gate, stop_mode, stamp, side, _, (index, direction, stop) in replay(rows):
            if kind not in KINDS or stop_mode != "h1_swing":
                continue
            for mode in EXITS:
                value = exit_r(rows, index, direction, stop, mode)
                if value is not None:
                    rows_out[kind, gate, mode].append((stamp, symbol, value))
    print(f"{'setup':12} {'gate':4} {'exit':9} | {'all n':>5} {'mean':>6} {'CI95':>15} | {'train':>6} {'test n':>6} {'test':>6} | sym>0")
    for (kind, gate, mode), items in sorted(rows_out.items()):
        values, stamps = [i[2] for i in items], [i[0] for i in items]
        mean, low, high = interval(values, stamps)
        train = [i[2] for i in items if i[0] < split]
        test = [i[2] for i in items if i[0] >= split]
        by_symbol = defaultdict(list)
        for _, symbol, value in items:
            by_symbol[symbol].append(value)
        positive = sum(sum(v) > 0 for v in by_symbol.values())
        print(f"{kind:12} {gate:4} {mode:9} | {len(values):5} {mean:+6.3f} [{low:+.3f},{high:+.3f}] | "
              f"{sum(train)/len(train):+6.3f} {len(test):6} {sum(test)/len(test):+6.3f} | {positive}/{len(by_symbol)}")

if __name__ == "__main__":
    main()
