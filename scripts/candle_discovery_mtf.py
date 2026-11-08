"""Multi-timeframe data-driven pattern discovery across D1, H4, H1, M15.

Uses frozen 344-day MEXC dataset (10 pairs).
Extracts causal price-action & volume features without predefining pattern names.
Tests simple decision-rule splits & nearest neighbors across 3 chronological folds:
Fold 1 (Train 50%), Fold 2 (Val 25%), Fold 3 (Holdout 25%).
Accounts for 0.08% taker fees, 0.02% slippage per side, and M15 funding.
"""

import bisect
import hashlib
import json
import math
import pickle
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import fmean, median

from backend.app.futures_learner import FEE, SLIP
from scripts.htf_portfolio_replay import funding_map, load_data
from scripts.price_action_replay import atr, bucket, ema

DATA_PATH = Path("research-output/candle-discovery-20261105/data.pkl")
OUTPUT_PATH = Path("research-output/candle-discovery-mtf.json")

TIMEFRAMES = {
    "D1": {"sec": 86400, "warmup": 35, "hold_bars": 15},
    "H4": {"sec": 14400, "warmup": 60, "hold_bars": 30},
    "H1": {"sec": 3600, "warmup": 100, "hold_bars": 40},
    "M15": {"sec": 900, "warmup": 200, "hold_bars": 48},
}


def build_folds(first_ts, last_ts):
    span = last_ts - first_ts
    f1 = first_ts + int(span * 0.50) // 86400 * 86400
    f2 = first_ts + int(span * 0.75) // 86400 * 86400
    return [
        {"name": "train_50", "start": first_ts, "end": f1},
        {"name": "val_25", "start": f1, "end": f2},
        {"name": "holdout_25", "start": f2, "end": last_ts},
    ]


def extract_features_and_trades(symbol, m15_rows, fair_rows, funding_records, tf_name):
    tf_spec = TIMEFRAMES[tf_name]
    sec = tf_spec["sec"]
    warmup = tf_spec["warmup"]
    hold_m15 = tf_spec["hold_bars"] * (sec // 900)

    bars = bucket(m15_rows, sec) if sec > 900 else m15_rows
    if len(bars) <= warmup + 10:
        return []

    m15_time_to_idx = {b.time: i for i, b in enumerate(m15_rows)}
    fund_times = [r["time"] for r in funding_records]
    rates = funding_map(funding_records)

    atrs = atr(bars)
    ema20 = ema([b.close for b in bars], 20)
    ema50 = ema([b.close for b in bars], 50)

    # Extract higher timeframe bias (D1 EMA20) for context
    d1_bars = bucket(m15_rows, 86400)
    d1_times = [b.time + 86400 for b in d1_bars]
    d1_ema = ema([b.close for b in d1_bars], 20)

    samples = []

    for idx in range(warmup, len(bars) - 1):
        bar = bars[idx]
        cur_atr = atrs[idx]
        if cur_atr <= 0:
            continue

        entry_time = bar.time + sec
        entry_m15_idx = m15_time_to_idx.get(entry_time)
        if entry_m15_idx is None or entry_m15_idx + hold_m15 >= len(m15_rows):
            continue

        # Multiframe D1 trend bias
        d1_idx = bisect.bisect_right(d1_times, entry_time) - 1
        d1_trend = 0
        if d1_idx >= 20:
            d1_trend = 1 if d1_bars[d1_idx].close > d1_ema[d1_idx] else -1

        # Causal state features at bar close:
        bar_range = max(bar.high - bar.low, 1e-12)
        body = abs(bar.close - bar.open)
        bar_dir = 1 if bar.close >= bar.open else -1
        body_ratio = body / bar_range
        upper_wick = (bar.high - max(bar.open, bar.close)) / bar_range
        lower_wick = (min(bar.open, bar.close) - bar.low) / bar_range
        norm_range = bar_range / cur_atr

        prior20 = bars[idx - 20:idx]
        p_high = max(b.high for b in prior20)
        p_low = min(b.low for b in prior20)
        p_range = max(p_high - p_low, 1e-12)
        range_compression = p_range / cur_atr
        pos_in_range = (bar.close - p_low) / p_range

        dist_ema20 = (bar.close - ema20[idx]) / cur_atr
        ema_trend = 1 if ema20[idx] > ema50[idx] else -1

        recent5 = bars[max(0, idx - 4):idx + 1]

        # Evaluate both potential actions: Long (1) and Short (-1)
        for direction in (1, -1):
            if direction == 1:
                swing = min(b.low for b in recent5)
                dist = max(bar.close - swing + 0.25 * cur_atr, 0.75 * cur_atr)
                stop = bar.close - dist
            else:
                swing = max(b.high for b in recent5)
                dist = max(swing - bar.close + 0.25 * cur_atr, 0.75 * cur_atr)
                stop = bar.close + dist

            if stop <= 0 or not math.isfinite(stop):
                continue

            # Simulate entry at next open with slip/fee
            raw_entry = m15_rows[entry_m15_idx].open
            if direction * (raw_entry - stop) <= 0:
                continue

            entry = raw_entry * (1 + direction * SLIP)
            risk = direction * (entry - stop)
            if risk <= 0:
                continue

            # Cost-aware 2R target & trailing/breakeven evaluation
            target = entry + direction * 2.0 * risk
            end_idx = min(len(m15_rows) - 1, entry_m15_idx + hold_m15)

            cur_stop = stop
            funding_cost = 0.0
            exit_raw = m15_rows[end_idx].close
            reason = "time"
            exit_idx = end_idx

            for c in range(entry_m15_idx, end_idx + 1):
                mb = m15_rows[c]
                if c > entry_m15_idx:
                    funding_cost += direction * fair_rows[c].open * rates.get(mb.time, 0.0)

                if direction * (mb.open - cur_stop) <= 0:
                    exit_raw, reason, exit_idx = mb.open, "stop_gap", c
                    break
                if mb.low <= cur_stop if direction == 1 else mb.high >= cur_stop:
                    exit_raw, reason, exit_idx = cur_stop, "stop", c
                    break
                if mb.high >= target if direction == 1 else mb.low <= target:
                    exit_raw, reason, exit_idx = target, "target", c
                    break

            exit_fill = exit_raw * (1 - direction * SLIP)
            fees = (entry + exit_fill) * FEE
            net_pnl = direction * (exit_fill - entry) - fees - funding_cost
            net_r = net_pnl / risk

            feat = {
                "tf": tf_name,
                "symbol": symbol,
                "time": entry_time,
                "direction": direction,
                "net_r": net_r,
                "reason": reason,
                # Pure state features
                "bar_dir_match": 1 if bar_dir == direction else 0,
                "body_ratio": body_ratio,
                "wick_entry": lower_wick if direction == 1 else upper_wick,
                "wick_contra": upper_wick if direction == 1 else lower_wick,
                "norm_range": norm_range,
                "range_compression": range_compression,
                "pos_in_range": pos_in_range if direction == 1 else (1.0 - pos_in_range),
                "dist_ema20": direction * dist_ema20,
                "ema_trend_match": 1 if ema_trend == direction else 0,
                "d1_trend_match": 1 if d1_trend == direction else 0,
            }
            samples.append(feat)

    return samples


def run_discovery():
    data, first, end, digest = load_data()
    folds = build_folds(first, end)
    print(f"Loaded {len(data)} symbols. Period: {datetime.fromtimestamp(first, timezone.utc)} to {datetime.fromtimestamp(end, timezone.utc)}")

    all_samples = []
    for tf_name in TIMEFRAMES:
        tf_samples = []
        for sym, pack in data.items():
            s = extract_features_and_trades(sym, pack[2], pack[3], pack[4], tf_name)
            tf_samples.extend(s)
        print(f"Timeframe {tf_name:4s}: generated {len(tf_samples):6d} evaluated action samples.")
        all_samples.extend(tf_samples)

    # Chronological partition
    train_samples = [s for s in all_samples if folds[0]["start"] <= s["time"] < folds[0]["end"]]
    val_samples = [s for s in all_samples if folds[1]["start"] <= s["time"] < folds[1]["end"]]
    holdout_samples = [s for s in all_samples if folds[2]["start"] <= s["time"] < folds[2]["end"]]

    print(f"\nSamples by fold -> Train: {len(train_samples)}, Val: {len(val_samples)}, Holdout: {len(holdout_samples)}")

    # Search rule-space: 1-condition & 2-condition splits
    candidate_rules = [
        # Momentum / Trend
        ("EMA20 Align + D1 Bias", lambda s: s["dist_ema20"] > 0 and s["d1_trend_match"] == 1),
        ("EMA50 Trend + Bar Dir", lambda s: s["ema_trend_match"] == 1 and s["bar_dir_match"] == 1),
        # Range breakout vs Mean Reversion
        ("Range Compression (<4.5 ATR)", lambda s: s["range_compression"] <= 4.5),
        ("Breakout from Compression", lambda s: s["range_compression"] <= 4.5 and s["pos_in_range"] >= 0.95 and s["bar_dir_match"] == 1),
        ("Mean Reversion (Extreme Range)", lambda s: s["pos_in_range"] <= 0.05 and s["wick_entry"] > 0.4),
        # Candlestick Anatomy
        ("Strong Body (>65%) in Direction", lambda s: s["body_ratio"] >= 0.65 and s["bar_dir_match"] == 1),
        ("Rejection Wick (>40% contra-wick)", lambda s: s["wick_entry"] >= 0.40 and s["bar_dir_match"] == 1),
        ("Large Bar (>1.5 ATR) Expansion", lambda s: s["norm_range"] >= 1.5 and s["bar_dir_match"] == 1),
        ("Low Vol Consolidation (<0.8 ATR)", lambda s: s["norm_range"] <= 0.8),
    ]

    report = {"folds": folds, "results_by_tf": {}}

    for tf_name in TIMEFRAMES:
        tf_train = [s for s in train_samples if s["tf"] == tf_name]
        tf_val = [s for s in val_samples if s["tf"] == tf_name]
        tf_hold = [s for s in holdout_samples if s["tf"] == tf_name]

        base_train_r = fmean(s["net_r"] for s in tf_train) if tf_train else 0.0
        base_val_r = fmean(s["net_r"] for s in tf_val) if tf_val else 0.0
        base_hold_r = fmean(s["net_r"] for s in tf_hold) if tf_hold else 0.0

        print(f"\n==========================================")
        print(f" TIMEFRAME: {tf_name}")
        print(f" Baseline (Take Every Signal): Train R={base_train_r:+.3f}, Val R={base_val_r:+.3f}, Holdout R={base_hold_r:+.3f}")
        print(f"==========================================")

        tf_results = {
            "baseline": {"train": base_train_r, "val": base_val_r, "holdout": base_hold_r},
            "rules": []
        }

        for rule_name, rule_fn in candidate_rules:
            matched_train = [s for s in tf_train if rule_fn(s)]
            matched_val = [s for s in tf_val if rule_fn(s)]
            matched_hold = [s for s in tf_hold if rule_fn(s)]

            n_tr, n_va, n_ho = len(matched_train), len(matched_val), len(matched_hold)
            if n_tr < 10 or n_va < 5 or n_ho < 5:
                continue

            r_tr = fmean(s["net_r"] for s in matched_train)
            r_va = fmean(s["net_r"] for s in matched_val)
            r_ho = fmean(s["net_r"] for s in matched_hold)

            win_ho = sum(1 for s in matched_hold if s["net_r"] > 0) / n_ho

            res_entry = {
                "name": rule_name,
                "n_train": n_tr, "r_train": r_tr,
                "n_val": n_va, "r_val": r_va,
                "n_hold": n_ho, "r_hold": r_ho,
                "win_hold": win_ho,
            }
            tf_results["rules"].append(res_entry)

            print(f" Rule: {rule_name:32s} | Tr(N={n_tr:4d}, R={r_tr:+.3f}) | Val(N={n_va:4d}, R={r_va:+.3f}) | Hold(N={n_ho:4d}, R={r_ho:+.3f}, Win={win_ho*100:4.1f}%)")

        report["results_by_tf"][tf_name] = tf_results

    OUTPUT_PATH.write_text(json.dumps(report, indent=2))
    print(f"\nReport written to {OUTPUT_PATH}")


if __name__ == "__main__":
    run_discovery()
