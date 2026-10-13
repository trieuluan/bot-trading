"""Read-only Exness MTF replay; independent hypothetical trades, not a portfolio."""

import argparse
import hashlib
import json
import math
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from backend.app.db import Session
from backend.app.exness_feed import FRAMES
from backend.app.exness_observation import audit_frames, load_frames, observe_forex

def advance(sample, bid, ask, slippage_bps=0, commission_per_unit=0, horizon_bars=96):
    if sample["status"] not in ("pending", "open"):
        return
    if bid.time != ask.time or bid.time != sample["last_candle"] + 900:
        sample.update(status="censored", reason="unverified_session_gap")
        return
    sample["last_candle"] = bid.time
    direction = 1 if sample["side"] == "long" else -1
    slip = slippage_bps / 10000
    exit_bar = bid if direction == 1 else ask
    if sample["status"] == "pending":
        entry = (ask.open if direction == 1 else bid.open) * (1 + direction * slip)
        risk = direction * (entry - sample["stop"])
        target = entry + direction * 2 * risk
        if risk <= 0 or target <= 0:
            sample.update(status="invalid", reason="next_open_outside_stop")
            return
        sample.update(status="open", entry=entry, entry_time=bid.time, risk=risk,
                      target=target, bars=0)
    sample["bars"] += 1
    stop, target = sample["stop"], sample["target"]
    stop_hit = exit_bar.low <= stop if direction == 1 else exit_bar.high >= stop
    target_hit = exit_bar.high >= target if direction == 1 else exit_bar.low <= target
    if direction * (exit_bar.open - stop) <= 0:
        price, outcome = exit_bar.open, "stop_gap"
    elif direction * (exit_bar.open - target) >= 0:
        price, outcome = target, "target_at_open"
    elif stop_hit:
        price, outcome = stop, "stop"
        sample["ambiguous_bar"] = target_hit
    elif target_hit:
        price, outcome = target, "target"
    elif sample["bars"] >= horizon_bars:
        price, outcome = exit_bar.close, "timeout"
    else:
        return
    price *= 1 - direction * slip
    pnl = direction * (price - sample["entry"]) - commission_per_unit
    sample.update(status="resolved", outcome=outcome, exit=price, exit_time=bid.time,
                  label_available_at=bid.time + 900, pnl_per_unit_ex_swap=pnl,
                  net_r_ex_swap=pnl / sample["risk"])

def summarize(samples):
    resolved = sorted((sample for sample in samples if sample["status"] == "resolved"),
                      key=lambda sample: (sample["exit_time"], sample["confirm_time"]))
    returns = [sample["net_r_ex_swap"] for sample in resolved]
    cumulative = peak = drawdown = 0
    for value in returns:
        cumulative += value
        peak = max(peak, cumulative)
        drawdown = max(drawdown, peak - cumulative)
    gains = sum(max(value, 0) for value in returns)
    losses = -sum(min(value, 0) for value in returns)
    return {"confirmations": len(samples), "states": dict(Counter(row["status"] for row in samples)),
            "resolved": len(resolved), "win_rate": sum(value > 0 for value in returns) / len(returns) if returns else None,
            "mean_r_ex_swap": cumulative / len(returns) if returns else None,
            "sum_r_ex_swap": cumulative, "profit_factor_ex_swap": gains / losses if losses else None,
            "closed_sample_drawdown_r": drawdown,
            "ambiguous_stop_first": sum(row.get("ambiguous_bar", False) for row in resolved)}

def run_replay(frames, now=None, slippage_bps=0, commission_per_unit=0):
    if not all(math.isfinite(value) and value >= 0 for value in (slippage_bps, commission_per_unit)) or slippage_bps >= 10000:
        raise ValueError("Costs must be finite and non-negative; slippage must be below 10000 bps")
    now = int(time.time()) if now is None else now
    frames = {frame: sorted((row for row in frames[frame] if row.time + step <= now),
                            key=lambda row: (row.time, row.price_type))
              for frame, (_, step) in FRAMES.items()}
    if any(len({(row.time, row.price_type) for row in rows}) != len(rows) for rows in frames.values()):
        raise ValueError("Duplicate candles")
    quality = audit_frames(frames)
    if quality["status"] == "blocked":
        raise ValueError("Invalid or incomplete bid/ask history; replay refused")
    bids = {frame: [row for row in rows if row.price_type == "bid"] for frame, rows in frames.items()}
    asks = {row.time: row for row in frames["15m"] if row.price_type == "ask"}
    samples, active, events = [], [], []
    previous = None
    last_event = None
    evaluated = 0
    for candle in bids["15m"]:
        for sample in active:
            advance(sample, candle, asks[candle.time], slippage_bps, commission_per_unit)
        active = [sample for sample in active if sample["status"] in ("pending", "open")]
        snapshot = observe_forex(candle, bids["1h"], bids["4h"], previous)
        snapshot["provenance"] = "historical"
        previous = snapshot
        evaluated += snapshot["status"] != "warming_up"
        key = (snapshot["status"], snapshot.get("breakout_time"), snapshot.get("side"), snapshot["reason"])
        if key != last_event:
            events.append(snapshot)
            last_event = key
        if snapshot["status"] == "confirmed":
            sample = {"status": "pending", "side": snapshot["side"], "confirm_time": candle.time,
                      "last_candle": candle.time, "stop": snapshot["stop"], "setup": snapshot}
            samples.append(sample)
            active.append(sample)
    for sample in active:
        sample.update(status="censored", reason="end_of_history")
    if evaluated == 0:
        raise ValueError("Insufficient closed H1/H4 warmup history")
    records = {frame: [{field: getattr(row, field) for field in (
        "time", "price_type", "open", "high", "low", "close")}
        for row in rows] for frame, rows in frames.items()}
    digest = hashlib.sha256(json.dumps(records, sort_keys=True, allow_nan=False).encode()).hexdigest()
    months = sorted({datetime.fromtimestamp(row["confirm_time"], timezone.utc).strftime("%Y-%m") for row in samples})
    return {"version": "exness_mtf_replay_v1", "source": "exness_forex", "symbol": "XAUUSD",
            "provenance": "historical", "observation_only": True, "dataset_sha256": digest,
            "as_of": now, "start": bids["15m"][0].time, "end": bids["15m"][-1].time + 900,
            "processed_bars": len(bids["15m"]), "evaluated_bars": evaluated,
            "assumptions": {"entry": "next M15 ask open for long, bid open for short",
                            "exit": "bid OHLC for long, ask OHLC for short",
                            "target": "2R from executable next-open entry; stop from bid retest structure",
                            "same_bar_policy": "open first, then stop before target",
                            "unknown_session_gap": "censor open/pending sample, cancel active setup",
                            "slippage_bps_each_side": slippage_bps,
                            "round_trip_commission_per_unit": commission_per_unit,
                            "swap_included": False, "costs_calibrated": False,
                            "horizon_trading_bars": 96, "overlapping_samples": True,
                            "portfolio_simulation": False, "out_of_sample": False},
            "quality": quality, "summary": summarize(samples),
            "by_side": {side: summarize([row for row in samples if row["side"] == side]) for side in ("long", "short")},
            "by_month": {month: summarize([row for row in samples if datetime.fromtimestamp(row["confirm_time"], timezone.utc).strftime("%Y-%m") == month]) for month in months},
            "events": events, "samples": samples}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("research-output/exness-mtf-replay.json"))
    parser.add_argument("--slippage-bps", type=float, default=0)
    parser.add_argument("--commission-per-unit", type=float, default=0)
    parser.add_argument("--as-of", type=int, help="UTC epoch seconds; replay only candles closed by this instant")
    args = parser.parse_args()
    now = int(time.time()) if args.as_of is None else args.as_of
    if now <= 0:
        parser.error("--as-of must be positive")
    with Session() as session:
        frames = load_frames(session, now)
        report = run_replay(frames, now, args.slippage_bps, args.commission_per_unit)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    temporary.replace(args.output)
    print(json.dumps({"output": str(args.output), "summary": report["summary"],
                      "by_side": report["by_side"], "assumptions": report["assumptions"]}, ensure_ascii=False))

if __name__ == "__main__":
    main()
