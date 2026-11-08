"""Limit vs market entry on the same H1 signals, filled/exited on M15. Research only.
python -m scripts.limit_entry_replay

Signal = closed H1 bar (scripts.regime_setup_study). Market: taker at next M15 open + 2 bps slip.
Limit: maker at close -/+ k*ATR_H1, fills only if a later M15 bar trades THROUGH the price within WAIT bars;
TP touched first or timeout = missed (0R). Stop and fill in same bar = loss. Funding ignored."""
import pickle
from collections import defaultdict
from pathlib import Path

from scripts.regime_setup_study import LOOK, MEXC, PKL, atr, bars, h4_regimes, pivots, signals

SLIP, HOLD, TP = 2 / 10000, 192, 2.0  # HOLD in M15 bars = 48 H1
OFFSETS, WAITS = (0.0, 0.25, 0.5), (4, 16)
FOCUS = {("sweep_reclaim", -1, 1, "down"), ("bos_retest", 1, 0, "up"), ("hl_pullback", 1, 0, "up"),
         ("sweep_reclaim", 1, 1, "up"), ("breakout", -1, 0, "range")}  # (setup, side, regime idx, regime)

def exit_r(m, k, side, ent, sl, entry_fee, exit_fee, slip):
    """Walk M15 from index k (fill bar already checked). Net R after entry_fee + exit_fee."""
    risk = side * (ent - sl); tp = ent + side * TP * risk; end = min(len(m) - 1, k + HOLD)
    for j in range(k, end + 1):
        b = m[j]
        if (b.low <= sl) if side > 0 else (b.high >= sl): px = sl; break
        if (b.high >= tp) if side > 0 else (b.low <= tp): px = tp; break
    else: px = m[end].close
    ex = px * (1 - side * slip)
    return (side * (ex - ent) - ent * entry_fee - ex * exit_fee) / risk

def market(m, k, side, sl, taker):
    ent = m[k].open * (1 + side * SLIP)
    return None if side * (ent - sl) <= 0 else exit_r(m, k, side, ent, sl, taker, taker, SLIP)

def limit(m, k, side, sl, price, wait, maker, taker):
    """Maker entry, taker exit. Returns (R, filled); (0.0, False) = missed, (None, False) = invalid."""
    if side * (price - sl) <= 0: return None, False
    risk = side * (price - sl); tp = price + side * TP * risk
    for j in range(k, min(len(m), k + wait)):
        b = m[j]
        through = (b.low < price) if side > 0 else (b.high > price)
        if ((b.high >= tp) if side > 0 else (b.low <= tp)) and not through: return 0.0, False  # ran away
        if through:
            if (b.low <= sl) if side > 0 else (b.high >= sl):  # fill + stop same bar = loss
                ex = sl * (1 - side * SLIP)
                return (side * (ex - price) - price * maker - ex * taker) / risk, True
            if j + 1 >= len(m): return None, True
            return exit_r(m, j + 1, side, price, sl, maker, taker, SLIP), True
    return 0.0, False

def run(sym, m15):
    taker, maker = 8 / 10000, 6 / 10000  # MEXC API realTakerFee=0.0008, realMakerFee=0.0006 across all 10 pairs
    h1, h4 = bars(m15, 3600), bars(m15, 14400); regs = h4_regimes(h4)
    t, o, h, l, c = map(list, zip(*h1)); a = atr(h, l, c)
    at15 = {b.time: n for n, b in enumerate(m15)}; h4_idx = {r[0] + 14400: k for k, r in enumerate(h4)}
    piv = pivots(h, l); p = 0; ph = pl = broke_h = broke_l = k4 = None; busy = defaultdict(int); out = []
    for i in range(LOOK + 1, len(c) - 2):
        k4 = h4_idx.get(t[i] + 3600, k4)
        while p < len(piv) and piv[p][0] <= i:
            if piv[p][1] == "H": ph = piv[p][2]
            else: pl = piv[p][2]
            p += 1
        if ph and c[i] > ph: broke_h, ph = (i, ph), None
        if pl and c[i] < pl: broke_l, pl = (i, pl), None
        k = at15.get(t[i] + 3600)
        if k4 is None or regs[k4][0] == "warm" or k is None: continue
        for name, side, sl in signals(t, o, h, l, c, a, i, (ph, pl, broke_h, broke_l)):
            cells = [f for f in FOCUS if f[0] == name and f[1] == side and regs[k4][f[2]] == f[3]]
            if not cells or busy[(name, side)] > k: continue
            mk = market(m15, k, side, sl, taker)
            if mk is None: continue
            busy[(name, side)] = k + HOLD // 4  # ponytail: approximate one-at-a-time spacing (12h)
            arms = {}
            for off in OFFSETS:
                for w in WAITS:
                    arms[(off, w)] = limit(m15, k, side, sl, c[i] - side * off * a[i], w, maker, taker)
            for f in cells: out.append((t[i], f, mk, arms))
    return out

def _check():
    from backend.app.market import Candle
    m = [Candle(0, 100, 100.5, 99.5, 100, 0), Candle(900, 100, 104.2, 99.9, 104, 0)]
    r, f = limit(m, 0, 1, 98, 99.8, 4, 0, 0); assert f and 1.9 < r <= 2.0  # fills bar0, TP 103.4 on bar1
    m = [Candle(0, 100, 104, 99.9, 104, 0), Candle(900, 104, 104, 99, 99, 0)]
    assert limit(m, 0, 1, 98, 99.8, 4, 0, 0) == (0.0, False)  # TP hit before price touched = missed
    m = [Candle(0, 100, 100, 97, 98, 0)]
    r, filled = limit(m, 0, 1, 98, 99.8, 4, 0, 0)
    assert filled and -1.02 < r <= -1.0  # fill + stop same bar = full loss

if __name__ == "__main__":
    _check()
    rows = []
    for sym, d in pickle.loads(Path(PKL).read_bytes()).items(): rows += run(sym, d[2])
    times = sorted(r[0] for r in rows); cut = times[int(len(times) * .7)]
    mean = lambda x: sum(x) / len(x) if x else float("nan")
    for f in sorted(FOCUS):
        sub = [r for r in rows if r[1] == f]
        if not sub: continue
        print(f"\n== {f[0]} {'L' if f[1] > 0 else 'S'} | H4 {'swing' if f[2] else 'ER'}={f[3]} | signals {len(sub)} ==")
        for half, lab in ((0, "train"), (1, "val")):
            part = [r for r in sub if (r[0] > cut) == half]
            mk = [r[2] for r in part]
            print(f"  [{lab}] n={len(part)} market taker R/signal={mean(mk):.3f}")
            for arm in sorted(part[0][3]) if part else []:
                got = [(r[3][arm], r[2]) for r in part if r[3][arm][0] is not None]
                fills = [x[0][0] for x in got if x[0][1]]
                mk_filled = [x[1] for x in got if x[0][1]]; mk_missed = [x[1] for x in got if not x[0][1]]
                print(f"    limit off={arm[0]:.2f}ATR wait={arm[1]:2}xM15 fill={len(fills)/max(len(got),1):4.0%} "
                      f"R/signal={sum(fills)/max(len(got),1):6.3f} R/fill={mean(fills):6.3f} | "
                      f"market R on filled={mean(mk_filled):6.3f} missed={mean(mk_missed):6.3f}")
