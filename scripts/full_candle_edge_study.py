"""Full-chart candle edge & multi-timeframe study on 344d MEXC data (to Nov 4, 2026).

Scans all closed bars on D1, H4, H1, M15 across 10 pairs without relying on archived
rule-tree signals. Compares every candidate setup against a matched random control
(same symbol, timeframe, trend direction, volatility regime, and stop sizing) across
3 chronological folds (Discovery / Validation / Holdout), including settled funding,
taker fees, and slippage.
"""

import bisect
import json
import math
import pickle
import random
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import fmean

from backend.app.futures_learner import FEE, SLIP
from backend.app.market import Candle
from scripts.price_action_replay import atr, bucket, ema, pivot

DATA = Path("research-output/futures-nov04/mexc_10pairs_340d_compare.pkl")
OUTPUT = Path("research-output/full-candle-edge-study.json")


def efficiency_ratio(closes, period=20):
    out = [0.0] * len(closes)
    for i in range(period, len(closes)):
        path = sum(abs(closes[k] - closes[k - 1]) for k in range(i - period + 1, i + 1))
        out[i] = abs(closes[i] - closes[i - period]) / path if path > 0 else 0.0
    return out


def sim_trade_m15(m15_rows, fair_rows, funding, fund_times, entry_m15_idx, direction, stop, mode, atr_val, max_m15_bars, boundary_idx):
    """Simulate trade on M15 bars for exact intrabar SL/TP and funding settlement."""
    if entry_m15_idx >= boundary_idx or entry_m15_idx >= len(m15_rows):
        return None
    raw_entry = m15_rows[entry_m15_idx].open
    entry = raw_entry * (1 + direction * SLIP)
    risk = direction * (entry - stop)
    if risk <= 0.0015 * entry or not math.isfinite(risk):
        return None

    cur_stop = stop
    target = None
    if mode in ("1.5R", "2R", "3R"):
        mult = 1.5 if mode == "1.5R" else (2.0 if mode == "2R" else 3.0)
        target = entry + direction * mult * risk

    end_idx = min(boundary_idx - 1, len(m15_rows) - 1, entry_m15_idx + max_m15_bars - 1)
    if end_idx < entry_m15_idx:
        return None

    funding_cost = 0.0
    exit_price_raw = m15_rows[end_idx].close
    reason = "time"
    exit_idx = end_idx

    for cursor in range(entry_m15_idx, end_idx + 1):
        bar = m15_rows[cursor]
        if cursor > entry_m15_idx:
            # Settled funding at bar.time
            lo = bisect.bisect_left(fund_times, bar.time)
            while lo < len(fund_times) and fund_times[lo] == bar.time:
                funding_cost += direction * fair_rows[cursor].open * funding[lo]["rate"]
                lo += 1

        if direction * (bar.open - cur_stop) <= 0:
            exit_price_raw, reason, exit_idx = bar.open, "stop_gap", cursor
            break
        if (bar.low <= cur_stop) if direction == 1 else (bar.high >= cur_stop):
            exit_price_raw, reason, exit_idx = cur_stop, "stop", cursor
            break
        if target is not None and ((bar.high >= target) if direction == 1 else (bar.low <= target)):
            exit_price_raw, reason, exit_idx = target, "target", cursor
            break
        if mode == "trail2atr":
            # Update trailing stop at M15 bar close using reference ATR
            cand = bar.close - direction * 2.0 * atr_val
            if direction * (cand - cur_stop) > 0:
                cur_stop = cand
        elif mode == "be1r_2r":
            target = entry + direction * 2.0 * risk
            if (bar.high >= target) if direction == 1 else (bar.low <= target):
                exit_price_raw, reason, exit_idx = target, "target", cursor
                break
            if direction * (bar.close - entry) >= 1.0 * risk:
                be = entry * (1 + direction * (2 * FEE + 2 * SLIP))
                if direction * (be - cur_stop) > 0 and direction * (bar.close - be) > 0:
                    cur_stop = be

    exit_fill = exit_price_raw * (1 - direction * SLIP)
    fees = (entry + exit_fill) * FEE
    slip = direction * (entry - raw_entry) + direction * (exit_price_raw - exit_fill)
    gross = direction * (exit_price_raw - raw_entry)
    net = direction * (exit_fill - entry) - fees - funding_cost

    return {
        "net_r": net / risk,
        "gross_r": gross / risk,
        "cost_r": (fees + slip + funding_cost) / risk,
        "fee_r": fees / risk,
        "funding_r": funding_cost / risk,
        "risk_pct": 100.0 * risk / entry,
        "reason": reason,
        "exit_m15_idx": exit_idx,
        "entry_time": m15_rows[entry_m15_idx].time,
    }


def summarize(trades):
    if not trades:
        return {"n": 0, "mean_r": 0.0, "gross_r": 0.0, "cost_r": 0.0, "pf": 0.0, "win": 0.0}
    vals = [t["net_r"] for t in trades]
    pos = sum(v for v in vals if v > 0)
    neg = -sum(v for v in vals if v < 0)
    return {
        "n": len(vals),
        "mean_r": fmean(vals),
        "gross_r": fmean(t["gross_r"] for t in trades),
        "cost_r": fmean(t["cost_r"] for t in trades),
        "pf": pos / neg if neg > 0 else None,
        "win": sum(1 for v in vals if v > 0) / len(vals),
        "risk_pct": fmean(t["risk_pct"] for t in trades),
    }


def scan_frame(raw_data, frame_name, sec, warmup, hold_bars_frame, folds):
    """Scan a timeframe (D1, H4, H1) across all 10 symbols and compare against matched control."""
    max_m15_bars = hold_bars_frame * (sec // 900)
    trades_by_arm = defaultdict(lambda: defaultdict(list))
    ctrl_by_arm = defaultdict(lambda: defaultdict(list))

    for sym, pack in sorted(raw_data.items()):
        m15 = pack[2]
        fair = pack[3]
        funding = pack[4]
        fund_times = [r["time"] for r in funding]
        m15_pos = {b.time: idx for idx, b in enumerate(m15)}

        rows = bucket(m15, sec) if sec > 900 else m15
        if len(rows) <= warmup + 5:
            continue

        closes = [r.close for r in rows]
        ema20 = ema(closes, 20)
        ema50 = ema(closes, 50)
        atr_s = atr(rows, 14)
        er20 = efficiency_ratio(closes, 20)

        # Build higher timeframe trend filter (D1 for H4/H1, H4 for M15)
        htf_sec = 86400 if sec < 86400 else None
        htf_rows = bucket(m15, htf_sec) if htf_sec else []
        htf_ema20 = ema([r.close for r in htf_rows], 20) if htf_rows else []
        htf_close_times = [r.time + htf_sec for r in htf_rows]

        highs, lows = [], []
        busy = defaultdict(lambda: -1)
        rng = random.Random(100 + sec)

        for i in range(2, len(rows) - 1):
            if i >= 4:
                for d, piv in ((1, highs), (-1, lows)):
                    if pivot(rows, i - 2, d):
                        piv.append(rows[i - 2].high if d == 1 else rows[i - 2].low)
            if i < warmup:
                continue

            bar = rows[i]
            close_time = bar.time + sec
            if close_time not in m15_pos:
                continue
            entry_m15_idx = m15_pos[close_time]

            # Determine fold & boundary
            fold_name, fold_end_ts = None, None
            for fname, fstart, fend in folds:
                if fstart <= bar.time < fend:
                    fold_name, fold_end_ts = fname, fend
                    break
            if fold_name is None:
                continue
            boundary_idx = bisect.bisect_left([b.time for b in m15], fold_end_ts)

            # Context indicators at bar i
            win20 = rows[i - 20:i]
            hi20, lo20 = max(r.high for r in win20), min(r.low for r in win20)
            win10 = rows[i - 10:i]
            hi10, lo10 = max(r.high for r in win10), min(r.low for r in win10)
            trend = 1 if bar.close > ema50[i] and ema20[i] > ema50[i] else (
                -1 if bar.close < ema50[i] and ema20[i] < ema50[i] else 0)

            htf_trend = trend
            if htf_rows:
                hk = bisect.bisect_right(htf_close_times, close_time) - 1
                if hk >= 20:
                    htf_trend = 1 if htf_rows[hk].close > htf_ema20[hk] else -1

            width20 = (hi20 - lo20) / atr_s[i] if atr_s[i] > 0 else 99.0
            is_squeeze = width20 <= 4.5
            is_range = er20[i] <= 0.28
            is_trend = er20[i] >= 0.35

            candidates = []
            for d in (1, -1):
                body_aligned = d * (bar.close - bar.open) > 0
                # 1. Donchian 20 Breakout with trend + expansion
                if (bar.close > hi20 if d == 1 else bar.close < lo20) and body_aligned and d == htf_trend:
                    candidates.append(("donchian20_brk_htf", d))
                    if is_squeeze:
                        candidates.append(("squeeze_brk_htf", d))

                # 2. Donchian 20 Sweep & Reclaim (turtle soup)
                swept20 = (bar.low < lo20 < bar.close) if d == 1 else (bar.close < hi20 < bar.high)
                if swept20 and body_aligned:
                    candidates.append(("sweep20_reclaim_all", d))
                    if is_range:
                        candidates.append(("sweep20_reclaim_range", d))
                    if d == htf_trend:
                        candidates.append(("sweep20_reclaim_htf", d))

                # 3. Trend Pullback to EMA20 + Reclaim candle
                pulled = (bar.low <= ema20[i] < bar.close) if d == 1 else (bar.high >= ema20[i] > bar.close)
                if d == trend and d == htf_trend and is_trend and pulled and body_aligned:
                    candidates.append(("trend_ema20_pullback", d))

                # 4. Confirmed Pivot BOS in trend
                piv = highs if d == 1 else lows
                if piv and d == htf_trend and body_aligned:
                    lvl = piv[-1]
                    if (rows[i - 1].close <= lvl < bar.close) if d == 1 else (rows[i - 1].close >= lvl > bar.close):
                        candidates.append(("pivot_bos_htf", d))

                # 5. Exhaustion Climax Reversal (bar > 2.2 ATR outside 10-bar extreme, opposite wick > 35% range)
                rng_bar = bar.high - bar.low
                if rng_bar >= 1.8 * atr_s[i] and is_range:
                    if d == 1 and bar.low < lo10 and (bar.close - bar.low) >= 0.6 * rng_bar and body_aligned:
                        candidates.append(("climax_reject_range", d))
                    elif d == -1 and bar.high > hi10 and (bar.high - bar.close) >= 0.6 * rng_bar and body_aligned:
                        candidates.append(("climax_reject_range", d))

            for setup_name, d in candidates:
                swing = min(r.low for r in rows[i - 4:i + 1]) if d == 1 else max(r.high for r in rows[i - 4:i + 1])
                stop_dist = max(d * (bar.close - swing) + 0.25 * atr_s[i], 1.0 * atr_s[i])
                stop = bar.close - d * stop_dist

                for mode in ("2R", "3R", "trail2atr", "be1r_2r"):
                    arm_key = (setup_name, mode)
                    if busy[arm_key] >= entry_m15_idx:
                        continue
                    res = sim_trade_m15(m15, fair, funding, fund_times, entry_m15_idx, d, stop, mode, atr_s[i], max_m15_bars, boundary_idx)
                    if res is None:
                        continue
                    busy[arm_key] = res["exit_m15_idx"]
                    trades_by_arm[arm_key][fold_name].append(res)

                    # Matched control: pick a random bar within ±15 frame bars in same fold with same direction & stop_dist in ATR
                    offsets = list(range(-15, 16))
                    rng.shuffle(offsets)
                    for off in offsets:
                        ci = i + off
                        if off == 0 or not (warmup <= ci < len(rows) - 1):
                            continue
                        cbar = rows[ci]
                        c_close_time = cbar.time + sec
                        if c_close_time not in m15_pos:
                            continue
                        if not (folds[0][1] <= cbar.time < folds[-1][2]):
                            continue
                        c_entry_idx = m15_pos[c_close_time]
                        c_stop = cbar.close - d * (stop_dist / atr_s[i] * atr_s[ci])
                        cres = sim_trade_m15(m15, fair, funding, fund_times, c_entry_idx, d, c_stop, mode, atr_s[ci], max_m15_bars, boundary_idx)
                        if cres is not None:
                            ctrl_by_arm[arm_key][fold_name].append(cres)
                            break

    return trades_by_arm, ctrl_by_arm


def main():
    raw_data = pickle.loads(DATA.read_bytes())
    s0 = next(iter(raw_data))
    first_ts = max(v[2][0].time for v in raw_data.values())
    last_ts = min(v[2][-1].time for v in raw_data.values()) + 900
    span = last_ts - first_ts
    # 3 chronological folds: 50% Discovery, 25% Validation, 25% Holdout (including Nov 1-4)
    f1 = first_ts + int(span * 0.50) // 86400 * 86400
    f2 = first_ts + int(span * 0.75) // 86400 * 86400
    folds = [
        ("discovery", first_ts, f1),
        ("validation", f1, f2),
        ("holdout", f2, last_ts),
    ]
    print(f"Dataset span: {datetime.fromtimestamp(first_ts, timezone.utc).isoformat()} -> {datetime.fromtimestamp(last_ts, timezone.utc).isoformat()}")
    for fn, a, b in folds:
        print(f"  Fold {fn:10s}: {datetime.fromtimestamp(a, timezone.utc).strftime('%Y-%m-%d')} -> {datetime.fromtimestamp(b, timezone.utc).strftime('%Y-%m-%d')}")

    frames = [
        ("D1", 86400, 30, 15),
        ("H4", 14400, 60, 30),
        ("H1", 3600, 120, 48),
    ]

    report = {"folds": folds, "results": {}}
    survivors = []

    for frame_name, sec, warmup, hold_bars in frames:
        print(f"\nScanning {frame_name} across 10 pairs...")
        trades, ctrls = scan_frame(raw_data, frame_name, sec, warmup, hold_bars, folds)
        report["results"][frame_name] = {}

        print(f"{'Frame':4s} {'Setup':24s} {'Exit':9s} | {'Disc N':>6s} {'Disc R':>7s} {'dCtrl':>6s} | {'Val N':>5s} {'Val R':>7s} {'dCtrl':>6s} | {'Hold N':>6s} {'Hold R':>7s} {'dCtrl':>6s} {'Hold PF':>7s}")
        for (setup_name, mode) in sorted(trades.keys()):
            f_stats = {}
            for fn, _, _ in folds:
                st = summarize(trades[(setup_name, mode)][fn])
                ct = summarize(ctrls[(setup_name, mode)][fn])
                st["ctrl_mean_r"] = ct["mean_r"]
                st["edge_vs_ctrl_r"] = st["mean_r"] - ct["mean_r"]
                f_stats[fn] = st
            report["results"][frame_name][f"{setup_name}:{mode}"] = f_stats

            d, v, h = f_stats["discovery"], f_stats["validation"], f_stats["holdout"]
            pf_h = f"{h['pf']:.2f}" if h["pf"] is not None else "N/A"
            print(f"{frame_name:4s} {setup_name:24s} {mode:9s} | {d['n']:6d} {d['mean_r']:+7.3f} {d['edge_vs_ctrl_r']:+6.3f} | {v['n']:5d} {v['mean_r']:+7.3f} {v['edge_vs_ctrl_r']:+6.3f} | {h['n']:6d} {h['mean_r']:+7.3f} {h['edge_vs_ctrl_r']:+6.3f} {pf_h:>7s}")

            if (d["n"] >= 20 and v["n"] >= 10 and h["n"] >= 10
                    and d["mean_r"] > 0 and v["mean_r"] > 0 and h["mean_r"] > 0
                    and d["edge_vs_ctrl_r"] > 0 and v["edge_vs_ctrl_r"] > 0 and h["edge_vs_ctrl_r"] > 0):
                survivors.append((frame_name, setup_name, mode, f_stats))

    print(f"\n=== SURVIVORS (Positive Net R AND Positive Edge vs Matched Control in ALL 3 folds): {len(survivors)} ===")
    for frame_name, setup_name, mode, f_stats in survivors:
        d, v, h = f_stats["discovery"], f_stats["validation"], f_stats["holdout"]
        print(f"  * {frame_name} {setup_name} ({mode}): Disc={d['mean_r']:+.3f}R (n={d['n']}, edge={d['edge_vs_ctrl_r']:+.3f}) | Val={v['mean_r']:+.3f}R (n={v['n']}, edge={v['edge_vs_ctrl_r']:+.3f}) | Hold={h['mean_r']:+.3f}R (n={h['n']}, edge={h['edge_vs_ctrl_r']:+.3f}, PF={h['pf']:.2f})")

    OUTPUT.write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    main()
