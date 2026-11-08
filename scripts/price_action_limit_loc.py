"""Replay: Market vs Limit-50%-of-signal-bar entry, with/without HTF location gate. Research only.

python -m scripts.price_action_limit_loc --history research-output/futures-nov01/mexc_10pairs_340d_compare.pkl
Signals come from price_action_replay.replay (closed bar). Location = signal extreme within 1 H1 ATR of
PDH/PDL or H4 Donchian-20. Limit fills on trade-through within 4 bars, no slippage; costs otherwise as live.
"""
import argparse
import bisect
from collections import defaultdict

from backend.app.futures_learner import FEE, SLIP
from scripts.price_action_replay import STOPS, WARMUP, atr, bucket, load, replay, stats, trade

WAIT, MAX_BARS, TARGET = 4, 96, 2.0

def limit_trade(rows, index, direction, stop, level):
    """Signal bar = rows[index-1]. Fill on trade-through; fill bar may stop out, target only afterwards."""
    for cursor in range(index, min(index + WAIT, len(rows))):
        bar = rows[cursor]
        if (bar.low < level) if direction == 1 else (bar.high > level):
            risk = direction * (level - stop)
            if risk <= 0:
                return None
            target = level + direction * TARGET * risk
            if (bar.low <= stop) if direction == 1 else (bar.high >= stop):
                price = stop
            else:
                price = None
                for k in range(cursor + 1, min(len(rows), cursor + MAX_BARS)):
                    row = rows[k]
                    if (row.low <= stop) if direction == 1 else (row.high >= stop):
                        price = stop; break
                    if (row.high >= target) if direction == 1 else (row.low <= target):
                        price = target; break
                else:
                    price = rows[min(len(rows) - 1, cursor + MAX_BARS - 1)].close
            exit_price = price * (1 - direction * SLIP)
            return (direction * (exit_price - level) - (level + exit_price) * FEE) / risk
    return 0.0  # unfilled scores 0R, same as live

def main():
    parser = argparse.ArgumentParser(); parser.add_argument("--history", required=True)
    parser.add_argument("--no-cost", action="store_true", help="FEE=SLIP=0: gross edge")
    args = parser.parse_args()
    if args.no_cost:
        import scripts.price_action_replay as pa
        global FEE, SLIP
        FEE = SLIP = pa.FEE = pa.SLIP = 0.0
    data = load(args.history, None)
    first = min(r[0].time for r in data.values()); last = max(r[-1].time for r in data.values())
    split = first + int((last - first) * 0.7)
    out = defaultdict(list)
    for symbol, rows in data.items():
        h1, h4 = bucket(rows, 3600), bucket(rows, 14400)
        h1_atr = atr(h1)
        h1_times = [r.time + 3600 for r in h1]
        h4_times = [r.time + 14400 for r in h4]
        day = defaultdict(list)
        for r in rows: day[r.time // 86400].append(r)
        hl = {d: (max(r.high for r in g), min(r.low for r in g)) for d, g in day.items() if len(g) == 96}
        pos = {r.time: i for i, r in enumerate(rows)}
        for kind, gate, stop_mode, stamp, side, _, (entry_index, direction, stop) in replay(rows):
            if kind == "random_ctrl" or gate != "all":
                continue
            sig = rows[entry_index - 1]
            k = bisect.bisect_right(h1_times, sig.time + 900) - 1
            m = bisect.bisect_right(h4_times, sig.time + 900) - 1
            if k < 5 or m < 20:
                continue
            extreme = sig.low if direction == 1 else sig.high
            near = 1.0 * h1_atr[k]
            levels = []
            d = hl.get(sig.time // 86400 - 1)
            if d: levels.append(d[1] if direction == 1 else d[0])
            win = h4[m - 19:m + 1]
            levels.append(min(r.low for r in win) if direction == 1 else max(r.high for r in win))
            loc = any(abs(extreme - lv) <= near for lv in levels)
            level = (sig.high + sig.low) / 2
            if direction * (sig.close - level) <= 0:
                continue
            part = "train" if stamp < split else "test"
            mk = trade(rows, entry_index, direction, stop, TARGET)
            lm = limit_trade(rows, entry_index, direction, stop, level)
            if mk is None or lm is None:
                continue
            for loc_tag in ("any", "loc") if loc else ("any",):
                out[stop_mode, "market", loc_tag, part].append(mk[0])
                out[stop_mode, "limit50", loc_tag, part].append(lm)
                out[stop_mode, kind, "mkt:" + loc_tag, part].append(mk[0])
                out[stop_mode, kind, "lim:" + loc_tag, part].append(lm)
                if lm != 0.0:
                    out[stop_mode, "limit50_filled", loc_tag, part].append(lm)
    print(f"{'stop':8} {'entry':14} {'loc':7} | {'train n':>7} {'meanR':>6} | {'test n':>7} {'meanR':>6} {'win':>5} {'pf':>5}")
    for stop_mode in STOPS:
        kinds = sorted({key[1] for key in out if key[2].startswith("mkt")})
        for entry, locs in [*((e, ("any", "loc")) for e in ("market", "limit50", "limit50_filled")),
                            *((k, ("mkt:any", "mkt:loc", "lim:any", "lim:loc")) for k in kinds)]:
            for loc in locs:
                a, b = stats(out[stop_mode, entry, loc, "train"]), stats(out[stop_mode, entry, loc, "test"])
                if a["n"] and b["n"]:
                    print(f"{stop_mode:8} {entry:14} {loc:7} | {a['n']:7} {a['mean_r']:+6.3f} | {b['n']:7} {b['mean_r']:+6.3f} {b['win']:5.1%} {b['pf'] or 0:5.2f}")

if __name__ == "__main__":
    main()
