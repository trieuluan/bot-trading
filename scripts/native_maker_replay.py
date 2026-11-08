"""Offline native-seed maker proxy; run: python -m scripts.native_maker_replay.

M15 OHLC cannot establish real post-only acceptance, queue priority or fills.
No API calls, database writes or production strategy changes.
"""

import hashlib
import json
import math
from collections import Counter, defaultdict
from pathlib import Path

from scripts import fee_edge_replay as edge
from scripts import htf_portfolio_replay as native

OUTPUT = Path("research-output/native-maker-replay-20261108.json")
CASES = {
    "d1_2r_fixed": ("d1_squeeze", 2, "fixed", "all"),
    "d1_2r_be": ("d1_squeeze", 2, "be", "all"),
    "h4_2r_all": ("h4_sweep", 2, "fixed", "all"),
    "h4_3r_session": ("h4_sweep", 3, "fixed", "utc_12_20"),
    "h4_3r_trail_session": ("h4_sweep", 3, "trail", "utc_12_20"),
}
ARMS = {"passive2_wait4": (2, 0, 4), "passive5_wait4": (5, 0, 4),
        "passive2_wait16": (2, 0, 16), "pullback005_wait4": (2, 0.05, 4)}


def passive_price(reference, opening, stop, direction, buffer_bps, offset_r):
    desired = reference - direction * offset_r * direction * (reference - stop)
    ceiling = opening * (1 - direction * buffer_bps / 10000)
    return min(desired, ceiling) if direction == 1 else max(desired, ceiling)


def limit_trade(rows, fair, rates, signal, target_r, mode, boundary, maker, taker,
                buffer_bps=2, offset_r=0, wait=4, slip=0.0002, price=None):
    start, direction, stop = signal["entry_idx"], signal["direction"], signal["stop"]
    hold = signal["max_m15_bars"]
    if direction not in (-1, 1) or mode not in ("fixed", "be", "trail"):
        raise ValueError("Invalid direction or exit policy")
    if not all(math.isfinite(value) and value >= 0 for value in
               (maker, taker, buffer_bps, offset_r, slip)) or max(maker, taker, slip) >= 1:
        raise ValueError("Invalid fee or slippage")
    if hold < 1 or wait < 1 or target_r <= 0 or not math.isfinite(stop) or stop <= 0:
        raise ValueError("Invalid horizon, target or stop")
    if start < 1 or start + hold > min(boundary, len(rows), len(fair)):
        return None
    opening, reference = rows[start].open, rows[start - 1].close
    if direction * (opening - stop) <= 0 or direction * (reference - stop) <= 0:
        return None
    if price is None:
        price = passive_price(reference, opening, stop, direction, buffer_bps, offset_r)
    if not math.isfinite(price) or price <= 0:
        raise ValueError("Invalid limit price")
    missed = dict(net_r=0.0, filled=False, entry_time=rows[start].time)
    if direction * (opening - price) <= 0:
        return dict(missed, status="post_only_proxy_rejected", exit_idx=start)
    risk = direction * (price - stop)
    target = price + direction * target_r * risk
    if risk <= 0 or target <= 0:
        return None
    end = start + hold - 1
    expiry = min(start + wait, end + 1)
    fill = None
    for cursor in range(start, expiry):
        bar = rows[cursor]
        if bar.low < price if direction == 1 else bar.high > price:
            fill = cursor
            break
    if fill is None:
        return dict(missed, status="expired", exit_idx=expiry - 1)
    funding, current_stop = 0.0, stop
    reason, raw_exit, exit_index = "time", rows[end].close, end
    for cursor in range(fill, end + 1):
        bar = rows[cursor]
        if cursor > fill:
            funding += direction * fair[cursor].open * rates.get(bar.time, 0.0)
        elif cursor > start and direction * (bar.open - price) < 0:
            funding += max(0, direction * fair[cursor].open * rates.get(bar.time, 0.0))
        if direction * (bar.open - current_stop) <= 0:
            raw_exit, reason, exit_index = bar.open, "stop_gap", cursor
            break
        if bar.low <= current_stop if direction == 1 else bar.high >= current_stop:
            raw_exit, reason, exit_index = current_stop, "stop", cursor
            break
        if cursor == fill:
            continue
        if bar.high >= target if direction == 1 else bar.low <= target:
            raw_exit, reason, exit_index = target, "target", cursor
            break
        if cursor == end or direction * (bar.close - price) < risk:
            continue
        proposed = current_stop
        if mode == "be":
            proposed = (direction * price + price * maker + funding) / (direction - taker)
            proposed /= 1 - direction * slip
        elif mode == "trail":
            proposed = bar.close - direction * risk
        if proposed > 0 and direction * (bar.close - proposed) > 0:
            current_stop = max(current_stop, proposed) if direction == 1 else min(current_stop, proposed)
    exit_fill = raw_exit * (1 - direction * slip)
    fees = price * maker + exit_fill * taker
    costs = fees + funding
    return dict(symbol=signal["symbol"], filled=True, status="filled", entry_idx=fill,
                exit_idx=exit_index, entry_time=rows[fill].time,
                net_r=(direction * (exit_fill - price) - costs) / risk,
                risk_pct=100 * risk / price, cost_r=costs / risk,
                fee_r=fees / risk, funding_r=funding / risk, reason=reason)


def summarize(outcomes):
    trades = [row for row in outcomes if row["filled"]]
    return dict(signals=len(outcomes), fills=len(trades),
                fill_rate=len(trades) / len(outcomes) if outcomes else None,
                mean_r_per_signal=sum(row["net_r"] for row in outcomes) / len(outcomes) if outcomes else None,
                statuses=dict(Counter(row["status"] for row in outcomes)),
                filled_metrics=edge.metrics(trades),
                week_ci95_per_signal=native.week_bootstrap_ci(outcomes))


def check():
    from backend.app.market import Candle

    signal = dict(symbol="TEST", entry_idx=1, direction=1, stop=98, max_m15_bars=2)
    rows = [Candle(900 * cursor, *values, 0) for cursor, values in enumerate(
        [(100, 101, 99, 100), (100, 105, 99, 103), (103, 105, 102, 104)])]
    def trade(bars=rows, **kwargs):
        return limit_trade(bars, bars, kwargs.pop("rates", {}), kwargs.pop("signal", signal),
                           2, "fixed", len(bars), kwargs.pop("maker", 0),
                           kwargs.pop("taker", 0), slip=0, **kwargs)
    assert trade(price=100)["status"] == "post_only_proxy_rejected"
    assert trade(price=99)["status"] == "expired"
    assert trade(price=99.5)["net_r"] == 2
    assert math.isclose(trade(price=99.5, maker=0.0006, taker=0.0008)["fee_r"],
                        (99.5 * 0.0006 + 102.5 * 0.0008) / 1.5)
    assert trade(price=99.5, rates={900: 0.5, 1800: 0.001})["funding_r"] == 0.103 / 1.5
    short = dict(signal, direction=-1, stop=102)
    reflected = [Candle(bar.time, 200-bar.open, 200-bar.low, 200-bar.high,
                        200-bar.close, 0) for bar in rows]
    assert trade(reflected, signal=short, price=100.5)["net_r"] == 2
    gap = [*rows[:2], Candle(1800, 97, 105, 96, 100, 0)]
    assert trade(gap, price=99.5)["net_r"] < -1
    tie = [rows[0], Candle(900, 100, 106, 97, 100, 0), rows[2]]
    assert trade(tie, price=99.5)["net_r"] == -1
    fillbar = [rows[0], rows[1], Candle(1800, 100, 101, 99, 100, 0)]
    assert trade(fillbar, price=99.5)["reason"] == "time"
    delayed = [rows[0], Candle(900, 100, 101, 100, 100, 0), rows[1]]
    assert trade(delayed, price=99.5, wait=1)["status"] == "expired"
    assert trade(delayed, price=99.5, wait=2)["entry_idx"] == 2
    assert trade(rows[:2], price=99.5) is None
    assert passive_price(100, 99, 98, 1, 2, 0) < 99
    print("Self-checks passed", flush=True)


def main():
    check()
    data, first, end, digest = native.load_data()
    snapshot = json.loads(edge.FEES.read_text())
    fees, folds = snapshot["fees"], native.build_folds(first, end)
    if set(fees) != set(data) or any(not math.isfinite(fee[kind]) or not 0 <= fee[kind] <= 100
                                   for fee in fees.values() for kind in ("maker_bps", "taker_bps")):
        raise ValueError("Invalid or incomplete fee snapshot")
    signals = defaultdict(list)
    for symbol, pack in sorted(data.items()):
        for signal in native.seed_signals(symbol, pack[2]):
            signals[signal["seed"]].append(signal)
    results = {}
    for name, (seed, target, mode, gate) in CASES.items():
        market = edge.replay(data, signals[seed], fees, folds, target, mode, 1, gate)
        cohort = {(row["symbol"], row["entry_idx"]): row for row in market}
        case = dict(market=edge.metrics(market), market_by_fold={label: edge.metrics(
            [row for row in market if row["fold"] == label]) for label, _, _ in folds}, arms={})
        for arm, (buffer, offset, wait) in ARMS.items():
            paired, schedules = [], {}
            for stress in (1, 2):
                outcomes, busy = [], defaultdict(int)
                for signal in sorted(signals[seed], key=lambda row: (row["entry_time"], row["symbol"])):
                    symbol, stamp = signal["symbol"], signal["entry_time"]
                    if gate == "utc_12_20" and not 12 <= stamp % 86400 / 3600 < 20:
                        continue
                    label, _, fold_end = next(fold for fold in folds if fold[1] <= stamp < fold[2])
                    pack = data[symbol]
                    outcome = limit_trade(pack[2], pack[3], native.funding_map(pack[4]), signal,
                                          target, mode, (fold_end-first)//900,
                                          fees[symbol]["maker_bps"]/10000 * stress,
                                          fees[symbol]["taker_bps"]/10000 * stress,
                                          buffer, offset, wait, slip=0.0002 * stress)
                    if outcome is None:
                        continue
                    outcome.update(symbol=symbol, fold=label, signal_time=stamp)
                    key = (symbol, signal["entry_idx"])
                    if stress == 1 and key in cohort:
                        paired.append(dict(outcome, market_r=cohort[key]["net_r"],
                                           delta_r=outcome["net_r"]-cohort[key]["net_r"]))
                    if signal["entry_idx"] >= busy[symbol]:
                        outcomes.append(outcome)
                        busy[symbol] = outcome["exit_idx"] + 1
                schedules[str(stress)] = dict(total=summarize(outcomes), by_fold={
                    label: summarize([row for row in outcomes if row["fold"] == label])
                    for label, _, _ in folds})
            if len(paired) != len(market):
                raise AssertionError("Paired cohort lost signals")
            case["arms"][arm] = dict(settings=dict(buffer_bps=buffer, offset_r=offset, wait_m15=wait),
                                    paired=summarize(paired),
                                    paired_mean_delta_r=sum(row["delta_r"] for row in paired)/len(paired),
                                    independent_schedule=schedules, paired_ledger=paired)
        results[name] = case
        print(name, "market", case["market"], flush=True)
        for arm, result in case["arms"].items():
            print(arm, result["paired"]["fills"], "paired R/signal",
                  result["paired"]["mean_r_per_signal"], "delta", result["paired_mean_delta_r"], flush=True)
    report = dict(data_sha256=digest, code_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                  period_utc=[native.iso(first), native.iso(end)], fee_snapshot=snapshot,
                  folds=folds, cases=results, limitations=[
                      "Exploratory reused history; not fresh out-of-sample or multiple-testing corrected",
                      "Passive price is capped below/above next open by buffer; not observed bid/ask",
                      "Strict trade-through is only a fill proxy: no order book, queue, latency or partial fills",
                      "Maker fee only on proxy passive fills; every exit uses taker fee and adverse slippage",
                      "No TP or stop updates on fill bar; stop-first, adverse gaps, no market fallback",
                      "Expiry after wait M15 bars; holding deadline anchored to signal, fold-crossing purged",
                      "Funding after fill; gap-fill timestamp settlement pays positive cost, never receives credit",
                      "Paired diagnostics may overlap; independent schedule enforces one pending/position per symbol",
                      "Current fee snapshot applied retrospectively; no historical fee schedule",
                      "No live orders, production configuration, learner or deployment changed"])
    OUTPUT.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print("Report:", OUTPUT)


if __name__ == "__main__":
    main()
