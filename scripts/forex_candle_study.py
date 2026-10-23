"""Offline M15 candle-trigger study. No broker calls, DB writes or live tree changes."""

import argparse
import hashlib
import json
import math
from bisect import bisect_right
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import datetime, timezone
from multiprocessing import Pool
from pathlib import Path
from statistics import fmean
from unittest.mock import patch

from backend.app import forex_htf_v2 as engine
from backend.app.exness_sessions import session_closed_ahead
from backend.app.forex_detectors import M15_TRIGGERS, family_context
from backend.app.futures_rule_tree import CANDLE_PATTERNS
from scripts import forex_persist_study as source

PATTERNS = ("engulfing", "pin_bar", "star", "doji_reversal", "harami", "tweezer")
TARGETS = ("1.5R", "2R", "3R", "structural")
MODES = ("market", "break_2bars")
HORIZON = 96
STEP = 900


def valid_series(series, duration):
    previous = -1
    for bar in series:
        values = (bar.open, bar.high, bar.low, bar.close)
        if (
            bar.time <= previous
            or bar.time % duration
            or not all(math.isfinite(value) and value > 0 for value in values)
            or bar.low > min(bar.open, bar.close)
            or bar.high < max(bar.open, bar.close)
        ):
            raise ValueError("Invalid, duplicate or unaligned candle")
        previous = bar.time


def rates(quote, rows, stamp):
    if quote == "USD":
        return 1.0, 1.0
    pair, inverse = {
        "JPY": ("USDJPY", True),
        "CAD": ("USDCAD", True),
        "CHF": ("USDCHF", True),
        "GBP": ("GBPUSD", False),
    }[quote]
    bids, asks, times = rows[pair]
    index = bisect_right(times, stamp - STEP) - 1
    if index < 0 or stamp - (times[index] + STEP) > 4 * 86400:
        raise ValueError("Missing closed FX conversion candle")
    bid, ask = bids[index].close, asks[index].close
    return (1 / bid, 1 / ask) if inverse else (ask, bid)


def simulate(
    signal,
    index,
    bids,
    asks,
    mode,
    target_mode,
    slip_bps,
    commission,
    conversion=lambda stamp: (1.0, 1.0),
):
    direction = 1 if signal["side"] == "long" else -1
    slip = slip_bps / 10000
    stop, atr = signal["stop"], signal["htf_structure"]["atr"]
    trigger = bids[index]
    pending = trigger.high + 0.05 * atr if direction == 1 else trigger.low - 0.05 * atr
    first = index + 1
    for fill_index in range(first, min(len(bids), first + (1 if mode == "market" else 2))):
        bid, ask = bids[fill_index], asks[fill_index]
        if bid.time != bids[fill_index - 1].time + STEP:
            return {"status": "gap_before_fill"}
        if session_closed_ahead(bid.time, 4, STEP, signal["symbol"]):
            return {"status": "session_before_fill"}
        entry_bar, exit_bar = (ask, bid) if direction == 1 else (bid, ask)
        if direction * (exit_bar.open - stop) <= 0:
            return {"status": "invalidated_before_fill"}
        if mode == "market":
            ideal_entry = entry_bar.open
        else:
            hit = entry_bar.high >= pending if direction == 1 else entry_bar.low <= pending
            stop_hit = exit_bar.low <= stop if direction == 1 else exit_bar.high >= stop
            at_open = direction * (entry_bar.open - pending) >= 0
            if stop_hit and not at_open:
                return {"status": "ambiguous_or_invalidated_pending"}
            if not hit:
                continue
            ideal_entry = (
                max(entry_bar.open, pending) if direction == 1 else min(entry_bar.open, pending)
            )
        entry = ideal_entry * (1 + direction * slip)
        risk = direction * (entry - stop)
        if not 0.5 * atr <= risk <= 3 * atr:
            return {"status": "entry_risk_outside_live_bounds"}
        target = (
            signal["target"]
            if target_mode == "structural"
            else entry + direction * float(target_mode[:-1]) * risk
        )
        if direction * (target - entry) <= 0:
            return {"status": "target_behind_entry"}
        entry_loss_rate, _ = conversion(bid.time)
        target_rr = direction * (target - entry) / risk
        details = {
            "entry_time": bid.time,
            "entry": entry,
            "stop": stop,
            "target": target,
            "gross_target_r": target_rr,
            "risk_atr": risk / atr,
        }
        for exit_index in range(fill_index, min(len(bids), fill_index + HORIZON)):
            bid, ask = bids[exit_index], asks[exit_index]
            if exit_index > fill_index and bid.time != bids[exit_index - 1].time + STEP:
                return {**details, "status": "gap_open", "censored_at": bid.time}
            exit_bar = bid if direction == 1 else ask
            stop_hit = exit_bar.low <= stop if direction == 1 else exit_bar.high >= stop
            target_hit = exit_bar.high >= target if direction == 1 else exit_bar.low <= target
            if direction * (exit_bar.open - stop) <= 0:
                price, reason = exit_bar.open, "stop_gap"
            elif direction * (exit_bar.open - target) >= 0:
                price, reason = target, "target"
            elif stop_hit:
                price, reason = stop, "both_hit_stop_first" if target_hit else "stop"
            elif target_hit and mode != "market" and exit_index == fill_index and not at_open:
                continue
            elif target_hit:
                price, reason = target, "target"
            elif session_closed_ahead(bid.time, 2, STEP, signal["symbol"]):
                price, reason = exit_bar.close, "session_close"
            elif exit_index == fill_index + HORIZON - 1:
                price, reason = exit_bar.close, "time_exit"
            else:
                continue
            price *= 1 - direction * slip
            gross = direction * (price - entry)
            loss_rate, gain_rate = conversion(bid.time + STEP)
            net = gross * (gain_rate if gross >= 0 else loss_rate) - 2 * commission
            return {
                **details,
                "status": "resolved",
                "exit_time": bid.time + STEP,
                "exit": price,
                "reason": reason,
                "net_r": net / (risk * entry_loss_rate),
            }
        return {**details, "status": "open_end"}
    return {"status": "unfilled"}


def load_data(path):
    source.DATA = Path(path)
    rows = source.load()
    specs = json.loads((Path(path) / "forex_specs.json").read_text())
    paired = {}
    for symbol, frames in rows.items():
        for (frame, _), series in frames.items():
            valid_series(series, {"1m": 60, "15m": STEP, "1h": 3600, "4h": 14400}[frame])
        bids, asks = frames[("15m", "bid")], frames[("15m", "ask")]
        if [bar.time for bar in bids] != [bar.time for bar in asks]:
            raise ValueError(f"Unpaired bid/ask timestamps: {symbol}")
        if any(ask.close < bid.close or ask.open < bid.open for bid, ask in zip(bids, asks)):
            raise ValueError(f"Crossed bid/ask: {symbol}")
        paired[symbol] = bids, asks, [bar.time for bar in bids]
        spec = specs[symbol]
        fee = spec.get("commission_account_per_unit")
        if (
            spec.get("account_currency") != "USD"
            or fee is None
            or not math.isfinite(float(fee))
            or float(fee) < 0
        ):
            raise ValueError(f"Unsupported account currency or missing commission: {symbol}")
    return rows, specs, paired


def default_groups(symbol):
    groups = {}
    for tree in engine.g0_trees(symbol):
        children = tree["root"]["children"]
        if len(children) > 4:
            continue
        family = children[1]["family"]
        group = groups.setdefault(family, {"tree": deepcopy(tree), "triggers": {}})
        node = children[2]
        group["triggers"][node["family"]] = deepcopy(node["params"])
    for group in groups.values():
        children = group["tree"]["root"]["children"]
        children[2] = {**children[2], "family": "engulfing", "params": {}}
        children[3] = {"frame": "exit", "policy": "fixed_structural"}
        engine.validate_htf2_tree(group["tree"])
    return groups


def run_symbol(args):
    symbol, path, cutoff, end, split, slippage = args
    rows, specs, paired = load_data(path)
    bids, asks, _ = paired[symbol]
    hours, higher = rows[symbol][("1h", "bid")], rows[symbol][("4h", "bid")]
    hour_times, higher_times = [bar.time for bar in hours], [bar.time for bar in higher]
    spec = specs[symbol]
    groups = default_groups(symbol)
    original = dict(M15_TRIGGERS)
    used, events, rejected = set(), [], Counter()
    for index, candle in enumerate(bids):
        if candle.time < cutoff or candle.time + STEP > end or index < 100:
            continue
        hour_end = bisect_right(hour_times, candle.time + STEP - 3600)
        higher_end = bisect_right(higher_times, candle.time + STEP - 14400)
        closed_hours = hours[max(0, hour_end - 300) : hour_end]
        closed_higher = higher[max(0, higher_end - 80) : higher_end]
        history = bids[max(0, index - 200) : index]
        _, gain_rate = rates(spec["quote_currency"], paired, candle.time + STEP)
        fee = float(spec["commission_account_per_unit"])
        for family, group in groups.items():
            matches = []

            def detector(current, prior, side, setup, params, context):
                for trigger_family, trigger_params in group["triggers"].items():
                    h1_context = family_context(
                        trigger_family, trigger_params, current, closed_hours, closed_higher
                    )
                    if h1_context is not None and original[trigger_family](
                        current, prior, side, setup, trigger_params, h1_context
                    ):
                        matches.append("baseline")
                        break
                direction = 1 if side == "long" else -1
                window = [*prior[-9:], current]
                if (
                    len(window) == 10
                    and all(
                        after.time - before.time == STEP
                        for before, after in zip(window, window[1:])
                    )
                    and direction * (current.close - setup["level"]) > 0
                ):
                    matches.extend(
                        name for name in PATTERNS if CANDLE_PATTERNS[name](window, direction)
                    )
                return bool(matches)

            with patch.dict(M15_TRIGGERS, {"engulfing": detector}):
                signal, reason = engine.diagnose(
                    group["tree"],
                    candle,
                    closed_hours,
                    history,
                    closed_higher,
                    asks[index],
                    slippage_bps=slippage,
                    commission=fee / gain_rate,
                    symbol=symbol,
                )
            if signal is None:
                rejected[reason] += 1
                continue
            for pattern in matches:
                key = (family, pattern, signal["side"], signal["htf_structure"]["setup_time"])
                if key in used:
                    continue
                used.add(key)
                events.append(
                    {
                        "symbol": symbol,
                        "time": candle.time,
                        "index": index,
                        "h1": family,
                        "pattern": pattern,
                        "signal": {**signal, "symbol": symbol},
                    }
                )
    trades, busy = [], defaultdict(int)
    for event in sorted(events, key=lambda item: (item["time"], item["h1"], item["pattern"])):
        for mode in MODES:
            for target_mode in TARGETS:
                key = (event["pattern"], mode, target_mode)
                if event["time"] + STEP < busy[key]:
                    continue
                result = simulate(
                    event["signal"],
                    event["index"],
                    bids,
                    asks,
                    mode,
                    target_mode,
                    slippage,
                    float(spec["commission_account_per_unit"]),
                    lambda stamp: rates(spec["quote_currency"], paired, stamp),
                )
                if "entry_time" in result:
                    busy[key] = result.get("exit_time", result.get("censored_at", end))
                elif mode != "market":
                    busy[key] = event["time"] + 3 * STEP
                resolved_before_split = result.get("exit_time", end + 1) <= split
                unfilled_before_split = (
                    "entry_time" not in result and event["time"] + 3 * STEP <= split
                )
                phase = (
                    "train"
                    if resolved_before_split or unfilled_before_split
                    else "test"
                    if event["time"] >= split + (HORIZON + 2) * STEP
                    else "purged"
                )
                trades.append(
                    {
                        "symbol": symbol,
                        "time": event["time"],
                        "h1": event["h1"],
                        "pattern": event["pattern"],
                        "mode": mode,
                        "target_mode": target_mode,
                        "phase": phase,
                        **result,
                    }
                )
    print(f"{symbol}: {len(events)} signals, {len(trades)} attempts", flush=True)
    return {"symbol": symbol, "events": len(events), "rejected": dict(rejected), "trades": trades}


def stats(trades):
    closed = sorted(
        (row for row in trades if row["status"] == "resolved"),
        key=lambda row: (row["exit_time"], row["symbol"], row["time"]),
    )
    values = [row["net_r"] for row in closed]
    gains, losses = sum(max(0, value) for value in values), -sum(min(0, value) for value in values)
    equity = peak = drawdown = 0.0
    for value in values:
        equity += value
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    return {
        "attempts": len(trades),
        "n": len(values),
        "net_r": round(sum(values), 4),
        "mean_r": round(fmean(values), 4) if values else None,
        "win_rate": round(sum(value > 0 for value in values) / len(values), 4) if values else None,
        "profit_factor": round(gains / losses, 4) if losses else None,
        "closed_trade_drawdown_r": round(drawdown, 4),
        "mean_gross_target_r": round(fmean(row["gross_target_r"] for row in closed), 4)
        if closed
        else None,
        "statuses": dict(Counter(row["status"] for row in trades)),
    }


def summarize(trades):
    output = []
    for pattern in ("baseline", *PATTERNS):
        for mode in MODES:
            for target_mode in TARGETS:
                subset = [
                    row
                    for row in trades
                    if (row["pattern"], row["mode"], row["target_mode"])
                    == (pattern, mode, target_mode)
                ]
                output.append(
                    {
                        "pattern": pattern,
                        "mode": mode,
                        "target": target_mode,
                        **{
                            phase: stats([row for row in subset if row["phase"] == phase])
                            for phase in ("train", "test", "purged")
                        },
                        "test_by_symbol": {
                            symbol: stats(
                                [
                                    row
                                    for row in subset
                                    if row["phase"] == "test" and row["symbol"] == symbol
                                ]
                            )
                            for symbol in sorted({row["symbol"] for row in subset})
                        },
                    }
                )
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--symbols", nargs="+")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--slippage-bps", type=float, default=1.0)
    args = parser.parse_args()
    if (
        not math.isfinite(args.slippage_bps)
        or not 0 <= args.slippage_bps <= 100
        or args.workers < 1
    ):
        parser.error("Invalid slippage or worker count")
    if args.output.exists():
        parser.error("Output exists; choose a new report path")
    rows, specs, paired = load_data(args.data)
    symbols = args.symbols or sorted(specs)
    if not set(symbols) <= set(paired):
        parser.error("Unknown symbol")
    start = max(paired[symbol][0][0].time + source.WARMUP for symbol in symbols)
    end = min(paired[symbol][0][-1].time + STEP for symbol in symbols)
    split = int(start + 0.7 * (end - start)) // STEP * STEP
    jobs = [(symbol, str(args.data), start, end, split, args.slippage_bps) for symbol in symbols]
    with Pool(min(args.workers, len(jobs))) as pool:
        results = pool.map(run_symbol, jobs)
    trades = [trade for result in results for trade in result["trades"]]
    summary = summarize(trades)
    candidates = [
        row
        for row in summary
        if row["pattern"] != "baseline" and row["train"]["n"] >= 30 and row["train"]["mean_r"] > 0
    ]
    selected = max(candidates, key=lambda row: row["train"]["mean_r"], default=None)
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "input_sha256": hashlib.sha256(
            (args.data / "workspace_candles.sql.gz").read_bytes()
        ).hexdigest(),
        "specs_sha256": hashlib.sha256((args.data / "forex_specs.json").read_bytes()).hexdigest(),
        "start": start,
        "end": end,
        "split": split,
        "test_start": split + (HORIZON + 2) * STEP,
        "symbols": symbols,
        "slippage_bps_per_side": args.slippage_bps,
        "method": [
            "Closed H4 trend + default H1 setups + default ADX gate; H1 structure stop unchanged",
            "Baseline is union of unfiltered G0 triggers per H1 family, not deployed adaptive portfolio",
            "Fixed exits isolate entry quality; structural TP copied from live diagnose",
            "One first trigger per H1 setup/pattern; one open trade per symbol/config; H1 ties alphabetical",
            "Next-bar market or 2-bar breakout stop with 0.05 H1 ATR buffer",
            "Intrabar stop/target collision: stop first; breakout fill bar target ignored unless filled at open",
            "Bid/ask spread, adverse slippage each side, commission each side, closed-bar FX conversion",
            "No swap; current instrument specs applied historically; no margin/lot/portfolio capital model",
            "96-bar time exit; session close exit; missing candles censored, resume at next observed bar",
            "70/30 chronological split plus 98-bar test embargo; selection uses train only",
            "Drawdown from closed equal-risk trade R, not mark-to-market account equity",
        ],
        "coverage": {
            symbol: {
                f"{frame}_{price}": {
                    "n": len(series),
                    "first": series[0].time,
                    "last": series[-1].time,
                }
                for (frame, price), series in rows[symbol].items()
            }
            for symbol in symbols
        },
        "selected_on_train": selected,
        "summary": summary,
        "results": results,
    }
    with args.output.open("x") as handle:
        json.dump(report, handle, allow_nan=False, indent=1)
    print(json.dumps({"selected_on_train": selected, "report": str(args.output)}, allow_nan=False))


if __name__ == "__main__":
    main()
