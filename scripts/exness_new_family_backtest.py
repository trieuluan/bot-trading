"""Read-only backtest for orb/vsa/mss/vwap genomes with stored bid/ask and exchange-fee override."""

import argparse
import json
import math
import time
from collections import defaultdict
from pathlib import Path
from statistics import mean, stdev

from sqlalchemy import select

from backend.app.db import ForexInstrumentState, Session, WorkspaceCandle
from backend.app.exness_feed import FRAMES, WORKSPACE
from backend.app.exness_observation import audit_frames
from backend.app.forex_buffer_study import advance_variant, new_variants
from backend.app.forex_learner import context, genome_name, genome_signal

FAMILIES = ("orb", "vsa", "mss", "vwap")

def lower(values):
    return mean(values) - 1.96 * stdev(values) / math.sqrt(len(values)) if len(values) > 1 else None

def stats(rows):
    values = [row["net_r"] for row in rows if row["status"] == "resolved"]
    return {"n": len(values), "mean_r": mean(values) if values else None, "lower_r": lower(values),
            "win_rate": sum(value > 0 for value in values) / len(values) if values else None}

def load(session, frame, bid_only=False):
    query = select(WorkspaceCandle).where(
        WorkspaceCandle.workspace_id == WORKSPACE, WorkspaceCandle.symbol == "XAUUSD",
        WorkspaceCandle.timeframe == frame,
        WorkspaceCandle.time + FRAMES[frame][1] <= int(time.time()))
    if bid_only:
        query = query.where(WorkspaceCandle.price_type == "bid")
    return session.scalars(query.order_by(WorkspaceCandle.time)).all()

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commission-per-unit", type=float)
    parser.add_argument("--output", type=Path, default=Path("research-output/exness-new-family-backtest.json"))
    args = parser.parse_args()
    with Session() as session:
        spec = session.get(ForexInstrumentState, WORKSPACE)
        condition = dict(spec.snapshot) if spec and spec.snapshot else None
        frames = {frame: load(session, frame) for frame in FRAMES}
        m15 = frames["15m"]
        h1, h4 = ([row for row in frames[frame] if row.price_type == "bid"] for frame in ("1h", "4h"))
    quality = audit_frames(frames)
    if quality["status"] == "blocked":
        raise SystemExit("Invalid or incomplete bid/ask history")
    if not condition:
        raise SystemExit("Missing instrument specification")
    commission = args.commission_per_unit if args.commission_per_unit is not None else condition.get("commission_per_unit")
    if commission is None or not math.isfinite(commission) or commission < 0:
        raise SystemExit("Missing verified commission")
    bids = [row for row in m15 if row.price_type == "bid"]
    if len(bids) < 300:
        raise SystemExit("Need at least 300 closed M15 candles")
    asks = {row.time: row for row in m15 if row.price_type == "ask"}
    index = {row.time: i for i, row in enumerate(bids)}
    cut = bids[int(len(bids) * 0.7)].time
    population = [g for g in json.loads(Path("research-output/exness-learning-policy.json").read_text())["population"]
                  if g.get("family") in FAMILIES]
    if not population:
        raise SystemExit("No new-family genomes in policy")
    active, done, signals = {}, defaultdict(list), defaultdict(int)
    for candle in bids:
        i, ask = index[candle.time], asks.get(candle.time)
        if i < 60 or ask is None:
            continue
        history, window = bids[i - 60:i], bids[i - 59:i + 1]
        available = candle.time + 900
        hours = [row for row in h1 if row.time + 3600 <= available][-60:]
        higher = [row for row in h4 if row.time + 14400 <= available][-60:]
        if len(hours) < 50 or len(higher) < 20:
            continue
        for name, row in list(active.items()):
            advance_variant(row, candle, ask, window, 0.0, commission)
            if row["status"] not in ("pending", "open"):
                done[name].append(row)
                del active[name]
        features = context(candle, hours, ask)
        for genome in population:
            name = genome_name(genome)
            signal = genome_signal(genome, candle, hours, history, higher=higher)
            if signal and signal["status"] == "confirmed":
                signals[name] += 1
                if name not in active:
                    active[name] = new_variants(signal, window, hours, higher, features, candle, condition)[0]
    for name, row in active.items():
        done[name].append(row)
    rows = []
    for genome in population:
        name = genome_name(genome)
        samples = done[name]
        train = [row for row in samples if row["confirm_time"] < cut and row.get("resolved_at", cut) < cut]
        validation = [row for row in samples if row["confirm_time"] >= cut]
        rows.append({"genome": name, "family": genome["family"], "signals": signals[name], "all": stats(samples),
                     "train": stats(train), "validation": stats(validation),
                     "outcomes": {outcome: sum(row.get("outcome", row["status"]) == outcome for row in samples)
                                  for outcome in {row.get("outcome", row["status"]) for row in samples}}})
    report = {"candles": len(bids), "from": bids[0].time, "to": bids[-1].time + 900,
              "quality": quality, "population": population,
              "train_before": cut, "commission_per_unit": commission,
              "assumptions": ["Closed M15, stored bid/ask, slippage 0", "No swap; one open trade per genome",
                              "70/30 chronological split; crossing train labels excluded; no multiple-testing correction",
                              "Population was already evolved on this history; validation is exploratory, not untouched out-of-sample",
                              "Stored spreads are mixed account history, not 93 days of Raw Spread", "VSA/VWAP use tick-volume proxy"],
              "results": rows}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False))
    f = lambda value: "  n/a" if value is None else f"{value:+.2f}"
    for row in sorted(rows, key=lambda item: (item["family"], -(item["all"]["mean_r"] or -99))):
        print(f"{row['genome'][:58]:<58} sig {row['signals']:>3} | tr n{row['train']['n']:>3} {f(row['train']['mean_r'])} "
              f"lo {f(row['train']['lower_r'])} | val n{row['validation']['n']:>3} {f(row['validation']['mean_r'])} "
              f"lo {f(row['validation']['lower_r'])} | win {f(row['all']['win_rate'])}")

if __name__ == "__main__":
    main()
