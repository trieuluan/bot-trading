"""Read-only spread sensitivity study; synthetic costs never update live policy."""

import argparse
import json
import math
import time
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import select

from backend.app import forex_scalping as scalp
from backend.app.db import ForexInstrumentState, Session, WorkspaceCandle
from backend.app.exness_feed import WORKSPACE
from backend.app.exness_observation import load_frames
from backend.app.forex_learner import train
from scripts import exness_partial_replay as partial


def synthetic_frames(frames, spread):
    if spread is not None and (not math.isfinite(spread) or spread < 0):
        raise ValueError("Spread must be finite and non-negative")
    result = {}
    for frame, rows in frames.items():
        bids = {row.time: row for row in rows if row.price_type == "bid"}
        result[frame] = []
        for row in rows:
            source = bids[row.time] if spread is not None and row.price_type == "ask" else row
            offset = spread if spread is not None and row.price_type == "ask" else 0
            result[frame].append(SimpleNamespace(
                time=row.time, price_type=row.price_type,
                **{field: getattr(source, field) + offset for field in ("open", "high", "low", "close")},
                tick_volume=row.tick_volume))
    return result


def m1_report(frames, commission, slippage):
    bids = [row for row in frames["1m"] if row.price_type == "bid"]
    asks = {row.time: row for row in frames["1m"] if row.price_type == "ask"}
    if len(bids) < 100 or any(row.time not in asks for row in bids):
        raise ValueError("Insufficient or unpaired M1 candles")
    cut = bids[int(len(bids) * 0.7)].time
    higher = {frame: [row for row in frames[frame] if row.price_type == "bid"] for frame in ("1h", "15m")}
    closes = {frame: [row.time + step for row in higher[frame]] for frame, step in (("1h", 3600), ("15m", 900))}
    trends = [tuple(partial.trend_at(higher[frame], closes[frame], row.time + 60, step)
                    for frame, step in (("1h", 3600), ("15m", 900))) for row in bids]
    costs = {"slippage_bps": slippage, "commission_per_unit": commission, "verified": False}
    results = []
    for variant in scalp.VARIANTS:
        signals = [scalp.scalp_signal(row, bids[max(0, index - 60):index], asks[row.time], variant)
                   if index >= 60 else None for index, row in enumerate(bids)]
        for mode in partial.FILTERS:
            filtered = [signal if signal and partial.allowed(mode, signal, candle, trend) else None
                        for signal, candle, trend in zip(signals, bids, trends)]
            for config in (None, *partial.EXITS):
                results.append({"strategy": variant["id"], "filter": mode,
                                "exit": config["id"] if config else "baseline",
                                **partial.replay(bids, asks, filtered, costs, config, cut)})
    return {"candles": len(bids), "train_before": cut, "results": results}


def m15_report(frames, now, commission, slippage, condition, population):
    report = train({frame: rows for frame, rows in frames.items() if frame != "1m"}, now,
                   commission_per_unit=commission, population=population,
                   slippage={"bps": slippage, "measured": False}, condition=condition)
    cut = report["train_before"]
    results = []
    for name in report["strategy_summary"]:
        samples = [row for row in report["samples"] if row["strategy"] == name]
        train_samples = [row for row in samples if row["confirm_time"] < cut
                         and row.get("label_available_at", now + 1) < cut]
        validation = [row for row in samples if row["confirm_time"] >= cut]
        results.append({"strategy": name, "all": scalp.stats(samples),
                        "train": scalp.stats(train_samples), "validation": scalp.stats(validation),
                        "outcomes": dict(Counter(row.get("outcome", row["status"]) for row in samples))})
    return {"candles": report["processed_bars"], "train_before": cut, "results": results}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--commission-per-lot", type=float, default=7.0)
    parser.add_argument("--raw-spread", type=float, default=0.02)
    parser.add_argument("--slippage-bps", type=float, default=0)
    parser.add_argument("--output", type=Path, default=Path("research-output/exness-raw-spread-audit.json"))
    args = parser.parse_args()
    if args.days <= 0 or any(not math.isfinite(value) or value < 0 for value in
                            (args.commission_per_lot, args.raw_spread, args.slippage_bps)) or args.slippage_bps >= 10000:
        parser.error("Invalid days or costs")
    now = int(time.time())
    with Session() as session:
        frames = load_frames(session, now)
        frames["1m"] = session.scalars(select(WorkspaceCandle).where(
            WorkspaceCandle.workspace_id == WORKSPACE, WorkspaceCandle.symbol == "XAUUSD",
            WorkspaceCandle.timeframe == "1m", WorkspaceCandle.time >= now - args.days * 86400,
            WorkspaceCandle.time + 60 <= now).order_by(WorkspaceCandle.time)).all()
        specs = session.get(ForexInstrumentState, WORKSPACE)
        condition = dict(specs.snapshot) if specs and specs.snapshot else None
    if not condition or float(condition.get("contract_size", 0)) <= 0:
        raise ValueError("Missing verified instrument dimensions")
    frames["15m"] = [row for row in frames["15m"] if row.time >= now - args.days * 86400]
    population = json.loads(Path("research-output/exness-learning-policy.json").read_text())["population"]
    commission = args.commission_per_lot / float(condition["contract_size"])
    report = {"as_of": now, "days": args.days, "population": population,
              "assumptions": ["Raw/zero spreads and commission are hypothetical, not verified account costs",
                              "Recorded baseline preserves original bid/ask OHLC; synthetic ask equals bid plus fixed spread",
                              "Bid path unchanged; account-specific bid differences and intrabar spread widening not modeled",
                              "Closed candles; same live learner management; no orders, policy writes or promotion",
                              "70/30 chronological split; overlapping M15 labels; repeated validation is exploratory",
                              "No swap; no tick-latency model; confidence bounds do not correct strategy selection bias"],
              "slippage_bps_each_side": args.slippage_bps, "scenarios": {}}
    for name, spread, fee in (("recorded_standard", None, 0), ("raw_hypothetical", args.raw_spread, commission),
                              ("zero_hypothetical", 0, commission)):
        print(f"Running {name}", flush=True)
        scenario_frames = synthetic_frames(frames, spread)
        scenario_condition = {**condition, "commission_per_unit": fee}
        report["scenarios"][name] = {"spread_override": spread, "commission_per_unit_round_turn": fee,
            "m1": m1_report(scenario_frames, fee, args.slippage_bps),
            "m15": m15_report(scenario_frames, now, fee, args.slippage_bps, scenario_condition, population)}
        args.output.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.output.with_suffix(".tmp")
        temporary.write_text(json.dumps(report, allow_nan=False, indent=2))
        temporary.replace(args.output)
        print(f"Saved {name}", flush=True)


if __name__ == "__main__":
    main()
