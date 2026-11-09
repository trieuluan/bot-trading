"""Offline Exness H4/H1/M15 investigation; never changes live trees or orders.

Run: python -m scripts.forex_mtf_investigation --data DATA --output NEW.json
"""

import argparse
import bisect
import hashlib
import json
import random
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from statistics import fmean
from zoneinfo import ZoneInfo

from backend.app.exness_sessions import regular_session_gap
from scripts.forex_candle_study import load_data, rates

STEP = 900
HORIZON = 96
MODES = ("h1_direct", "m15_aligned", "m15_sweep", "m15_tight", "limit_half_body", "stop_h1_break")
TARGETS = (1.5, 2.0, 3.0)
LONDON = ZoneInfo("Europe/London")
NEW_YORK = ZoneInfo("America/New_York")


def ema(values, period):
    current, result = values[0], []
    for value in values:
        current += 2 / (period + 1) * (value - current)
        result.append(current)
    return result


def atr(rows):
    current, result = None, []
    for index, row in enumerate(rows):
        previous = rows[index - 1].close if index else row.close
        width = max(row.high - row.low, abs(row.high - previous), abs(row.low - previous))
        current = width if current is None else current + (width - current) / 14
        result.append(current)
    return result


def liquid(stamp):
    london = datetime.fromtimestamp(stamp, LONDON)
    new_york = datetime.fromtimestamp(stamp, NEW_YORK)
    return 8 <= london.hour < 17 or 8 <= new_york.hour < 12


def rollover(stamp):
    local = datetime.fromtimestamp(stamp, NEW_YORK)
    minutes = local.hour * 60 + local.minute
    return 16 * 60 + 30 <= minutes < 18 * 60


def clean_window(rows, seconds, symbol):
    return all(after.time - before.time == seconds or regular_session_gap(
        before.time, after.time, seconds, symbol) for before, after in zip(rows, rows[1:]))


def closed_index(times, available, duration):
    return bisect.bisect_right(times, available - duration) - 1


def signals(symbol, frames):
    hours, higher = frames[("1h", "bid")], frames[("4h", "bid")]
    higher_times = [row.time for row in higher]
    fast, slow = ema([row.close for row in higher], 20), ema([row.close for row in higher], 50)
    average = ema([row.close for row in hours], 20)
    ranges = atr(hours)
    for index in range(50, len(hours)):
        row = hours[index]
        available = row.time + 3600
        higher_index = closed_index(higher_times, available, 14400)
        if higher_index < 49 or available - (higher_times[higher_index] + 14400) >= 14400:
            continue
        if not clean_window(hours[index - 20:index + 1], 3600, symbol) or not clean_window(
                higher[higher_index - 49:higher_index + 1], 14400, symbol):
            continue
        prior = hours[index - 10:index]
        upper, lower = max(bar.high for bar in prior), min(bar.low for bar in prior)
        for direction in (1, -1):
            if (direction * (higher[higher_index].close - slow[higher_index]) <= 0
                    or direction * (fast[higher_index] - slow[higher_index]) <= 0):
                continue
            sweep = row.low < lower < row.close if direction == 1 else row.close < upper < row.high
            broken = row.close > upper if direction == 1 else row.close < lower
            pullback = (row.low <= average[index] <= row.close if direction == 1
                        else row.close <= average[index] <= row.high)
            pullback = pullback and direction * (row.close - row.open) > 0
            families = (["sweep"] if sweep else []) + (["squeeze"] if broken and (
                upper - lower) / ranges[index] <= 4 else []) + (["pullback"] if pullback else [])
            recent = hours[index - 3:index + 1]
            swing = min(bar.low for bar in recent) if direction == 1 else max(bar.high for bar in recent)
            stop = row.close - direction * max(
                direction * (row.close - swing) + 0.2 * ranges[index], 0.8 * ranges[index])
            for family in families:
                yield dict(symbol=symbol, family=family, direction=direction, available=available,
                           stop=stop, atr=ranges[index], close=row.close, open=row.open,
                           high=row.high, low=row.low,
                           setup_id=f"{symbol}:{available}:{direction}:{family}")


def fill(signal, mode, bids, asks, times, ranges):
    direction, available, stop = signal["direction"], signal["available"], signal["stop"]
    first = bisect.bisect_left(times, available)
    if first == len(bids) or times[first] != available:
        return {"status": "gap_before_fill", "release": available + STEP}
    if mode == "h1_direct":
        return dict(status="filled", index=first, ideal=(asks if direction == 1 else bids)[first].open,
                    intrabar=False, stop=stop, risk_atr=signal["atr"])
    for offset in range(4):
        index = first + offset
        if index >= len(bids):
            return {"status": "pending_end", "release": times[-1] + STEP}
        row, ask = bids[index], asks[index]
        if row.time != available + offset * STEP:
            return {"status": "gap_waiting", "release": row.time}
        if rollover(row.time):
            return {"status": "session_waiting", "release": row.time + STEP}
        exit_bar, entry_bar = (row, ask) if direction == 1 else (ask, row)
        if direction * (exit_bar.open - stop) <= 0:
            return {"status": "invalidated", "release": row.time + STEP}
        if mode in ("limit_half_body", "stop_h1_break"):
            level = (signal["close"] - direction * 0.5 * abs(signal["close"] - signal["open"])
                     if mode == "limit_half_body" else
                     (signal["high"] if direction == 1 else signal["low"]) + direction * 0.05 * signal["atr"])
            at_open = direction * (entry_bar.open - level) * (1 if mode == "stop_h1_break" else -1) >= 0
            touched = ((entry_bar.low <= level if direction == 1 else entry_bar.high >= level)
                       if mode == "limit_half_body" else
                       (entry_bar.high >= level if direction == 1 else entry_bar.low <= level))
            if at_open or touched:
                return dict(status="filled", index=index, ideal=entry_bar.open if at_open else level,
                            intrabar=not at_open, stop=stop, risk_atr=signal["atr"])
            if exit_bar.low <= stop if direction == 1 else exit_bar.high >= stop:
                return {"status": "invalidated", "release": row.time + STEP}
        else:
            if (exit_bar.low <= stop if direction == 1 else exit_bar.high >= stop):
                return {"status": "invalidated", "release": row.time + STEP}
            if index < 4 or any(times[cursor] - times[cursor - 1] != STEP
                                for cursor in range(index - 3, index + 1)):
                continue
            prior = bids[index - 4:index]
            swept = (row.low < min(bar.low for bar in prior) < row.close if direction == 1
                     else row.close < max(bar.high for bar in prior) < row.high)
            confirmed = swept if mode == "m15_sweep" else direction * (row.close - row.open) > 0
            if confirmed:
                next_index = index + 1
                if next_index >= len(bids):
                    return {"status": "pending_end", "release": row.time + STEP}
                if times[next_index] != row.time + STEP:
                    return {"status": "gap_before_fill", "release": times[next_index]}
                if mode == "m15_tight":
                    recent = bids[index - 3:index + 1]
                    swing = min(bar.low for bar in recent) if direction == 1 else max(bar.high for bar in recent)
                    stop = row.close - direction * max(
                        direction * (row.close - swing) + 0.2 * ranges[index], 0.8 * ranges[index])
                return dict(status="filled", index=next_index,
                            ideal=(asks if direction == 1 else bids)[next_index].open,
                            intrabar=False, stop=stop,
                            risk_atr=ranges[index] if mode == "m15_tight" else signal["atr"])
    return {"status": "unfilled", "release": available + 4 * STEP}


def simulate(signal, execution, bids, asks, spec, paired, target_r, slippage_bps=1.0):
    direction, first, stop = signal["direction"], execution["index"], execution["stop"]
    slip = slippage_bps / 10000
    entry = execution["ideal"] * (1 + direction * slip)
    risk = direction * (entry - stop)
    details = dict(entry_time=bids[first].time, entry=entry, stop=stop)
    if not 0.5 * execution["risk_atr"] <= risk <= 3.5 * execution["risk_atr"]:
        return {**details, "status": "risk_bounds", "release": bids[first].time + STEP}
    target = (entry + direction * target_r * risk if stop != signal["stop"] else
              signal["close"] + direction * target_r * direction * (signal["close"] - signal["stop"]))
    if target <= 0 or direction * (target - entry) <= 0:
        return {**details, "status": "invalid_target", "release": bids[first].time + STEP}
    if rollover(bids[first].time):
        return {**details, "status": "session_entry", "release": bids[first].time + STEP}
    entry_loss, _ = rates(spec["quote_currency"], paired, bids[first].time)
    ambiguous = False
    for index in range(first, min(len(bids), first + HORIZON)):
        bid, ask = bids[index], asks[index]
        if index > first and bid.time != bids[index - 1].time + STEP:
            return {**details, "status": "gap_open", "release": bid.time}
        row = bid if direction == 1 else ask
        stop_hit = row.low <= stop if direction == 1 else row.high >= stop
        target_hit = row.high >= target if direction == 1 else row.low <= target
        ambiguous |= stop_hit and target_hit or (index == first and execution["intrabar"] and (stop_hit or target_hit))
        if direction * (row.open - stop) <= 0:
            price, reason = row.open, "stop_gap"
        elif direction * (row.open - target) >= 0:
            price, reason = target, "target"
        elif stop_hit:
            price, reason = stop, "stop"
        elif target_hit and not (index == first and execution["intrabar"]):
            price, reason = target, "target"
        elif rollover(bid.time + STEP):
            price, reason = row.close, "pre_rollover"
        elif index == first + HORIZON - 1:
            price, reason = row.close, "time"
        else:
            continue
        exit_price = price * (1 - direction * slip)
        gross = direction * (exit_price - entry)
        loss, gain = rates(spec["quote_currency"], paired, bid.time + STEP)
        conversion = gain if gross >= 0 else loss
        denominator = risk * entry_loss
        fee_r = 2 * float(spec["commission_account_per_unit"]) / denominator
        slip_r = (execution["ideal"] + price) * slip * conversion / denominator
        spread_r = ((asks[first].open - bids[first].open) + (ask.open - bid.open)) / 2 * conversion / denominator
        net_r = gross * conversion / denominator - fee_r
        return {**details, "status": "resolved", "release": bid.time + STEP,
                "exit_time": bid.time + STEP, "exit": exit_price, "target": target,
                "net_r": net_r, "stress_r": net_r - fee_r - slip_r - spread_r,
                "fee_r": fee_r, "slip_r": slip_r, "spread_r": spread_r,
                "ambiguous": bool(ambiguous), "reason": reason}
    return {**details, "status": "open_end", "release": bids[-1].time + STEP}


def weekly_interval(trades):
    groups = defaultdict(list)
    for trade in trades:
        groups[(trade["available"] + 3 * 86400) // (7 * 86400)].append(trade["net_r"])
    weeks = list(groups.values())
    if len(weeks) < 3:
        return None
    rng = random.Random(9217)
    means = []
    for _ in range(500):
        draw = [rng.choice(weeks) for _ in weeks]
        means.append(sum(map(sum, draw)) / sum(map(len, draw)))
    means.sort()
    return [round(means[12], 4), round(means[487], 4)]


def stats(trades):
    resolved = [trade for trade in trades if trade["status"] == "resolved"]
    values = [trade["net_r"] for trade in resolved]
    losses = -sum(value for value in values if value < 0)
    clean = [trade["net_r"] for trade in resolved if not trade["ambiguous"]]
    return dict(n=len(values), statuses=dict(Counter(trade["status"] for trade in trades)),
                mean_r=round(fmean(values), 4) if values else None,
                total_r=round(sum(values), 3),
                pf=round(sum(value for value in values if value > 0) / losses, 3) if losses else None,
                win_rate=round(sum(value > 0 for value in values) / len(values), 4) if values else None,
                stress_r=round(fmean(trade["stress_r"] for trade in resolved), 4) if values else None,
                cost_r=round(fmean(trade["fee_r"] + trade["slip_r"] + trade["spread_r"] for trade in resolved), 4) if values else None,
                weeks=len({(trade["available"] + 3 * 86400) // (7 * 86400) for trade in resolved}),
                weekly_bootstrap95=weekly_interval(resolved),
                ambiguous=sum(trade["ambiguous"] for trade in resolved),
                unambiguous_mean_r=round(fmean(clean), 4) if clean else None)


def partition(stamp, first_split, second_split):
    if stamp < first_split:
        return "discovery"
    if stamp < first_split + (HORIZON + 4) * STEP:
        return "embargo"
    if stamp < second_split:
        return "validation"
    if stamp < second_split + (HORIZON + 4) * STEP:
        return "embargo"
    return "recent"


def paired_comparisons(records):
    groups = defaultdict(dict)
    for trade in records:
        if trade["symbol"] != "XAUUSD":
            groups[trade["config"]][trade["setup_id"]] = trade
    result = {}
    zero_statuses = {"unfilled", "invalidated", "risk_bounds", "invalid_target", "session_entry", "session_waiting"}
    for key, alternatives in groups.items():
        family, mode, target, session = key.split("|")
        if mode == "h1_direct":
            continue
        control = groups[f"{family}|h1_direct|{target}|{session}"]
        paired = []
        for setup_id in sorted(set(control) & set(alternatives)):
            first, second = control[setup_id], alternatives[setup_id]
            if first["status"] != "resolved" or second["status"] not in zero_statuses | {"resolved"}:
                continue
            value = second["net_r"] if second["status"] == "resolved" else 0.0
            paired.append(dict(available=first["available"], net_r=value - first["net_r"],
                               baseline_r=first["net_r"], alternative_r=value, fold=first["fold"]))
        def comparison(items):
            return {"n": len(items), "mean_delta_r": round(fmean(item["net_r"] for item in items), 4) if items else None,
                    "mean_baseline_r": round(fmean(item["baseline_r"] for item in items), 4) if items else None,
                    "mean_alternative_per_opportunity_r": round(fmean(item["alternative_r"] for item in items), 4) if items else None,
                    "weekly_delta95": weekly_interval(items)}
        result[key] = {"all": comparison(paired), **{fold: comparison([item for item in paired if item["fold"] == fold])
                       for fold in ("discovery", "validation", "recent")}}
    return result


def run(data, output):
    if output.exists():
        raise ValueError("Output exists; choose a new output path")
    rows, specs, paired = load_data(data)
    cutoff = int((data / "workspace_candles.sql.gz").stat().st_mtime)
    for symbol, (bids, asks, _) in list(paired.items()):
        if any(ask.high < bid.high or ask.low < bid.low for bid, ask in zip(bids, asks)):
            raise ValueError(f"Crossed bid/ask extremes: {symbol}")
        closed = [(bid, ask) for bid, ask in zip(bids, asks) if bid.time + STEP <= cutoff]
        paired[symbol] = ([item[0] for item in closed], [item[1] for item in closed], [item[0].time for item in closed])
    start = max(values[0][0].time for values in paired.values()) + 14 * 86400
    end = min(values[0][-1].time for values in paired.values()) + STEP
    split1 = (start + int((end - start) * 0.6)) // 86400 * 86400
    split2 = (start + int((end - start) * 0.8)) // 86400 * 86400
    records, coverage = [], {}
    for symbol in sorted(specs):
        bids, asks, times = paired[symbol]
        ranges = atr(bids)
        busy = defaultdict(int)
        coverage[symbol] = {"first": times[0], "last_close": times[-1] + STEP, "m15": len(bids),
                            "unverified_m15_gaps": sum(after.time - before.time != STEP and not regular_session_gap(
                                before.time, after.time, STEP, symbol) for before, after in zip(bids, bids[1:]))}
        for signal in signals(symbol, rows[symbol]):
            available = signal["available"]
            fold = partition(available, split1, split2)
            if not start <= available < end or fold == "embargo":
                continue
            boundary = {"discovery": split1, "validation": split2, "recent": end}[fold]
            if available + (HORIZON + 4) * STEP > boundary:
                continue
            for mode in MODES:
                execution = fill(signal, mode, bids, asks, times, ranges)
                entry_time = times[execution["index"]] if execution["status"] == "filled" else available
                for session in ("all", "liquid"):
                    if session == "liquid" and not liquid(entry_time):
                        continue
                    for target in TARGETS:
                        key = f"{signal['family']}|{mode}|{target}R|{session}"
                        if available < busy[key]:
                            records.append({**signal, "config": key, "fold": fold, "status": "busy"})
                            continue
                        outcome = (simulate(signal, execution, bids, asks, specs[symbol], paired, target)
                                   if execution["status"] == "filled" else execution)
                        busy[key] = outcome["release"]
                        records.append({**signal, **outcome, "config": key, "fold": fold})
        print(symbol, "done", flush=True)
    summary = {}
    for universe in ("fx", "gold"):
        subset = [trade for trade in records if (trade["symbol"] == "XAUUSD") == (universe == "gold")]
        by_config = defaultdict(list)
        for trade in subset:
            by_config[trade["config"]].append(trade)
        table = {}
        for key, trades in sorted(by_config.items()):
            table[key] = {"all": stats(trades), **{fold: stats([trade for trade in trades if trade["fold"] == fold])
                         for fold in ("discovery", "validation", "recent")},
                         "by_symbol": {symbol: stats([trade for trade in trades if trade["symbol"] == symbol])
                                       for symbol in sorted({trade["symbol"] for trade in trades})}}
        eligible = [key for key, value in table.items() if value["discovery"]["n"] >= 30
                    and value["discovery"]["weekly_bootstrap95"] is not None
                    and value["discovery"]["weekly_bootstrap95"][0] > 0]
        selected = max(eligible, key=lambda key: (table[key]["discovery"]["weekly_bootstrap95"][0], key), default=None)
        summary[universe] = {"train_selected": selected, "configs": table}
    report = dict(start=start, end=end, split1=split1, split2=split2, as_of=cutoff,
                  source_sha256=hashlib.sha256((data / "workspace_candles.sql.gz").read_bytes()).hexdigest(),
                  specs_sha256=hashlib.sha256((data / "forex_specs.json").read_bytes()).hexdigest(),
                  coverage=coverage, summary=summary, paired_fx=paired_comparisons(records), records=records,
                  method=["Exploratory reused data; 60/20/20 calendar split; 100-M15 embargo, purge to fold boundaries",
                          "H4 closed EMA20/50 trend; H1 sweep10, compression break10 <=4ATR, or EMA20 pullback",
                          "Six entries; H1 stop/target frozen at setup except explicitly labelled M15 tight-stop control",
                          "Only closed bars; next-open market; pending valid four consecutive M15 bars",
                          "One waiting/open position per symbol/config; no overlapping per-config trades",
                          "Paired deltas compare common non-busy setups; unfilled/cancelled alternatives=0R, gaps excluded",
                          "Bid/ask, 1bps adverse slippage each side, dated Oct23 specs/commission applied historically",
                          "No overnight carry: exit at 16:30 New York, forbid entries until 18:00; DST-aware liquid gate",
                          "96-M15 time cap; gaps and unresolved tails censored, never zero-filled",
                          "Stop-first OHLC; intrabar pending fill-bar TP not credited; ambiguity reported",
                          "Stress reprices same trades at another fee+slip+estimated spread cost; frozen risk denominator",
                          "Spread cost attribution uses bar-open spread approximation; spread already inherent in PnL",
                          "Weekly cluster bootstrap, 500 draws, not multiple-testing-adjusted; no significance claim",
                          "XAUUSD reported separately; no lot rounding, margin, market impact, macro-event or account portfolio model"])
    output.write_text(json.dumps(report, indent=1, allow_nan=False))
    for universe, result in summary.items():
        print(universe, "train-selected:", result["train_selected"])
        ranked = sorted(result["configs"].items(), key=lambda item: item[1]["all"]["mean_r"] or -100, reverse=True)
        for key, value in ranked[:5]:
            print(key, [(fold, value[fold]["n"], value[fold]["mean_r"]) for fold in ("discovery", "validation", "recent")])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run(args.data, args.output)
