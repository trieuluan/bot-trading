"""Regime x setup study on MEXC futures data. Research only: no DB/VPS writes.
python -m scripts.regime_setup_study [pkl]

Signals on closed H1 bars, entry next H1 open. Regime from last CLOSED H4 bar only.
Costs = live FEE + SLIP per side. Stop wins when stop and target share a bar.
One open trade per (symbol, setup, tp) to avoid overlapping duplicates."""
import pickle, sys
from collections import defaultdict
from pathlib import Path

FEE, SLIP = 8 / 10000, 2 / 10000  # mirrors backend.app.futures_learner
SLIP_PAPER = SLIP

PKL = "research-output/futures-nov04/mexc_10pairs_340d_compare.pkl"
MODE = sys.argv[1] if len(sys.argv) > 1 else "paper"  # paper | taker | maker
# MEXC contract/detail public rates fetched 2026-11-08 (taker, maker); may change, account tier may differ.
MEXC = {"BTC_USDT": (2, 0), "ETH_USDT": (2, 0), "SOL_USDT": (1, 0), "XRP_USDT": (0, 0), "NEAR_USDT": (2, 0),
        "SUI_USDT": (4, 1), "LINK_USDT": (0, 0), "DOGE_USDT": (4, 1), "AVAX_USDT": (0, 0), "BNB_USDT": (2, 0)}
TPS, HOLD, ER_N, LOOK = (1.0, 1.5, 2.0), 48, 30, 24  # ponytail: fixed grid; widen only if a cell survives

def bars(rows, sec):
    g = defaultdict(list)
    for b in rows: g[b.time // sec].append(b)
    need = sec // 900
    return [(k * sec, x[0].open, max(b.high for b in x), min(b.low for b in x), x[-1].close)
            for k, x in sorted(g.items()) if len(x) == need]

def atr(h, l, c, n=14):
    out, a = [], h[0] - l[0]
    for i in range(len(c)):
        tr = max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1])) if i else h[i] - l[i]
        a += (tr - a) / n; out.append(a)
    return out

def pivots(h, l, k=2):
    """(confirm_index, kind, price): pivot at i known only after bar i+k closes."""
    out = []
    for i in range(k, len(h) - k):
        win = range(i - k, i + k + 1)
        if all(h[i] > h[j] for j in win if j != i): out.append((i + k, "H", h[i]))
        if all(l[i] < l[j] for j in win if j != i): out.append((i + k, "L", l[i]))
    return out

def h4_regimes(h4):
    """Per H4 index: (er_regime, swing_regime), using bars <= index only."""
    t, o, h, l, c = map(list, zip(*h4)); piv = pivots(h, l); p = 0; hs, ls = [], []; out = []
    for i in range(len(c)):
        while p < len(piv) and piv[p][0] <= i:
            (hs if piv[p][1] == "H" else ls).append(piv[p][2]); p += 1
        if i < ER_N: out.append(("warm", "warm")); continue
        path = sum(abs(c[j] - c[j - 1]) for j in range(i - ER_N + 1, i + 1))
        er = abs(c[i] - c[i - ER_N]) / path if path else 0
        e = "range" if er <= .2 else "unclear" if er <= .3 else "up" if c[i] > c[i - ER_N] else "down"
        s = "warm"
        if len(hs) >= 2 and len(ls) >= 2:
            s = ("up" if hs[-1] > hs[-2] and ls[-1] > ls[-2] else
                 "down" if hs[-1] < hs[-2] and ls[-1] < ls[-2] else "range")
        out.append((e, s))
    return out

def simulate(o, h, l, c, i, side, sl, tp_r):
    """Entry at o[i+1]; returns net R or None."""
    ent = o[i + 1] * (1 + side * SLIP); risk = side * (ent - sl)
    if risk <= 0: return None, i
    tp = ent + side * tp_r * risk; end = min(len(c) - 1, i + HOLD)
    for j in range(i + 1, end + 1):
        if (l[j] <= sl) if side > 0 else (h[j] >= sl): px = sl; break
        if (h[j] >= tp) if side > 0 else (l[j] <= tp): px = tp; break
    else: px, j = c[end], end
    ex = px * (1 - side * SLIP)
    return (side * (ex - ent) - (ent + ex) * FEE) / risk, j

def signals(t, o, h, l, c, a, i, piv_state):
    """Yield (setup, side, stop). Structural stops floored at 0.5 ATR so fee/R stays sane."""
    hi, lo = max(h[i - LOOK:i]), min(l[i - LOOK:i]); up, dn = c[i] > o[i], c[i] < o[i]
    floor = lambda side, s: min(s, c[i] - .5 * a[i]) if side > 0 else max(s, c[i] + .5 * a[i])
    yield "all_long", 1, c[i] - a[i]
    yield "all_short", -1, c[i] + a[i]
    if l[i] < lo < c[i] and up: yield "sweep_reclaim", 1, floor(1, l[i] - .1 * a[i])
    if h[i] > hi > c[i] and dn: yield "sweep_reclaim", -1, floor(-1, h[i] + .1 * a[i])
    if c[i] > hi: yield "breakout", 1, c[i] - a[i]
    if c[i] < lo: yield "breakout", -1, c[i] + a[i]
    ph, pl, broke_h, broke_l = piv_state
    # HL pullback: retrace into last confirmed H1 swing low zone, close bullish above it
    if pl and pl < l[i] <= pl + .5 * a[i] or (pl and l[i] <= pl < c[i]):
        if up: yield "hl_pullback", 1, floor(1, min(pl, l[i]) - .25 * a[i])
    if ph and ph - .5 * a[i] <= h[i] < ph or (ph and h[i] >= ph > c[i]):
        if dn: yield "lh_pullback", -1, floor(-1, max(ph, h[i]) + .25 * a[i])
    # BOS retest: level broken within last LOOK bars, now retested and held
    if broke_h and i - broke_h[0] <= LOOK and l[i] <= broke_h[1] < c[i] and up:
        yield "bos_retest", 1, floor(1, l[i] - .1 * a[i])
    if broke_l and i - broke_l[0] <= LOOK and h[i] >= broke_l[1] > c[i] and dn:
        yield "bos_retest", -1, floor(-1, h[i] + .1 * a[i])

def run(m15):
    h1, h4 = bars(m15, 3600), bars(m15, 14400)
    t, o, h, l, c = map(list, zip(*h1)); a = atr(h, l, c); regs = h4_regimes(h4)
    h4_idx = {row[0] + 14400: k for k, row in enumerate(h4)}  # close time -> index
    piv = pivots(h, l); p = 0; ph = pl = None; broke_h = broke_l = None; k4 = None
    busy, out = defaultdict(int), []
    for i in range(LOOK + 1, len(c) - 2):
        k4 = h4_idx.get(t[i] + 3600, k4)
        while p < len(piv) and piv[p][0] <= i:
            if piv[p][1] == "H": ph = piv[p][2]
            else: pl = piv[p][2]
            p += 1
        if ph and c[i] > ph: broke_h, ph = (i, ph), None
        if pl and c[i] < pl: broke_l, pl = (i, pl), None
        if k4 is None or regs[k4][0] == "warm": continue
        for name, side, sl in signals(t, o, h, l, c, a, i, (ph, pl, broke_h, broke_l)):
            for tp in TPS:
                key = (name, side, tp)
                if busy[key] > i: continue
                r, j = simulate(o, h, l, c, i, side, sl, tp)
                if r is None: continue
                busy[key] = j + 1
                out.append((t[i], regs[k4], name, side, tp, r))
    return out

def _check():
    o = [100, 100, 100, 100]; h = [101, 101, 103, 101]; l = [99, 99, 99.5, 97]; c = [100] * 4
    r, j = simulate(o, h, l, c, 0, 1, 98, 1.0)  # target 102 hit on bar 2 before stop
    assert j == 2 and 0.9 < r < 1.0, r
    t = [(i * 14400, 1, 2 + (i == 3), 1 - (i == 6) * .5, 1.5) for i in range(10)]
    assert pivots(list(zip(*t))[2], list(zip(*t))[3])[0][0] == 5  # pivot H at 3 confirmed at 5, not earlier

if __name__ == "__main__":
    _check()
    rows = []
    print("cost mode:", MODE)
    for sym, d in pickle.loads(Path(PKL).read_bytes()).items():
        if MODE != "paper":  # maker = limit fill at next open, no slippage (upper bound: ignores missed fills)
            FEE = MEXC[sym][MODE == "maker"] / 10000; SLIP = 0 if MODE == "maker" else SLIP_PAPER
        rows += [(sym, *x) for x in run(d[2])]
    times = sorted(r[1] for r in rows); cut = times[int(len(times) * .7)]
    q = [times[int(len(times) * f / 4)] for f in (1, 2, 3)]
    m = lambda x: sum(x) / len(x) if x else float("nan")
    for ri, rname in ((0, "H4 ER"), (1, "H4 swing")):
        agg = defaultdict(lambda: [[], [], [[] for _ in range(4)], set()])
        for sym, ts, reg, name, side, tp, r in rows:
            sd = "L" if side > 0 else "S"
            for key in ((name, sd, reg[ri], tp), (name, "LS", reg[ri], tp)):
                g = agg[key]; g[ts > cut].append(r); g[2][sum(ts > x for x in q)].append(r)
                if r > 0: g[3].add(sym)
        print(f"\n== regime {rname} | cells with n>=150, train>0, val>0 ==")
        print(f"{'setup':14}{'side':>4} {'reg':>7} {'tp':>4} {'n_tr':>6} {'R_tr':>7} {'n_val':>6} {'R_val':>7}  quarters")
        hits = 0
        for key, (tr_, va, qs, _) in sorted(agg.items(), key=lambda kv: -m(kv[1][1])):
            if len(tr_) + len(va) < 150 or m(tr_) <= 0 or m(va) <= 0: continue
            hits += 1
            flag = " *" if sum(m(x) > 0 for x in qs) >= 3 and len(va) >= 100 else ""
            print(f"{key[0]:14}{key[1]:>4} {key[2]:>7} {key[3]:4.1f} {len(tr_):6} {m(tr_):7.3f} {len(va):6} {m(va):7.3f}  "
                  + " ".join(f"{m(x):6.3f}" for x in qs) + flag)
        ranked = [kv for kv in agg.items() if len(kv[1][0]) + len(kv[1][1]) >= 150]
        print(f"cells tested (n>=150): {len(ranked)}; top 6 by R_val regardless of train:")
        for key, (tr_, va, qs, _) in sorted(ranked, key=lambda kv: -m(kv[1][1]))[:6]:
            print(f"  {key[0]:14}{key[1]:>4} {key[2]:>7} {key[3]:4.1f} n_tr={len(tr_):5} R_tr={m(tr_):7.3f} n_val={len(va):5} R_val={m(va):7.3f}")
        print("cells passing:", hits, "| * = >=3/4 quarters positive and n_val>=100")
        base = [r for _, ts, reg, name, *_rest, r in rows if name in ("all_long", "all_short")]
        print("baseline all_long/all_short mean R:", round(m(base), 3))
