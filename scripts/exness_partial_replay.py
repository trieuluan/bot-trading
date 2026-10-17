"""Read-only M1 partial profit / entry-stop exit experiment."""

import argparse
import json
import math
import time
from bisect import bisect_right
from collections import Counter
from pathlib import Path

from sqlalchemy import select

from backend.app import forex_scalping as scalp
from backend.app.db import Session, WorkspaceCandle
from backend.app.exness_feed import WORKSPACE
from backend.app.exness_sessions import regular_session_gap, session_closed_ahead
from backend.app.forex_strategies import ema

EXITS = (
    {"id": "half_0.5r_free", "partial_r": 0.5, "weight": 0.5, "target_r": None},
    {"id": "half_0.8r_free", "partial_r": 0.8, "weight": 0.5, "target_r": None},
    {"id": "half_1r_free", "partial_r": 1.0, "weight": 0.5, "target_r": None},
    {"id": "seventy_1r_free", "partial_r": 1.0, "weight": 0.7, "target_r": None},
    {"id": "half_1r_tp3r", "partial_r": 1.0, "weight": 0.5, "target_r": 3.0},
    {"id": "half_1.2r_tp4r", "partial_r": 1.2, "weight": 0.5, "target_r": 4.0},
)
FILTERS = ("none", "h1", "m15", "h1+m15")


def advance_partial(sample, bid, ask, config, costs, horizon=60):
    if sample["status"] not in ("pending", "open"):
        return
    if bid.time != ask.time or bid.time != sample["last_candle"] + 60:
        status = "skipped" if (sample["status"] == "pending" and bid.time == ask.time
                               and regular_session_gap(sample["last_candle"], bid.time, 60)) else "excluded"
        sample.update(status=status, reason="data_gap")
        return
    sample["last_candle"] = bid.time
    direction = 1 if sample["side"] == "long" else -1
    slip = costs["slippage_bps"] / 10000
    commission = costs["commission_per_unit"] or 0
    exit_bar = bid if direction == 1 else ask
    if sample["status"] == "pending":
        if session_closed_ahead(bid.time, 4, 60):
            sample.update(status="skipped", reason="session_close_near")
            return
        entry = (ask.open if direction == 1 else bid.open) * (1 + direction * slip)
        risk = direction * (entry - sample["stop"])
        if risk <= 0:
            sample.update(status="invalid", reason="next_open_outside_stop")
            return
        sample.update(status="open", entry=entry, entry_time=bid.time, risk=risk, bars=0,
                      remaining=1.0, realized=0.0, partial_hit=False)
    sample["bars"] += 1
    entry, risk = sample["entry"], sample["risk"]
    partial = entry + direction * config["partial_r"] * risk
    target = entry + direction * config["target_r"] * risk if config["target_r"] else None

    def fill(price, weight):
        execution = price * (1 - direction * slip)
        return weight * (direction * (execution - entry) - commission)

    def resolve(price, outcome):
        pnl = sample["realized"] + fill(price, sample["remaining"])
        sample.update(status="resolved", net_r_ex_swap=pnl / risk, outcome=outcome,
                      label_available_at=bid.time + 60, exit_time=bid.time + 60)

    def stop_touched():
        return exit_bar.low <= sample["stop"] if direction == 1 else exit_bar.high >= sample["stop"]

    if direction * (exit_bar.open - sample["stop"]) <= 0:
        resolve(exit_bar.open, "be_gap" if sample["partial_hit"] else "stop_gap")
        return
    if not sample["partial_hit"]:
        partial_at_open = direction * (exit_bar.open - partial) >= 0
        partial_touched = exit_bar.high >= partial if direction == 1 else exit_bar.low <= partial
        if stop_touched() and not partial_at_open:
            sample["ambiguous_bar"] = partial_touched
            resolve(sample["stop"], "full_stop")
            return
        if partial_touched:
            sample.update(partial_hit=True, remaining=1 - config["weight"], stop=entry,
                          realized=fill(partial, config["weight"]))
            if target and direction * (exit_bar.open - target) >= 0:
                resolve(target, "runner_target_at_open")
                return
            if stop_touched():
                sample["ambiguous_bar"] = not partial_at_open
                resolve(entry, "partial_then_be")
                return
    else:
        if target and direction * (exit_bar.open - target) >= 0:
            resolve(target, "runner_target_at_open")
            return
        if stop_touched():
            sample["ambiguous_bar"] = bool(target and (
                exit_bar.high >= target if direction == 1 else exit_bar.low <= target))
            resolve(entry, "partial_then_be")
            return
    if sample["partial_hit"] and target and (
            exit_bar.high >= target if direction == 1 else exit_bar.low <= target):
        resolve(target, "runner_target")
    elif session_closed_ahead(bid.time, 2, 60):
        resolve(exit_bar.close, "session_close")
    elif sample["bars"] >= horizon:
        resolve(exit_bar.close, "timeout")


def trend_at(rows, closes_at, available, step):
    index = bisect_right(closes_at, available)
    closed = rows[max(0, index - 60):index]
    if len(closed) < 50 or available - closes_at[index - 1] >= step:
        return None
    values = [row.close for row in closed]
    fast, slow = ema(values, 20), ema(values, 50)
    direction = "long" if fast > slow else "short" if fast < slow else None
    middle = (max(row.high for row in closed[-20:]) + min(row.low for row in closed[-20:])) / 2
    return direction, middle


def allowed(mode, signal, candle, trends):
    hourly, minutes = trends
    side = signal["side"]
    h1_ok = hourly is not None and hourly[0] == side
    m15_ok = minutes is not None and minutes[0] == side and (
        candle.close > minutes[1] if side == "long" else candle.close < minutes[1])
    return {"none": True, "h1": h1_ok, "m15": m15_ok, "h1+m15": h1_ok and m15_ok}[mode]


def summarize(samples, cut):
    train = [sample for sample in samples if sample["confirm_time"] < cut
             and sample.get("label_available_at", sample["last_candle"] + 60) < cut]
    validation = [sample for sample in samples if sample["confirm_time"] >= cut]
    return {"train": scalp.stats(train), "validation": scalp.stats(validation),
            "outcomes": dict(Counter(sample.get("outcome", sample["status"]) for sample in samples)),
            "unfinished": sum(sample["status"] in ("pending", "open") for sample in samples),
            "ambiguous_bars": sum(bool(sample.get("ambiguous_bar")) for sample in samples),
            "boundary_excluded": sum(sample["confirm_time"] < cut <= sample.get(
                "label_available_at", sample["last_candle"] + 60) for sample in samples)}


def replay(bids, asks, signals, costs, config, cut):
    samples, active = [], None
    for index, candle in enumerate(bids):
        ask = asks.get(candle.time)
        if ask is None:
            if active:
                active.update(status="excluded", reason="missing_ask")
                samples.append(active)
                active = None
            continue
        if active:
            if config is None:
                scalp._step(active, candle, ask)
            else:
                advance_partial(active, candle, ask, config, costs)
            if active["status"] not in ("pending", "open"):
                samples.append(active)
                active = None
        if active is None and signals[index]:
            active = scalp._new_sample(signals[index], candle, costs)
    if active:
        samples.append(active)
    return summarize(samples, cut)


def run(as_of):
    with Session() as session:
        bids, asks = scalp._load(session, start=as_of - 30 * 86400)
        costs = scalp._costs(session)
        higher = {}
        for frame in ("1h", "15m"):
            higher[frame] = session.scalars(select(WorkspaceCandle).where(
                WorkspaceCandle.workspace_id == WORKSPACE, WorkspaceCandle.symbol == "XAUUSD",
                WorkspaceCandle.price_type == "bid", WorkspaceCandle.timeframe == frame,
                WorkspaceCandle.time < as_of).order_by(WorkspaceCandle.time)).all()
    bids = [row for row in bids if row.time + 60 <= as_of]
    if len(bids) < 100:
        raise ValueError("Not enough closed M1 candles")
    cut = bids[int(len(bids) * 0.7)].time
    hourly, minutes = higher["1h"], higher["15m"]
    hour_times, minute_times = [row.time + 3600 for row in hourly], [row.time + 900 for row in minutes]
    trends = [(trend_at(hourly, hour_times, row.time + 60, 3600),
               trend_at(minutes, minute_times, row.time + 60, 900)) for row in bids]
    results = []
    for variant in scalp.VARIANTS:
        signals = [scalp.scalp_signal(row, bids[index - 60:index], asks.get(row.time), variant)
                   if index >= 60 else None for index, row in enumerate(bids)]
        for mode in FILTERS:
            filtered = [signal if signal and allowed(mode, signal, candle, trend) else None
                        for signal, candle, trend in zip(signals, bids, trends)]
            for slippage in sorted({costs["slippage_bps"], max(1.0, costs["slippage_bps"])}):
                scenario = {**costs, "slippage_bps": slippage}
                for config in (None, *EXITS):
                    result = {"id": f"{variant['id']}:{mode}:{config['id'] if config else 'baseline'}:{slippage}",
                              "strategy": variant["id"], "filter": mode, "exit": config or {"id": "baseline"},
                              "slippage_bps": slippage,
                              **replay(bids, asks, filtered, scenario, config, cut)}
                    results.append(result)
    ranked = sorted(results, key=lambda row: row["train"]["lower_r"] if row["train"]["lower_r"] is not None
                    else -math.inf, reverse=True)
    eligible = [row for row in ranked if row["train"]["resolved"] >= 30
                and row["train"]["lower_r"] is not None and row["train"]["lower_r"] > 0]
    return {"as_of": as_of, "from": bids[0].time, "to": bids[-1].time + 60, "candles": len(bids),
            "cut": cut, "costs": costs, "selected_on_train": eligible[0]["id"] if eligible else None,
            "train_eligible": [row["id"] for row in eligible], "results": ranked,
            "assumptions": ["Closed candles only; next M1 open; actual stored bid/ask spread",
                            "Stop-first; partial then BE on ambiguous same bar; stop gaps filled at open",
                            "Entry-stop is gross BE, not net BE after commission/slippage",
                            "Runner has no trailing stop; no TP for *_free; 60-bar/session-close exit",
                            "Baseline uses existing TP and 30-bar horizon; independent non-overlapping replay per row",
                            "Train labels crossing 70/30 boundary excluded; ranking uses train only",
                            "Slippage/commission estimates unverified; repeated use of validation is exploratory"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--as-of", type=int, default=int(time.time()))
    parser.add_argument("--output", type=Path, default=Path("research-output/exness-partial-replay.json"))
    args = parser.parse_args()
    report = run(args.as_of)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(".tmp")
    temporary.write_text(json.dumps(report, indent=2, allow_nan=False))
    temporary.replace(args.output)
    print(json.dumps({key: value for key, value in report.items() if key != "results"}, indent=2))
    for row in report["results"][:12]:
        print(row["id"], "train", row["train"], "validation", row["validation"])


if __name__ == "__main__":
    main()
