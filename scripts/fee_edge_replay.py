"""Offline exit/target study with signed API fees; never reads or writes live DB.

Run: /tmp/btv/bin/python -m scripts.fee_edge_replay
Reused historical folds are exploratory, not a new holdout.
"""

import bisect
import hashlib
import json
import math
from collections import defaultdict
from itertools import product
from pathlib import Path

from scripts import htf_portfolio_replay as native
from scripts import regime_setup_study as h1

FEES = Path("research-output/mexc-api-fees-20261108.json")
OUTPUT = Path("research-output/fee-edge-replay-20261108.json")
FOCUS = (("sweep_reclaim", -1, 1, "down"), ("bos_retest", 1, 0, "up"),
         ("hl_pullback", 1, 0, "up"), ("sweep_reclaim", 1, 1, "up"),
         ("breakout", -1, 0, "range"))


def hourly_signals(symbol, rows):
    hours, higher = h1.bars(rows, 3600), h1.bars(rows, 14400)
    stamps, opens, highs, lows, closes = map(list, zip(*hours))
    ranges, regimes = h1.atr(highs, lows, closes), h1.h4_regimes(higher)
    higher_closes = [row[0] + 14400 for row in higher]
    times = {bar.time: index for index, bar in enumerate(rows)}
    pivots, cursor = h1.pivots(highs, lows), 0
    high_pivot = low_pivot = broken_high = broken_low = None
    for index in range(h1.LOOK + 1, len(hours)):
        while cursor < len(pivots) and pivots[cursor][0] <= index:
            if pivots[cursor][1] == "H":
                high_pivot = pivots[cursor][2]
            else:
                low_pivot = pivots[cursor][2]
            cursor += 1
        if high_pivot and closes[index] > high_pivot:
            broken_high, high_pivot = (index, high_pivot), None
        if low_pivot and closes[index] < low_pivot:
            broken_low, low_pivot = (index, low_pivot), None
        stamp = stamps[index] + 3600
        higher_index = bisect.bisect_right(higher_closes, stamp) - 1
        entry_index = times.get(stamp)
        if higher_index < 0 or entry_index is None:
            continue
        state = (high_pivot, low_pivot, broken_high, broken_low)
        for name, direction, stop in h1.signals(
                stamps, opens, highs, lows, closes, ranges, index, state):
            for setup, side, regime_index, regime in FOCUS:
                if name == setup and direction == side and regimes[higher_index][regime_index] == regime:
                    seed = f"h1_{name}_{side}_{regime}"
                    yield dict(symbol=symbol, seed=seed, entry_idx=entry_index,
                               entry_time=stamp, direction=direction, stop=stop, max_m15_bars=192)


def simulate(rows, fair, rates, signal, target_r, mode, hold, boundary, fee, slip=0.0002):
    entry_index, direction, stop = signal["entry_idx"], signal["direction"], signal["stop"]
    if entry_index + hold > boundary:
        return None
    raw_entry = rows[entry_index].open
    if direction * (raw_entry - stop) <= 0:
        return None
    entry = raw_entry * (1 + direction * slip)
    risk = direction * (entry - stop)
    target = entry + direction * target_r * risk
    if target <= 0:
        return None
    remaining, realized, funding, exit_fees, partial = 1.0, 0.0, 0.0, 0.0, False
    current_stop = stop
    end_index = entry_index + hold - 1
    reason, raw_exit, exit_index = "time", rows[end_index].close, end_index
    for cursor in range(entry_index, end_index + 1):
        bar = rows[cursor]
        if cursor > entry_index:
            funding += remaining * direction * fair[cursor].open * rates.get(bar.time, 0.0)
        if direction * (bar.open - current_stop) <= 0:
            raw_exit, reason, exit_index = bar.open, "stop_gap", cursor
            break
        if bar.low <= current_stop if direction == 1 else bar.high >= current_stop:
            raw_exit, reason, exit_index = current_stop, "stop", cursor
            break
        if bar.high >= target if direction == 1 else bar.low <= target:
            raw_exit, reason, exit_index = target, "target", cursor
            break
        if cursor == end_index or direction * (bar.close - entry) < risk:
            continue
        if mode == "partial" and not partial:
            fill = bar.close * (1 - direction * slip)
            realized += 0.5 * direction * (fill - entry)
            exit_fees += 0.5 * fill * fee
            remaining, partial = 0.5, True
        proposed = current_stop
        if mode in ("be", "partial"):
            costs = entry * fee + funding + exit_fees - realized
            proposed = (direction * entry + costs / remaining) / (direction - fee) / (1 - direction * slip)
        elif mode == "trail":
            proposed = bar.close - direction * risk
        if proposed > 0 and direction * (bar.close - proposed) > 0:
            current_stop = max(current_stop, proposed) if direction == 1 else min(current_stop, proposed)
    exit_fill = raw_exit * (1 - direction * slip)
    fees = entry * fee + exit_fees + remaining * exit_fill * fee
    net = realized + remaining * direction * (exit_fill - entry) - fees - funding
    return dict(symbol=signal["symbol"], arm=signal["seed"], entry_idx=entry_index,
                exit_idx=exit_index, entry_time=rows[entry_index].time,
                exit_time=rows[exit_index].time + (0 if reason == "stop_gap" else 900),
                entry=entry, exit_fill=exit_fill, direction=direction, risk=risk,
                net_r=net / risk, fee=fee, fee_r=fees / risk, funding_r=funding / risk,
                funding_per_unit=funding, risk_pct=100 * risk / entry,
                cost_r=(fees + funding) / risk, reason=reason, partial=partial)


def replay(data, signals, fees, folds, target, mode, hold_scale, gate, stress=1):
    trades, busy = [], defaultdict(int)
    rates = {symbol: native.funding_map(pack[4]) for symbol, pack in data.items()}
    funding_times = {symbol: sorted(values) for symbol, values in rates.items()}
    for signal in sorted(signals, key=lambda row: (row["entry_time"], row["symbol"])):
        symbol, stamp = signal["symbol"], signal["entry_time"]
        rows, fair = data[symbol][2:4]
        if signal["entry_idx"] < busy[symbol]:
            continue
        if gate == "utc_12_20" and not 12 <= stamp % 86400 / 3600 < 20:
            continue
        if gate == "not_paying_last":
            known = bisect.bisect_left(funding_times[symbol], stamp) - 1
            if known < 0 or signal["direction"] * rates[symbol][funding_times[symbol][known]] > 0:
                continue
        fold, _, end = next(item for item in folds if item[1] <= stamp < item[2])
        boundary = (end - rows[0].time) // 900
        fee = fees[symbol]["taker_bps"] / 10000 * stress
        hold = int(signal["max_m15_bars"] * hold_scale)
        trade = simulate(rows, fair, rates[symbol], signal, target, mode, hold, boundary,
                         fee, 0.0002 * stress)
        if trade is not None:
            trade["fold"] = fold
            trades.append(trade)
            busy[symbol] = trade["exit_idx"] + 1
    return trades


def metrics(trades):
    result = native.trade_metrics(trades)
    if trades:
        result["mean_fee_r"] = sum(row["fee_r"] for row in trades) / len(trades)
        result["mean_funding_r"] = sum(row["funding_r"] for row in trades) / len(trades)
        result["positive_symbols"] = sum(
            sum(row["net_r"] for row in trades if row["symbol"] == symbol) > 0
            for symbol in {row["symbol"] for row in trades})
    return result


def check():
    from backend.app.market import Candle

    signal = dict(symbol="TEST", seed="test", entry_idx=0, direction=1, stop=98)
    rows = [Candle(0, 100, 101, 99, 100, 0), Candle(900, 97, 103, 96, 100, 0)]
    trade = simulate(rows, rows, {900: 0.001}, signal, 2, "fixed", 2, 2, 0)
    assert trade["reason"] == "stop_gap" and trade["net_r"] < -1.4
    assert math.isclose(trade["funding_per_unit"], 0.097)
    rows = [Candle(0, 100, 105, 97, 104, 0)]
    assert simulate(rows, rows, {}, signal, 2, "fixed", 1, 1, 0, 0)["net_r"] == -1
    assert simulate(rows, rows, {}, signal, 2, "fixed", 2, 1, 0, 0) is None
    rows = [Candle(0, 100, 103, 99, 102.5, 0), Candle(900, 102.5, 103, 100, 100, 0)]
    trade = simulate(rows, rows, {900: 0.001}, signal, 3, "partial", 2, 2, 0.0008)
    assert trade["partial"] and math.isclose(trade["funding_per_unit"], 0.05125)
    short = {**signal, "direction": -1, "stop": 102}
    rows = [Candle(0, 100, 101, 95, 96, 0)]
    assert simulate(rows, rows, {}, short, 2, "fixed", 1, 1, 0, 0)["net_r"] == 2
    assert simulate(rows, rows, {}, short, 2, "fixed", 1, 1, 0.0008)["net_r"] < 2


def main():
    check()
    data, first, end, digest = native.load_data()
    fee_snapshot = json.loads(FEES.read_text())
    fees, folds = fee_snapshot["fees"], native.build_folds(first, end)
    if set(fees) != set(data) or any(
            not math.isfinite(row["taker_bps"]) or not 0 <= row["taker_bps"] <= 100
            for row in fees.values()):
        raise ValueError("Fee snapshot must cover all dataset pairs with valid rates")
    grouped = defaultdict(list)
    for symbol, pack in sorted(data.items()):
        for signal in [*hourly_signals(symbol, pack[2]), *native.seed_signals(symbol, pack[2])]:
            grouped[signal["seed"]].append(signal)
    configs, all_trades = {}, {}
    print("Period:", native.iso(first), native.iso(end), "days:", (end - first) / 86400, flush=True)
    for seed, signals in sorted(grouped.items()):
        gates = ("all", "not_paying_last") if seed == "d1_squeeze" else ("all", "not_paying_last", "utc_12_20")
        for target, mode, hold, gate in product((1, 2, 3), ("fixed", "be", "trail", "partial"), (0.5, 1), gates):
            key = f"{seed}|tp{target}|{mode}|hold{hold}|{gate}"
            trades = replay(data, signals, fees, folds, target, mode, hold, gate)
            configs[key] = dict(seed=seed, target_r=target, mode=mode, hold_scale=hold, gate=gate,
                                folds={label: metrics([row for row in trades if row["fold"] == label])
                                       for label, _, _ in folds})
            all_trades[key] = trades
        print(seed, "signals:", len(signals), "configs:", len(configs), flush=True)
    def discovery(key):
        return configs[key]["folds"]["discovery"]
    ranked = sorted((key for key in configs if discovery(key)["n"] >= 30),
                    key=lambda key: (discovery(key)["mean_r"], key), reverse=True)
    finalists = ranked[:5]
    baseline = [key for key in configs if "|tp2|fixed|hold1|all" in key]
    detailed = {}
    for key in dict.fromkeys([*baseline, *finalists]):
        trades, config = all_trades[key], configs[key]
        stressed = replay(data, grouped[config["seed"]], fees, folds, config["target_r"],
                          config["mode"], config["hold_scale"], config["gate"], stress=2)
        detailed[key] = dict(metrics=metrics(trades), week_ci95=native.week_bootstrap_ci(trades),
                             by_symbol={symbol: metrics([row for row in trades if row["symbol"] == symbol])
                                        for symbol in data}, double_cost=metrics(stressed),
                             double_cost_by_fold={label: metrics([row for row in stressed if row["fold"] == label])
                                                  for label, _, _ in folds},
                             week_ci95_by_fold={label: native.week_bootstrap_ci(
                                 [row for row in trades if row["fold"] == label]) for label, _, _ in folds})
        if config["mode"] != "partial":
            portfolio = native.simulate_event_portfolio(trades, data)
            detailed[key]["uncapped_portfolio_1pct"] = {
                name: value for name, value in portfolio.items() if name not in ("ledger", "equity_curve")}
    baseline_checks = {}
    for key in baseline:
        config = configs[key]
        sample = all_trades[key]
        baseline_checks[key] = len(sample)
        for trade in sample:
            pack = data[trade["symbol"]]
            signal = dict(symbol=trade["symbol"], seed=config["seed"],
                          entry_idx=trade["entry_idx"], direction=trade["direction"],
                          stop=trade["entry"] - trade["direction"] * trade["risk"])
            original = next(row for row in grouped[config["seed"]]
                            if row["symbol"] == trade["symbol"] and row["entry_idx"] == trade["entry_idx"])
            boundary = next(stop for label, _, stop in folds if label == trade["fold"])
            expected = native.simulate_m15_trade(
                pack[2], pack[3], native.funding_map(pack[4]), signal["entry_idx"],
                signal["direction"], original["stop"], "2R", original["max_m15_bars"],
                (boundary - first) // 900, fee=fees[trade["symbol"]]["taker_bps"] / 10000)
            if expected is None or not math.isclose(expected["net_r"], trade["net_r"], abs_tol=1e-10):
                raise AssertionError(f"Baseline differs from existing M15 replay: {key}")
    passing = [key for key in ranked if all(configs[key]["folds"][label]["n"] >= 15
               and (configs[key]["folds"][label]["mean_r"] or 0) > 0 for label, _, _ in folds)]
    for key in dict.fromkeys([*baseline, *finalists]):
        print(key, " | ", " | ".join(f"{label}: n={configs[key]['folds'][label]['n']} "
              f"R={configs[key]['folds'][label]['mean_r']}" for label, _, _ in folds), flush=True)
    report = dict(data_sha256=digest, fee_snapshot=fee_snapshot,
                  code_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                  helper_hashes={name: hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest()
                                 for name, module in (("native", native), ("h1", h1))},
                  period_utc=[native.iso(first), native.iso(end)], folds=folds,
                  signal_counts={seed: len(rows) for seed, rows in grouped.items()},
                  config_count=len(configs), configs=configs, train_only_finalists=finalists,
                  baseline_parity_counts=baseline_checks,
                  exploratory_all_folds_positive=passing, detailed=detailed,
                  limitations=["All history previously inspected; no fresh holdout and no multiple-testing correction",
                               "Signals whose full maximum holding horizon crosses fold end are purged",
                               "One position per symbol per arm; next M15 open entry; stop-first; adverse gaps",
                               "Market only: taker fee snapshot on both legs, 2 bps slippage per fill, actual settlements",
                               "Past funding filter uses settlements strictly before entry, not future funding",
                               "BE/trailing stops update only after closed M15 and apply from next M15",
                               "Partial exits sell half at a closed M15 >=1R, not optimistic intrabar fills",
                               "Uncapped portfolio is a research scenario: no exchange margin/liquidation, not production sizing",
                               "Historical fees are unknown: current account fee schedule is applied to all history",
                               "No production tree, learner epoch or live entry policy changed"])
    OUTPUT.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print("All-fold-positive exploratory cells:", len(passing), "report:", OUTPUT, flush=True)


if __name__ == "__main__":
    main()
