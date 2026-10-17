"""Audit all 40 population genomes with real M15/H1/4H data across 30 days and various SL/TP exit policies."""

import json
import math
import time
from collections import defaultdict
from statistics import mean, stdev

from sqlalchemy import select

from backend.app.db import ForexInstrumentState, Session, WorkspaceCandle
from backend.app.exness_feed import WORKSPACE
from backend.app.forex_buffer_study import VARIANTS, advance_variant, new_variants
from backend.app.forex_learner import (
    context,
    genome_name,
    genome_signal,
)


def _lower(values):
    return mean(values) - 1.96 * stdev(values) / math.sqrt(len(values)) if len(values) > 1 else None

def stats(samples):
    values = [r["net_r"] for r in samples if r["status"] == "resolved"]
    return {
        "n": len(values),
        "mean_r": mean(values) if values else None,
        "lower_r": _lower(values),
        "win_rate": sum(v > 0 for v in values) / len(values) if values else None,
        "sum_r": sum(values) if values else 0.0,
    }

def main():
    now = int(time.time())
    start_30d = now - 30 * 86400

    with Session() as session:
        specs = session.get(ForexInstrumentState, WORKSPACE)
        condition = specs.snapshot if specs else None
        if not condition:
            condition = {
                "instrument": "XAUUSD", "trade_mode": "enabled", "exec_mode": "market",
                "quote_currency": "USD", "volume_min": "0.01", "volume_max": "10.0",
                "volume_step": "0.01", "contract_size": "100", "point_digits": 3
            }
        
        m15_all = session.scalars(select(WorkspaceCandle).where(
            WorkspaceCandle.workspace_id == WORKSPACE, WorkspaceCandle.symbol == "XAUUSD",
            WorkspaceCandle.timeframe == "15m").order_by(WorkspaceCandle.time)).all()
        h1_all = session.scalars(select(WorkspaceCandle).where(
            WorkspaceCandle.workspace_id == WORKSPACE, WorkspaceCandle.symbol == "XAUUSD",
            WorkspaceCandle.timeframe == "1h", WorkspaceCandle.price_type == "bid").order_by(WorkspaceCandle.time)).all()
        h4_all = session.scalars(select(WorkspaceCandle).where(
            WorkspaceCandle.workspace_id == WORKSPACE, WorkspaceCandle.symbol == "XAUUSD",
            WorkspaceCandle.timeframe == "4h", WorkspaceCandle.price_type == "bid").order_by(WorkspaceCandle.time)).all()

    m15_bids = [r for r in m15_all if r.price_type == "bid"]
    m15_asks = {r.time: r for r in m15_all if r.price_type == "ask"}
    m15_idx_map = {r.time: i for i, r in enumerate(m15_bids)}

    # Target 30 days
    eval_bids = [r for r in m15_bids if r.time >= start_30d]
    if len(eval_bids) < 100:
        raise ValueError("Not enough closed M15 candles")

    cut_time = eval_bids[int(len(eval_bids) * 0.7)].time
    print(f"Dataset 30 days: {len(eval_bids)} M15 candles | Cutoff 70/30: {cut_time}")

    policy = json.load(open("research-output/exness-learning-policy.json"))
    population = policy["population"]
    print(f"Population: {len(population)} genomes")

    # We will test all variants across all genomes
    # To avoid position overlap distortion per genome, we can track each (genome, variant) independently
    # Active orders per (genome_name, variant_name) -> sample
    active_map = {}
    completed = defaultdict(lambda: defaultdict(list)) # genome -> variant -> list of samples

    for c in eval_bids:
        idx = m15_idx_map.get(c.time)
        if idx is None or idx < 60:
            continue
        ask = m15_asks.get(c.time)
        if not ask:
            continue
        history = m15_bids[idx-60:idx]
        history_with_c = m15_bids[max(0, idx-59):idx+1]
        avail = c.time + 900
        hours = [r for r in h1_all if r.time + 3600 <= avail][-60:]
        higher = [r for r in h4_all if r.time + 14400 <= avail][-60:]
        if len(hours) < 50 or len(higher) < 20:
            continue

        # Step existing active orders
        for key, row in list(active_map.items()):
            advance_variant(row, c, ask, history_with_c, slip=0.0, commission=0.0)
            if row["status"] not in ("pending", "open"):
                gname, vname = key
                completed[gname][vname].append(row)
                del active_map[key]

        features = context(c, hours, ask)

        # Check signals for each genome
        for g in population:
            gname = genome_name(g)
            try:
                sig = genome_signal(g, c, hours, history, higher=higher)
            except Exception:
                sig = None
            if sig and sig.get("status") == "confirmed":
                # Generate variants for this genome
                try:
                    # Modify signal so new_variants creates all variants (not just k0.25_)
                    # We create custom variants for gen: k0.0, k0.25, k0.5 with structural, 1.5r, 2r, 3r
                    sig_custom = dict(sig)
                    sig_custom["generated"] = False # allow all variants
                    rows = new_variants(sig_custom, history + [c], hours, higher, features, c, condition)
                except Exception:
                    rows = []
                for row in rows:
                    vname = row["variant"]
                    key = (gname, vname)
                    # Non-overlapping per (genome, variant)
                    if key not in active_map:
                        active_map[key] = row

    # Flush remaining
    for key, row in active_map.items():
        gname, vname = key
        completed[gname][vname].append(row)

    print("\nSimulation complete. Processing results...")
    
    # 1. Aggregate by variant across all genomes
    variant_agg = defaultdict(lambda: {"train": [], "val": []})
    genome_agg = defaultdict(lambda: {"train": [], "val": []})
    
    detailed_rows = []

    for g in population:
        gname = genome_name(g)
        for vname in VARIANTS:
            samples = completed[gname][vname]
            tr = [r for r in samples if r["confirm_time"] < cut_time]
            va = [r for r in samples if r["confirm_time"] >= cut_time]
            
            tr_stats = stats(tr)
            va_stats = stats(va)
            
            variant_agg[vname]["train"].extend([r["net_r"] for r in tr if r["status"] == "resolved"])
            variant_agg[vname]["val"].extend([r["net_r"] for r in va if r["status"] == "resolved"])

            # Default variant for this genome
            if vname == "k0.25_structural":
                genome_agg[gname]["train"].extend([r["net_r"] for r in tr if r["status"] == "resolved"])
                genome_agg[gname]["val"].extend([r["net_r"] for r in va if r["status"] == "resolved"])

            detailed_rows.append({
                "genome": gname, "family": g.get("family"), "trend": g.get("trend"),
                "variant": vname, "train": tr_stats, "val": va_stats
            })

    # Save full audit
    out_path = "research-output/exness-gen-exit-audit.json"
    json.dump({
        "as_of": now,
        "cut_time": cut_time,
        "candles": len(eval_bids),
        "detailed": detailed_rows
    }, open(out_path, "w"), indent=2)
    print(f"Saved full audit report to {out_path}")

    # Output Variant Performance (Aggregate)
    print("\n" + "="*80)
    print(f"{'VARIANT (SL / TP)':<22} | {'TRAIN n':<8} {'R':<8} {'lo_R':<8} {'Win%':<7} | {'VAL n':<8} {'R':<8} {'lo_R':<8} {'Win%':<7}")
    print("="*80)
    
    v_results = []
    for vname in VARIANTS:
        tr_vals = variant_agg[vname]["train"]
        va_vals = variant_agg[vname]["val"]
        tr_s = {
            "n": len(tr_vals), "mean_r": mean(tr_vals) if tr_vals else 0,
            "lo": _lower(tr_vals), "win": sum(x>0 for x in tr_vals)/len(tr_vals) if tr_vals else 0
        }
        va_s = {
            "n": len(va_vals), "mean_r": mean(va_vals) if va_vals else 0,
            "lo": _lower(va_vals), "win": sum(x>0 for x in va_vals)/len(va_vals) if va_vals else 0
        }
        v_results.append((vname, tr_s, va_s))

    v_results.sort(key=lambda x: x[1]["mean_r"], reverse=True)
    for vname, tr_s, va_s in v_results:
        print(f"{vname:<22} | {tr_s['n']:<8} {tr_s['mean_r']:+7.3f} {tr_s['lo'] if tr_s['lo'] else 0:+7.3f} {tr_s['win']*100:5.1f}% | {va_s['n']:<8} {va_s['mean_r']:+7.3f} {va_s['lo'] if va_s['lo'] else 0:+7.3f} {va_s['win']*100:5.1f}%")

    # Output Top Genomes
    print("\n" + "="*80)
    print(f"{'TOP GENOMES (default exit k0.25_structural)':<45} | {'TRAIN n':<8} {'R':<8} {'lo_R':<8} | {'VAL n':<8} {'R':<8}")
    print("="*80)
    g_results = []
    for gname, data in genome_agg.items():
        tr_vals = data["train"]
        va_vals = data["val"]
        tr_mean = mean(tr_vals) if tr_vals else -99
        tr_lo = _lower(tr_vals) if tr_vals else -99
        va_mean = mean(va_vals) if va_vals else -99
        g_results.append((gname, len(tr_vals), tr_mean, tr_lo, len(va_vals), va_mean))

    g_results.sort(key=lambda x: x[2], reverse=True)
    for gname, tr_n, tr_r, tr_lo, va_n, va_r in g_results[:15]:
        print(f"{gname:<45} | {tr_n:<8} {tr_r:+7.3f} {tr_lo:+7.3f} | {va_n:<8} {va_r:+7.3f}")

if __name__ == "__main__":
    main()
