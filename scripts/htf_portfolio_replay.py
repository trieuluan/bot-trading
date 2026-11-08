"""Research-only native D1/H4 seeds and unlimited-entry, event-time portfolio replay.

Run: /private/tmp/btvenv3/bin/python -m scripts.htf_portfolio_replay
"""

import bisect
import hashlib
import json
import math
import pickle
import random
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import fmean

from backend.app.futures_learner import FEE, SLIP
from backend.app.mexc import validate_candles
from scripts.price_action_replay import atr, bucket, ema

DATA = Path("research-output/candle-discovery-20261105/data.pkl")
MANIFEST = DATA.with_name("data-manifest.json")
OUTPUT = Path("research-output/htf-portfolio-replay.json")
SEEDS = {
    "d1_squeeze": {
        "seconds": 86400, "warmup": 30, "hold_bars": 15,
        "setup": "Prior 20 D1 high-low width / closed signal ATR14 <= 4.5; aligned-body close breaks range; long close > EMA50 and EMA20 > EMA50, short inverse",
    },
    "h4_sweep": {
        "seconds": 14400, "warmup": 60, "hold_bars": 30,
        "setup": "Wick sweeps prior 20 H4 extreme then aligned-body close reclaims; long if latest closed D1 close > EMA20, short if below",
    },
}
PORTFOLIOS = {
    "D1_2R": {"d1_squeeze_2R"},
    "D1_BE": {"d1_squeeze_BE"},
    "H4_2R": {"h4_sweep_2R"},
    "H4_BE": {"h4_sweep_BE"},
    "Combined_2R": {"d1_squeeze_2R", "h4_sweep_2R"},
    "Combined_BE": {"d1_squeeze_BE", "h4_sweep_BE"},
    "Combined_D1_BE_H4_2R": {"d1_squeeze_BE", "h4_sweep_2R"},
}


def iso(stamp):
    return datetime.fromtimestamp(stamp, timezone.utc).isoformat()


def build_folds(first_ts, last_ts):
    span = last_ts - first_ts
    first_split = first_ts + int(span * 0.50) // 86400 * 86400
    second_split = first_ts + int(span * 0.75) // 86400 * 86400
    return (("discovery", first_ts, first_split), ("validation", first_split, second_split),
            ("reused_holdout", second_split, last_ts))


def funding_map(funding):
    totals = defaultdict(float)
    for record in funding:
        totals[record["time"]] += record["rate"]
    return totals


def load_data():
    content = DATA.read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    if digest != json.loads(MANIFEST.read_text())["data_sha256"]:
        raise ValueError("Dataset hash differs from frozen manifest")
    raw = pickle.loads(content)
    first = max(pack[2][0].time for pack in raw.values())
    end = min(pack[2][-1].time for pack in raw.values()) + 900
    data = {}
    for symbol, pack in raw.items():
        rows = [bar for bar in pack[2] if first <= bar.time < end]
        fair = [bar for bar in pack[3] if first <= bar.time < end]
        validate_candles(rows)
        validate_candles(fair)
        if [bar.time for bar in rows] != [bar.time for bar in fair]:
            raise ValueError(f"Trade/mark timestamps differ: {symbol}")
        if len(rows) != (end - first) // 900:
            raise ValueError(f"Incomplete common period: {symbol}")
        times = [record["time"] for record in pack[4]]
        if times != sorted(set(times)) or any(
                not math.isfinite(record["rate"]) or record["time"] % 900 for record in pack[4]):
            raise ValueError(f"Invalid funding: {symbol}")
        if not times or times[0] > first or times[-1] < end - 86400:
            raise ValueError(f"Missing funding coverage: {symbol}")
        data[symbol] = (pack[0], end, rows, fair, pack[4])
    return data, first, end, digest


def seed_signals(symbol, rows):
    times = {bar.time: index for index, bar in enumerate(rows)}
    daily = bucket(rows, 86400)
    daily_times = [bar.time + 86400 for bar in daily]
    daily_ema = ema([bar.close for bar in daily], 20)
    for seed, spec in SEEDS.items():
        seconds = spec["seconds"]
        frames = daily if seconds == 86400 else bucket(rows, seconds)
        averages = ema([bar.close for bar in frames], 20)
        slow = ema([bar.close for bar in frames], 50)
        ranges = atr(frames)
        for index in range(spec["warmup"], len(frames)):
            bar = frames[index]
            entry_idx = times.get(bar.time + seconds)
            if entry_idx is None:
                continue
            prior = frames[index - 20:index]
            upper, lower = max(item.high for item in prior), min(item.low for item in prior)
            if ranges[index] <= 0:
                continue
            for direction in (1, -1):
                if direction * (bar.close - bar.open) <= 0:
                    continue
                if seed == "d1_squeeze":
                    aligned = direction * (bar.close - slow[index]) > 0 and direction * (averages[index] - slow[index]) > 0
                    broken = bar.close > upper if direction == 1 else bar.close < lower
                    allowed = aligned and broken and (upper - lower) / ranges[index] <= 4.5
                else:
                    daily_idx = bisect.bisect_right(daily_times, bar.time + seconds) - 1
                    if daily_idx < 20:
                        continue
                    aligned = direction * (daily[daily_idx].close - daily_ema[daily_idx]) > 0
                    swept = bar.low < lower < bar.close if direction == 1 else bar.close < upper < bar.high
                    allowed = aligned and swept
                if not allowed:
                    continue
                recent = frames[index - 4:index + 1]
                swing = min(item.low for item in recent) if direction == 1 else max(item.high for item in recent)
                distance = max(direction * (bar.close - swing) + 0.25 * ranges[index], ranges[index])
                stop = bar.close - direction * distance
                if stop <= 0 or not math.isfinite(stop):
                    raise ValueError(f"Invalid signal stop: {symbol}/{seed}")
                yield dict(symbol=symbol, seed=seed, entry_idx=entry_idx, entry_time=bar.time + seconds,
                           signal_time=bar.time, direction=direction, stop=stop,
                           max_m15_bars=spec["hold_bars"] * seconds // 900)


def simulate_m15_trade(m15, fair, funding_by_time, entry_idx, direction, stop, mode,
                       max_m15_bars, boundary_idx, fee=FEE, slip=SLIP):
    if direction not in (1, -1) or mode not in ("2R", "BE", "be1r_2r"):
        raise ValueError("Invalid side or exit mode")
    if not 0 <= fee < 1 or not 0 <= slip < 1 or not math.isfinite(stop) or stop <= 0:
        raise ValueError("Invalid costs or stop")
    if not isinstance(max_m15_bars, int) or max_m15_bars <= 0 or len(fair) != len(m15):
        raise ValueError("Invalid holding period or fair-price alignment")
    if not 0 <= entry_idx < min(boundary_idx, len(m15)):
        return None
    raw_entry = m15[entry_idx].open
    if direction * (raw_entry - stop) <= 0:
        return None
    entry = raw_entry * (1 + direction * slip)
    risk = direction * (entry - stop)
    if risk <= 0 or not math.isfinite(risk):
        return None
    target = entry + direction * 2 * risk
    if target <= 0:
        return None
    current_stop, funding_cost = stop, 0.0
    max_idx = min(len(m15), boundary_idx) - 1
    end_idx = min(max_idx, entry_idx + max_m15_bars - 1)
    exit_idx = end_idx
    exit_raw = m15[end_idx].close
    reason = "time" if entry_idx + max_m15_bars - 1 <= max_idx else "boundary_mark"
    for cursor in range(entry_idx, end_idx + 1):
        bar = m15[cursor]
        if cursor > entry_idx:
            funding_cost += direction * fair[cursor].open * funding_by_time.get(bar.time, 0.0)
        if direction * (bar.open - current_stop) <= 0:
            exit_raw, reason, exit_idx = bar.open, "stop_gap", cursor
            break
        if bar.low <= current_stop if direction == 1 else bar.high >= current_stop:
            exit_raw, reason, exit_idx = current_stop, "stop", cursor
            break
        if bar.high >= target if direction == 1 else bar.low <= target:
            exit_raw, reason, exit_idx = target, "target", cursor
            break
        if mode != "2R" and direction * (bar.close - entry) >= risk:
            breakeven = (direction * entry + entry * fee + funding_cost) / (direction - fee) / (1 - direction * slip)
            if breakeven > 0 and direction * (bar.close - breakeven) > 0:
                current_stop = max(current_stop, breakeven) if direction == 1 else min(current_stop, breakeven)
    exit_fill = exit_raw * (1 - direction * slip)
    fees = (entry + exit_fill) * fee
    slippage = direction * (entry - raw_entry + exit_raw - exit_fill)
    net_r = (direction * (exit_fill - entry) - fees - funding_cost) / risk
    return dict(entry_idx=entry_idx, exit_idx=exit_idx, entry_time=m15[entry_idx].time,
                exit_time=m15[exit_idx].time + (0 if reason == "stop_gap" else 900),
                direction=direction, entry=entry, risk=risk, initial_stop=stop, target=target,
                exit_fill=exit_fill, reason=reason, fee=fee, slip=slip, funding_per_unit=funding_cost,
                net_r=net_r, gross_r=direction * (exit_raw - raw_entry) / risk,
                fee_r=fees / risk, slip_r=slippage / risk, funding_r=funding_cost / risk,
                cost_r=(fees + slippage + funding_cost) / risk, risk_pct=100 * risk / entry)


def replay_trades(data, signals, fee=FEE, slip=SLIP):
    output, rejected = [], Counter()
    funding = {symbol: funding_map(pack[4]) for symbol, pack in data.items()}
    for signal in signals:
        rows, fair = data[signal["symbol"]][2:4]
        for mode in ("2R", "BE"):
            trade = simulate_m15_trade(rows, fair, funding[signal["symbol"]], signal["entry_idx"],
                                       signal["direction"], signal["stop"], mode,
                                       signal["max_m15_bars"], len(rows), fee, slip)
            if trade is None:
                rejected[signal["seed"]] += 1
                continue
            trade.update(symbol=signal["symbol"], arm=f"{signal['seed']}_{mode}", seed=signal["seed"])
            output.append(trade)
    return output, dict(rejected)


def trade_metrics(trades):
    if not trades:
        return {"n": 0, "total_r": 0.0, "mean_r": None, "pf": None}
    values = [trade["net_r"] for trade in trades]
    losses = -sum(value for value in values if value < 0)
    return dict(n=len(values), total_r=sum(values), mean_r=fmean(values),
                pf=sum(value for value in values if value > 0) / losses if losses else None,
                win_rate=sum(value > 0 for value in values) / len(values),
                avg_risk_pct=fmean(trade["risk_pct"] for trade in trades),
                avg_cost_r=fmean(trade["cost_r"] for trade in trades),
                reasons=dict(Counter(trade["reason"] for trade in trades)))


def week_bootstrap_ci(trades):
    if not trades:
        return None
    weeks = defaultdict(list)
    for trade in trades:
        weeks[trade["entry_time"] // (7 * 86400)].append(trade["net_r"])
    blocks, rng, means = list(weeks.values()), random.Random(42), []
    for _ in range(1000):
        picked = [rng.choice(blocks) for _ in blocks]
        means.append(sum(map(sum, picked)) / sum(map(len, picked)))
    means.sort()
    return [means[25], means[974]]


def simulate_event_portfolio(trades, data, risk_fraction=0.01, initial_equity=10000.0):
    if not 0 < risk_fraction <= 1 or not math.isfinite(initial_equity) or initial_equity <= 0:
        raise ValueError("Invalid sizing")
    first_pack = next(iter(data.values()))
    timeline = [bar.time for bar in first_pack[2]]
    row_maps = {symbol: {bar.time: bar for bar in pack[2]} for symbol, pack in data.items()}
    fair_maps = {symbol: {bar.time: bar for bar in pack[3]} for symbol, pack in data.items()}
    rates = {symbol: funding_map(pack[4]) for symbol, pack in data.items()}
    entries = defaultdict(list)
    for trade in trades:
        entries[trade["entry_time"]].append(trade)
    active, ledger, curve = [], [], []
    cash = peak = initial_equity
    max_dd = max_risk = max_leverage = 0.0
    max_concurrent = max_same_symbol = skipped_insolvent = 0

    def close(position):
        nonlocal cash
        trade, quantity = position["trade"], position["quantity"]
        if not math.isclose(position["funding"], trade["funding_per_unit"], abs_tol=1e-9):
            raise ValueError("Position funding does not reconcile")
        cash += quantity * (trade["direction"] * (trade["exit_fill"] - trade["entry"]) - trade["exit_fill"] * trade["fee"])
        pnl = position["risk_dollars"] * trade["net_r"]
        ledger.append({**trade, "risk_dollars": position["risk_dollars"], "quantity": quantity, "pnl": pnl})

    def equity(stamp, field):
        value = cash
        for position in active:
            trade, quantity = position["trade"], position["quantity"]
            if field == "close" and trade["reason"] != "stop_gap" and trade["exit_time"] <= stamp + 900:
                value += quantity * (trade["direction"] * (trade["exit_fill"] - trade["entry"]) - trade["exit_fill"] * trade["fee"])
            else:
                value += quantity * trade["direction"] * (getattr(row_maps[trade["symbol"]][stamp], field) - trade["entry"])
        return value

    for stamp in timeline:
        remaining = []
        for position in active:
            trade = position["trade"]
            if trade["exit_time"] == stamp and trade["reason"] != "stop_gap":
                close(position)
            else:
                remaining.append(position)
        active = remaining
        remaining = []
        for position in active:
            trade = position["trade"]
            funding = trade["direction"] * fair_maps[trade["symbol"]][stamp].open * rates[trade["symbol"]].get(stamp, 0.0)
            cash -= position["quantity"] * funding
            position["funding"] += funding
            if trade["exit_time"] == stamp:
                close(position)
            else:
                remaining.append(position)
        active = remaining
        entry_equity = equity(stamp, "open")
        batch = sorted(entries.get(stamp, []), key=lambda trade: (trade["symbol"], trade["arm"]))
        for trade in batch:
            if entry_equity <= 0:
                skipped_insolvent += 1
                continue
            risk_dollars = entry_equity * risk_fraction
            quantity = risk_dollars / trade["risk"]
            cash -= quantity * trade["entry"] * trade["fee"]
            active.append(dict(trade=trade, risk_dollars=risk_dollars, quantity=quantity, funding=0.0))
        if active:
            current_equity = equity(stamp, "open")
            max_concurrent = max(max_concurrent, len(active))
            max_same_symbol = max(max_same_symbol, max(Counter(item["trade"]["symbol"] for item in active).values()))
            if current_equity > 0:
                max_risk = max(max_risk, 100 * sum(item["risk_dollars"] for item in active) / current_equity)
                notional = sum(item["quantity"] * row_maps[item["trade"]["symbol"]][stamp].open for item in active)
                max_leverage = max(max_leverage, notional / current_equity)
        close_equity = equity(stamp, "close")
        peak = max(peak, close_equity)
        max_dd = max(max_dd, 100 * (peak - close_equity) / peak)
        curve.append([stamp + 900, close_equity])
    for position in active:
        close(position)
    if not math.isclose(cash, initial_equity + sum(item["pnl"] for item in ledger), abs_tol=1e-6):
        raise ValueError("Portfolio ledger does not reconcile to trade cash flows")
    months = defaultdict(list)
    for stamp, value in curve:
        months[datetime.fromtimestamp(stamp - 1, timezone.utc).strftime("%Y-%m")].append(value)
    previous, monthly = initial_equity, {}
    for month, values in months.items():
        monthly[month] = 100 * (values[-1] / previous - 1) if previous > 0 else None
        previous = values[-1]
    return dict(initial_equity=initial_equity, final_equity=cash,
                total_return_pct=100 * (cash / initial_equity - 1), close_mtm_max_dd_pct=max_dd,
                risk_per_signal_pct=100 * risk_fraction, max_concurrent_positions=max_concurrent,
                max_positions_same_symbol=max_same_symbol, max_initial_risk_budget_pct=max_risk,
                max_gross_notional_equity_ratio=max_leverage, skipped_insolvent=skipped_insolvent,
                monthly_return_pct=monthly, equity_curve=curve, ledger=ledger)


def main():
    data, first, end, digest = load_data()
    folds = build_folds(first, end)
    signals = [signal for symbol, pack in sorted(data.items()) for signal in seed_signals(symbol, pack[2])]
    trades, rejected = replay_trades(data, signals)
    print(f"Native HTF research seeds; period {iso(first)} to {iso(end)}")
    print("No cap, no per-symbol cooldown, no re-entry suppression; 1% initial risk per signal")
    report = {}
    for name, members in PORTFOLIOS.items():
        selected = [trade for trade in trades if trade["arm"] in members]
        result = simulate_event_portfolio(selected, data)
        metrics = trade_metrics(selected)
        report[name] = {
            "metrics": metrics, "week_ci95_mean_r": week_bootstrap_ci(selected),
            "by_entry_period": {label: trade_metrics([trade for trade in selected if start <= trade["entry_time"] < stop])
                                for label, start, stop in folds},
            "by_symbol": {symbol: trade_metrics([trade for trade in selected if trade["symbol"] == symbol]) for symbol in data},
            "portfolio_1pct": result,
        }
        print(f"{name:24s} n={metrics['n']:4d} R={metrics.get('mean_r', 0):+.3f} PF={metrics.get('pf', 0):.2f} "
              f"return={result['total_return_pct']:+.1f}% DD={result['close_mtm_max_dd_pct']:.1f}% "
              f"concurrent={result['max_concurrent_positions']} same_symbol={result['max_positions_same_symbol']} "
              f"open_risk={result['max_initial_risk_budget_pct']:.1f}%")
    primary = PORTFOLIOS["Combined_D1_BE_H4_2R"]
    stressed, stress_rejected = replay_trades(data, signals, fee=FEE * 2, slip=SLIP * 2)
    stress_trades = [trade for trade in stressed if trade["arm"] in primary]
    stress_result = simulate_event_portfolio(stress_trades, data)
    OUTPUT.write_text(json.dumps({
        "data_sha256": digest, "code_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "period_utc": [iso(first), iso(end)], "folds": folds, "seed_specs": SEEDS,
        "signal_counts": dict(Counter(signal["seed"] for signal in signals)), "rejected_geometry": rejected,
        "assumptions": {
            "fee_per_side": FEE, "slippage_per_side": SLIP, "seed_stop": "5 closed native bars swing plus 0.25 ATR14; at least 1 ATR14 from signal close",
            "entry": "First M15 open after native signal candle closes", "target": "2R from slipped entry",
            "breakeven": "After M15 close >= 1R, cost-aware stop effective next candle; no partial",
            "position_limit": None, "allow_same_symbol_pyramiding": True,
            "sizing": "1% equity marked at entry open per signal; all simultaneous signals use common pre-batch equity",
        },
        "limitations": [
            "Exploratory candidates selected on reused history; chronological cohorts are not fresh out-of-sample evidence",
            "No concurrent or same-symbol cap. Virtual independent positions may overlap and oppose; no exchange netting/margin/liquidation model",
            "Drawdown marks trade closes every M15, not intrabar lows or simultaneous multi-symbol worst prices",
            "OHLC cannot identify order when stop and target both touched; stop-first and adverse stop gaps used",
            "Funding at entry timestamp excluded; held positions pay/receive at bar opens through exit bar",
            "Open positions administratively settled at dataset end; entry-period cohorts retain cross-boundary trades",
            "Week bootstrap is descriptive, no multiple-search adjustment; no control claim made from earlier flawed matched-control study",
            "No production tree seeds changed: current live DSL lacks native D1 and uses different holding defaults",
        ],
        "portfolios": report,
        "primary_double_cost_stress": {"metrics": trade_metrics(stress_trades), "portfolio": stress_result, "rejected_geometry": stress_rejected},
    }, indent=2, allow_nan=False) + "\n")


if __name__ == "__main__":
    main()
