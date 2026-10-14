"""Read-only Exness replay: legacy event study or isolated single-wallet comparisons."""

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
from backend.app.forex_paper import _step_trade
from backend.app.exness_sessions import gap_details, regular_session_gap, session_closed_ahead
from backend.app.forex_reporting import trade_report
from backend.app.forex_strategies import STRATEGIES, strategy_snapshot

def advance(sample, bid, ask, slippage_bps=0, commission_per_unit=0, horizon_bars=96):
    if sample["status"] not in ("pending", "open"):
        return
    if bid.time != ask.time or bid.time != sample["last_candle"] + 900:
        if sample["status"] == "pending" and bid.time == ask.time and regular_session_gap(sample["last_candle"], bid.time):
            sample.update(status="skipped", reason="session_close_near")
            return
        sample.update(status="excluded", reason="data_gap", gap=gap_details(sample["last_candle"], bid.time))
        return
    sample["last_candle"] = bid.time
    direction = 1 if sample["side"] == "long" else -1
    slip = slippage_bps / 10000
    exit_bar = bid if direction == 1 else ask
    if sample["status"] == "pending":
        if session_closed_ahead(bid.time, 4):
            sample.update(status="skipped", reason="session_close_near")
            return
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
    elif session_closed_ahead(bid.time, 2):
        price, outcome = exit_bar.close, "session_close"
    elif sample["bars"] >= horizon_bars:
        price, outcome = exit_bar.close, "timeout"
    else:
        return
    price *= 1 - direction * slip
    pnl = direction * (price - sample["entry"]) - commission_per_unit
    sample.update(status="resolved", outcome=outcome, exit=price, exit_time=bid.time + (900 if outcome == "session_close" else 0),
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
                            "unknown_session_gap": "exclude open/pending sample from stats",
                            "session_close": "flat on last candle before scheduled closure (US DST); no entry within 4 bars of closure",
                            "slippage_bps_each_side": slippage_bps,
                            "round_trip_commission_per_unit": commission_per_unit,
                            "swap_included": False, "costs_calibrated": False,
                            "horizon_trading_bars": 96, "overlapping_samples": True,
                            "portfolio_simulation": False, "out_of_sample": False},
            "quality": quality, "summary": summarize(samples),
            "by_side": {side: summarize([row for row in samples if row["side"] == side]) for side in ("long", "short")},
            "by_month": {month: summarize([row for row in samples if datetime.fromtimestamp(row["confirm_time"], timezone.utc).strftime("%Y-%m") == month]) for month in months},
            "events": events, "samples": samples}

def portfolio(frames, strategy, balance=10000.0, risk=0.01, slippage_bps=2,
              commission_per_unit=0, now=None):
    """One wallet per strategy, using the exact forward-paper execution model."""
    if strategy not in STRATEGIES:
        raise ValueError("Unknown strategy")
    if not math.isfinite(balance) or balance <= 0 or not math.isfinite(risk) or not 0 < risk <= 1:
        raise ValueError("Invalid balance or risk fraction")
    if not all(math.isfinite(value) and value >= 0 for value in (slippage_bps, commission_per_unit)) or slippage_bps >= 10000:
        raise ValueError("Invalid costs")
    now = int(time.time()) if now is None else now
    frames = {frame: sorted((row for row in frames[frame] if row.time + step <= now),
                            key=lambda row: (row.time, row.price_type))
              for frame, (_, step) in FRAMES.items()}
    if any(len({(row.time, row.price_type) for row in rows}) != len(rows) for rows in frames.values()):
        raise ValueError("Duplicate candles")
    if audit_frames(frames)["status"] == "blocked":
        raise ValueError("Invalid or incomplete bid/ask history")
    bids = {frame: [row for row in rows if row.price_type == "bid"] for frame, rows in frames.items()}
    asks = {row.time: row for row in frames["15m"] if row.price_type == "ask"}
    starting_balance = balance
    samples, states = [], Counter()
    active = previous = blocked_at = None
    for index, candle in enumerate(bids["15m"]):
        if active:
            _step_trade(active, candle, asks[candle.time])
            if active["status"] == "resolved":
                balance += active["pnl"]
                active["balance_after"] = balance
            if active["status"] not in ("pending", "open"):
                active = None
        if strategy == "breakout_retest":
            snapshot = observe_forex(candle, bids["1h"], bids["4h"], previous)
            previous = snapshot
        elif strategy == "regime_router":
            breakout = observe_forex(candle, bids["1h"], bids["4h"], previous)
            previous = breakout
            snapshot = strategy_snapshot(strategy, candle, bids["1h"], bids["15m"][max(0, index - 60):index],
                                         higher=bids["4h"], breakout=breakout)
        else:
            snapshot = strategy_snapshot(strategy, candle, bids["1h"], bids["15m"][max(0, index - 60):index])
        states[snapshot["status"]] += 1
        if snapshot["status"] != "confirmed":
            continue
        reason = ("position_already_active" if active else
                  "insufficient_balance" if balance <= 0 else None)
        sample = {
            "status": "skipped" if reason else "pending", "side": snapshot["side"],
            "confirm_time": candle.time, "last_candle": candle.time, "stop": snapshot["stop"],
            "capital": max(0, balance), "allocated_risk": max(0, balance) * risk,
            "slippage_bps": slippage_bps, "commission_per_unit": commission_per_unit,
            "signal": snapshot, "swap_included": False,
        }
        if reason:
            sample["reason"] = reason
        else:
            active = sample
        samples.append(sample)
    if not any(count for status, count in states.items() if status != "warming_up"):
        raise ValueError("Insufficient closed warmup history")
    report = trade_report(samples, starting_balance)
    resolved = [row for row in samples if row["status"] == "resolved"]
    return {
        "strategy": strategy, "slippage_bps": slippage_bps, "signals": len(samples),
        "accepted": sum(row["status"] != "skipped" for row in samples),
        "skipped_busy": sum(row.get("reason") == "position_already_active" for row in samples),
        "rejection_reasons": dict(Counter(row["reason"] for row in samples if "reason" in row)),
        "observation_states": dict(states), "trades": len(resolved),
        "win_rate": sum(row["pnl"] > 0 for row in resolved) / len(resolved) if resolved else None,
        "final_balance": round(balance, 2), "return_pct": (balance / starting_balance - 1) * 100,
        "profit_factor": report["profit_factor"], "max_drawdown_pct": report["max_closed_drawdown_percent"],
        "mean_r": report["expectancy_r"], "report": report,
        "status": "blocked" if blocked_at is not None else "open" if active else "complete",
        "blocked_at": blocked_at, "equity_verified": blocked_at is None and not active and not any(row["status"] == "excluded" for row in samples),
        "blocked_gap": None,
        "excluded_trades": sum(row["status"] == "excluded" for row in samples),
        "session_closes": sum(row.get("outcome") == "session_close" for row in resolved),
        "samples": samples,
    }


def run_comparison(frames, now=None, commission_per_unit=0):
    now = int(time.time()) if now is None else now
    baseline = run_replay(frames, now, commission_per_unit=commission_per_unit)
    return {
        "version": "exness_portfolio_compare_v1", "policy_version": "flat_before_session_v2", "source": "exness_forex", "symbol": "XAUUSD",
        "as_of": now, "start": baseline["start"], "end": baseline["end"],
        "processed_bars": baseline["processed_bars"], "dataset_sha256": baseline["dataset_sha256"],
        "quality": baseline["quality"], "observation_only": True,
        "assumptions": {
            "starting_balance": 10000, "risk_fraction": 0.01, "max_notional_equity": 1,
            "max_open_positions": 1, "target_r": 2, "portfolio_simulation": True,
            "entry": "next_closed_m15_open_bid_ask", "same_bar_policy": "stop_first",
            "unknown_gap": "exclude_trade_from_stats; wallet not blocked",
            "session_close": "flat on last candle before scheduled closure (US DST); no entry within 4 bars of closure",
            "balance_and_drawdown": "closed_trades_only; unverified gaps and open positions excluded",
            "excluded_trade_pnl": "unknown, not zero; balance and returns conditional on included samples",
            "swap_included": False, "costs_calibrated": False, "lot_constraints_verified": False,
            "round_trip_commission_per_unit": commission_per_unit, "out_of_sample": False,
            "timeout": None,
        },
        "results": [portfolio(frames, strategy, slippage_bps=bps,
                              commission_per_unit=commission_per_unit, now=now)
                    for strategy in STRATEGIES for bps in (0, 1, 2)],
    }

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--slippage-bps", type=float, default=0)
    parser.add_argument("--commission-per-unit", type=float, default=0)
    parser.add_argument("--compare", action="store_true", help="single-wallet comparison of all strategies")
    parser.add_argument("--as-of", type=int, help="UTC epoch seconds; replay only candles closed by this instant")
    args = parser.parse_args()
    now = int(time.time()) if args.as_of is None else args.as_of
    if now <= 0:
        parser.error("--as-of must be positive")
    with Session() as session:
        frames = load_frames(session, now)
        if args.compare:
            report = run_comparison(frames, now, args.commission_per_unit)
        else:
            report = run_replay(frames, now, args.slippage_bps, args.commission_per_unit)
    output = args.output or Path("research-output/exness-strategy-compare.json" if args.compare
                                 else "research-output/exness-mtf-replay.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    temporary.replace(output)
    result = {"output": str(output), "assumptions": report["assumptions"]}
    if args.compare:
        result["results"] = [{key: value for key, value in row.items() if key not in ("samples", "report")}
                             for row in report["results"]]
    else:
        result.update(summary=report["summary"], by_side=report["by_side"])
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))

if __name__ == "__main__":
    main()
