"""Native H4 and D1 strategy replay on 340d MEXC data. Research only: no DB or bot writes.

python -m scripts.htf_replay [--no-cost]
Closed H4/D1 bars only; entry at next bar open; stop wins on shared bar; 1 position per (symbol, frame, setup, exit).
"""
import argparse
import random
from collections import defaultdict

from backend.app.futures_learner import FEE, SLIP
from scripts.price_action_replay import atr, bucket, ema, load, pivot, stats

PATH = "research-output/futures-nov01/mexc_10pairs_340d_compare.pkl"
FRAMES = (("H4", 14400, 60, 42), ("D1", 86400, 30, 20))  # name, sec, warmup, max_bars (~7d H4, 20d D1)

def run_trade(rows, index, direction, stop, mode, atr_series, max_bars, fee, slip):
    entry = rows[index].open * (1 + direction * slip)
    risk = direction * (entry - stop)
    if risk <= 0.002 * entry:
        return None
    cur_stop = stop
    end = min(len(rows) - 1, index + max_bars - 1)
    for cursor in range(index, end + 1):
        bar = rows[cursor]
        if (bar.low <= cur_stop) if direction == 1 else (bar.high >= cur_stop):
            price = cur_stop; break
        if mode in ("2R", "3R"):
            target = entry + direction * (2.0 if mode == "2R" else 3.0) * risk
            if (bar.high >= target) if direction == 1 else (bar.low <= target):
                price = target; break
        else:  # trail 2 ATR from close, ratcheted on bar close
            cand = bar.close - direction * 2.0 * atr_series[cursor]
            if direction * (cand - cur_stop) > 0:
                cur_stop = cand
    else:
        price = rows[end].close
    exit_price = price * (1 - direction * slip)
    return (direction * (exit_price - entry) - (entry + exit_price) * fee) / risk, cursor

def signals_at(rows, i, closes, ema20, ema50, atr_s, highs, lows, rng):
    bar = rows[i]
    win20 = rows[i - 20:i]
    hi20, lo20 = max(r.high for r in win20), min(r.low for r in win20)
    trend = 1 if bar.close > ema50[i] else -1
    out = []
    if rng.random() < 0.08:
        out.append(("random_ctrl", 1 if bar.close >= bar.open else -1))
    for d in (1, -1):
        if (bar.close > hi20 if d == 1 else bar.close < lo20):
            out.append(("donchian20_brk", d))
        if (bar.low < lo20 < bar.close if d == 1 else bar.close < hi20 < bar.high) and d * (bar.close - bar.open) > 0:
            out.append(("donchian20_sweep", d))
        if d == trend and (bar.low <= ema20[i] < bar.close if d == 1 else bar.high >= ema20[i] > bar.close) and d * (bar.close - bar.open) > 0:
            out.append(("ema_pullback", d))
        piv = highs if d == 1 else lows
        if piv and (rows[i - 1].close <= piv[-1] < bar.close if d == 1 else rows[i - 1].close >= piv[-1] > bar.close):
            out.append(("bos", d))
    return out

def main():
    p = argparse.ArgumentParser(); p.add_argument("--no-cost", action="store_true")
    args = p.parse_args()
    fee, slip = (0.0, 0.0) if args.no_cost else (FEE, SLIP)
    data = load(PATH, None)
    first = min(r[0].time for r in data.values()); last = max(r[-1].time for r in data.values())
    split = first + int((last - first) * 0.7)
    out = defaultdict(list)
    for frame, sec, warm, max_bars in FRAMES:
        for symbol, m15 in data.items():
            rows = bucket(m15, sec)
            if len(rows) <= warm + 5:
                continue
            closes = [r.close for r in rows]
            ema20, ema50, atr_s = ema(closes, 20), ema(closes, 50), atr(rows)
            highs, lows, busy, rng = [], [], defaultdict(lambda: -1), random.Random(42)
            for i in range(2, len(rows) - 1):
                if i >= 4:
                    for d, piv in ((1, highs), (-1, lows)):
                        if pivot(rows, i - 2, d):
                            piv.append(rows[i - 2].high if d == 1 else rows[i - 2].low)
                if i < warm:
                    continue
                for kind, d in signals_at(rows, i, closes, ema20, ema50, atr_s, highs, lows, rng):
                    swing = min(r.low for r in rows[i - 4:i + 1]) if d == 1 else max(r.high for r in rows[i - 4:i + 1])
                    stop = rows[i].close - d * max(d * (rows[i].close - swing) + 0.25 * atr_s[i], atr_s[i])
                    for mode in ("2R", "3R", "trail"):
                        if busy[kind, mode] >= i + 1:
                            continue
                        res = run_trade(rows, i + 1, d, stop, mode, atr_s, max_bars, fee, slip)
                        if not res:
                            continue
                        busy[kind, mode] = res[1]
                        part = "train" if rows[i].time < split else "test"
                        out[frame, kind, mode, part].append(res[0])
    print(f"cost={'OFF' if args.no_cost else 'ON'} | {'frame':3} {'setup':17} {'exit':5} | {'tr n':>5} {'meanR':>6} {'pf':>5} | {'te n':>5} {'meanR':>6} {'win':>5} {'pf':>5}")
    for frame, *_ in FRAMES:
        for kind in ("random_ctrl", "donchian20_brk", "donchian20_sweep", "ema_pullback", "bos"):
            for mode in ("2R", "3R", "trail"):
                a, b = stats(out[frame, kind, mode, "train"]), stats(out[frame, kind, mode, "test"])
                if a["n"] and b["n"]:
                    print(f"         {frame:3} {kind:17} {mode:5} | {a['n']:5} {a['mean_r']:+6.3f} {a['pf'] or 0:5.2f} | {b['n']:5} {b['mean_r']:+6.3f} {b['win']:5.1%} {b['pf'] or 0:5.2f}")

if __name__ == "__main__":
    main()
