"""Three-timeframe cascade replay: D1->H4->H1 and H4->H1->M15 vs direct middle-frame entry.

Run: /tmp/btv/bin/python -m scripts.three_frame_replay
Offline research only; no DB or VPS writes. Reused history: exploratory, not fresh holdout.
ponytail: M5/M1 cascades skipped (dataset starts at M15; M15 cost/stop already binds).
"""

import bisect
import json
from collections import defaultdict
from pathlib import Path
from statistics import fmean

from scripts import fee_edge_replay as edge
from scripts import htf_portfolio_replay as native
from scripts.mtf_precision_replay import SPECS_PATH, cross_margin_account
from scripts.price_action_replay import atr, bucket, ema

OUTPUT = Path("research-output/three-frame-replay-20261108.json")


def frame_series(m15_rows, seconds):
    rows = m15_rows if seconds == 900 else bucket(m15_rows, seconds)
    closes = [b.close for b in rows]
    return dict(
        rows=rows,
        closes_at=[b.time + seconds for b in rows],
        by_open={b.time: i for i, b in enumerate(rows)},
        ema20=ema(closes, 20) if rows else [],
        ema50=ema(closes, 50) if rows else [],
        atr=atr(rows) if rows else [],
    )


def trigger_confirmed(trig_rows, idx, d, mode):
    bar = trig_rows[idx]
    if d * (bar.close - bar.open) <= 0:
        return False
    if mode == "aligned":
        return True
    if mode == "dip_reclaim":
        if idx == 0:
            return False
        prev = trig_rows[idx - 1]
        dip = bar.low < bar.open if d == 1 else bar.high > bar.open
        brk = bar.close > prev.high if d == 1 else bar.close < prev.low
        return dip or brk
    raise ValueError(f"Unknown trigger mode: {mode}")


def cascade_signals(symbol, m15_rows, top_sec, mid_sec, trig_sec, wait_trig_bars=4):
    """Generate direct mid-frame signals and 3-frame triggered variants."""
    m15_by_time = {b.time: i for i, b in enumerate(m15_rows)}
    top = frame_series(m15_rows, top_sec)
    mid = frame_series(m15_rows, mid_sec)
    trig = frame_series(m15_rows, trig_sec)

    mid_rows, mid_atr = mid["rows"], mid["atr"]
    trig_rows = trig["rows"]
    hold_m15 = (30 * mid_sec) // 900  # 30 mid-frame bars (5d for H4, 30h for H1)

    for idx in range(25, len(mid_rows)):
        bar = mid_rows[idx]
        mid_close_time = bar.time + mid_sec
        top_idx = bisect.bisect_right(top["closes_at"], mid_close_time) - 1
        if top_idx < 50 or mid_atr[idx] <= 0:
            continue

        prior = mid_rows[idx - 20:idx]
        upper = max(b.high for b in prior)
        lower = min(b.low for b in prior)
        width_atr = (upper - lower) / mid_atr[idx]

        top_bar = top["rows"][top_idx]
        top_e20, top_e50 = top["ema20"][top_idx], top["ema50"][top_idx]

        for d in (1, -1):
            if d * (bar.close - bar.open) <= 0:
                continue
            # Top trend bias: strict EMA20/EMA50 alignment, plus D1 close > EMA20 for D1
            strict_trend = d * (top_bar.close - top_e50) > 0 and d * (top_e20 - top_e50) > 0
            ema20_bias = d * (top_bar.close - top_e20) > 0

            swept = bar.low < lower < bar.close if d == 1 else bar.close < upper < bar.high
            broke = bar.close > upper if d == 1 else bar.close < lower
            squeezed = broke and width_atr <= 4.5

            recent = mid_rows[idx - 4:idx + 1]
            swing = min(b.low for b in recent) if d == 1 else max(b.high for b in recent)
            dist = max(d * (bar.close - swing) + 0.25 * mid_atr[idx], mid_atr[idx])
            stop = bar.close - d * dist
            if stop <= 0:
                continue

            setups = []
            if swept and ema20_bias:
                setups.append("sweep_ema20")
            if swept and strict_trend:
                setups.append("sweep_strict")
            if squeezed and strict_trend:
                setups.append("squeeze_strict")

            for sname in setups:
                prefix = f"{top_sec//3600}h_{mid_sec//3600}h_{trig_sec//60}m_{sname}"
                # 1) Direct entry at mid_close_time
                direct_m15_idx = m15_by_time.get(mid_close_time)
                if direct_m15_idx is not None:
                    yield dict(
                        symbol=symbol, seed=f"{prefix}_direct", signal_id=(symbol, mid_close_time, d, sname),
                        entry_idx=direct_m15_idx, entry_time=mid_close_time,
                        direction=d, stop=stop, max_m15_bars=hold_m15,
                    )

                # 2) Triggered entry on trig frame within next wait_trig_bars
                first_trig_idx = trig["by_open"].get(mid_close_time)
                if first_trig_idx is None:
                    continue
                for tmode in ("aligned", "dip_reclaim"):
                    for j in range(first_trig_idx, min(first_trig_idx + wait_trig_bars, len(trig_rows))):
                        tbar = trig_rows[j]
                        # Cancel if structural stop breached before trigger
                        if (d == 1 and tbar.low <= stop) or (d == -1 and tbar.high >= stop):
                            break
                        if trigger_confirmed(trig_rows, j, d, tmode):
                            trig_close = tbar.time + trig_sec
                            ent_idx = m15_by_time.get(trig_close)
                            delay_m15 = (trig_close - mid_close_time) // 900
                            if ent_idx is not None and hold_m15 > delay_m15:
                                yield dict(
                                    symbol=symbol, seed=f"{prefix}_trig_{tmode}",
                                    signal_id=(symbol, mid_close_time, d, sname),
                                    entry_idx=ent_idx, entry_time=trig_close,
                                    direction=d, stop=stop, max_m15_bars=hold_m15 - delay_m15,
                                )
                            break


def check():
    from backend.app.market import Candle
    c = [Candle(i * 900, 100, 101, 99, 100.5, 1) for i in range(8)]
    s = frame_series(c, 3600)
    assert len(s["rows"]) == 2 and trigger_confirmed(s["rows"], 1, 1, "aligned")
    assert not trigger_confirmed(s["rows"], 1, -1, "aligned")
    print("self-checks passed", flush=True)


def main():
    check()
    data, first, end, _ = native.load_data()
    fees = json.loads(edge.FEES.read_text())["fees"]
    specs = json.loads(SPECS_PATH.read_text())
    folds = native.build_folds(first, end)

    grouped = defaultdict(list)
    for sym, pack in sorted(data.items()):
        # Cascade 1: D1 (24h) -> H4 (4h) -> H1 (60m), wait up to 4 H1 bars (=1 H4 bar)
        for sig in cascade_signals(sym, pack[2], 86400, 14400, 3600, wait_trig_bars=4):
            grouped[sig["seed"]].append(sig)
        # Cascade 2: H4 (4h) -> H1 (1h) -> M15 (15m), wait up to 4 M15 bars (=1 H1 bar)
        for sig in cascade_signals(sym, pack[2], 14400, 3600, 900, wait_trig_bars=4):
            grouped[sig["seed"]].append(sig)

    report = {}
    print(f"{'Arm':52s} | {'n':>4s} | {'R':>6s} | {'2xR':>6s} | {'costR':>5s} | {'Folds (disc/val/hold)':24s} | {'$100':>6s} | {'DD%':>5s}")
    print("-" * 126)

    for seed in sorted(grouped):
        is_d1_h4 = seed.startswith("24h_4h_")
        configs = ((3, "fixed", "utc_12_20"), (2, "be", "all")) if is_d1_h4 else ((2, "fixed", "all"), (3, "fixed", "utc_12_20"))
        for tp, mode, gate in configs:
            label = f"{seed}|tp{tp}_{mode}_{gate}"
            trades = edge.replay(data, grouped[seed], fees, folds, tp, mode, 1, gate)
            if not trades:
                continue
            m = edge.metrics(trades)
            st = edge.replay(data, grouped[seed], fees, folds, tp, mode, 1, gate, stress=2)
            sm = edge.metrics(st)
            fr = [round(edge.metrics([x for x in trades if x["fold"] == f])["mean_r"] or 0, 3) for f, _, _ in folds]
            acc = cross_margin_account(trades, data, specs, capital=100.0, risk_pct=0.02, leverage=10, max_open=3)
            cost_r = round(m["mean_fee_r"] + m["mean_funding_r"], 3)
            report[label] = dict(
                n=m["n"], mean_r=round(m["mean_r"], 3), double_cost_r=round(sm["mean_r"], 3),
                cost_r=cost_r, folds=fr, account_100=acc,
            )
            print(f"{label:52s} | {m['n']:4d} | {m['mean_r']:+6.3f} | {sm['mean_r']:+6.3f} | {cost_r:5.3f} | {str(fr):24s} | {acc['final']:6.2f} | {acc['max_drawdown_pct']:5.1f}", flush=True)

    OUTPUT.write_text(json.dumps(report, indent=2))
    print(f"\nSaved to {OUTPUT}")


if __name__ == "__main__":
    main()
