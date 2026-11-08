"""Local paired timing audit of archived, resolved rule-tree signals; no DB/VPS writes.

python -m scripts.entry_timing_paired
H1 arm waits until the next hour boundary AFTER the same M15 signal. This tests
execution delay, not an H1-only strategy. Initial SL/TP and adaptive exit rules
are unchanged. Separate signals may overlap; this is not a portfolio backtest.
"""

import bisect
import copy
import hashlib
import json
import math
import random
from collections import Counter, defaultdict
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from statistics import fmean, median

from backend.app import futures_learner as learner
from backend.app.futures_strategies import strategy_setup
from backend.app.futures_symbols import symbol_scope
from scripts.futures_replay_340d import setup_memo

DATA = Path("research-output/futures-nov01/mexc_10pairs_340d_compare.pkl")
COHORT = Path("research-output/futures-gene-ablation.json")
MANIFEST = COHORT.with_suffix(".manifest.json")
OUTPUT = Path("research-output/entry-timing-paired.json")
BASES = {"exit_0": 0, "exit_1": 1, "dryup": 11, "cdir_10": 10, "cdir_23": 23}


def next_hour(stamp):
    return -(-stamp // 3600) * 3600


def wait_valid(rows, first, delayed, direction, stop, target):
    for bar in rows[first:delayed]:
        if direction * (bar.open - stop) <= 0 or (bar.low <= stop if direction == 1 else bar.high >= stop):
            return "stop_before_entry"
        if bar.high >= target if direction == 1 else bar.low <= target:
            return "target_before_entry"
    return None


def measured_finish(sample, direction, price, why, bar, original_finish):
    quantity = sample.get("quantity", 1.0)
    fill = price * (1 - direction * learner.SLIP)
    fees = (sample["entry"] * learner.FEE + sample.get("realized_exit_fee", 0)
            + fill * quantity * learner.FEE)
    slip = direction * (sample["entry"] - sample["raw_entry"])
    for partial in sample.get("partial_exits") or []:
        raw = partial["price"] / (1 - direction * learner.SLIP)
        slip += direction * (raw - partial["price"]) * partial["quantity"]
    slip += direction * (price - fill) * quantity
    original_finish(sample, direction, price, why, bar)
    risk = sample["risk"]
    sample.update(fee_r=fees / risk, slip_r=slip / risk,
                  funding_r=sample["funding_cost"] / risk,
                  raw_gross_r=sample["net_r"] + (fees + slip + sample["funding_cost"]) / risk,
                  initial_risk_pct=100 * risk / sample["entry"])


def simulate(sample, rows, fair, funding, times, entry_index, boundary, history):
    sample = copy.deepcopy(sample)
    sample["entry_time"] = rows[entry_index].time
    sample["raw_entry"] = rows[entry_index].open
    for cursor in range(entry_index, len(rows)):
        bar = rows[cursor]
        if bar.time >= boundary:
            sample.update(status="excluded", exit_reason="boundary")
            break
        high = bisect.bisect_right(times, bar.time)
        learner.advance(sample, bar, fair[cursor], funding[max(0, high - 4):high], history(cursor))
        if sample["status"] != "open":
            break
    if sample["status"] == "open":
        sample.update(status="excluded", exit_reason="dataset_boundary")
    return sample


def summary(rows):
    if not rows:
        return {"n": 0}
    values = [row["net_r"] for row in rows]
    losses = -sum(value for value in values if value < 0)
    return dict(n=len(rows), mean_r=fmean(values),
                pf=sum(value for value in values if value > 0) / losses if losses else None,
                **{field: fmean(row[field] for row in rows)
                   for field in ("raw_gross_r", "fee_r", "slip_r", "funding_r")},
                median_initial_risk_pct=median(row["initial_risk_pct"] for row in rows))


def paired_interval(pairs):
    weeks = defaultdict(list)
    for pair in pairs:
        weeks[pair["time"] // (7 * 86400)].append(pair["h1"]["net_r"] - pair["m15"]["net_r"])
    if not weeks:
        return None
    blocks, rng, means = list(weeks.values()), random.Random(42), []
    for _ in range(1000):
        picked = [rng.choice(blocks) for _ in blocks]
        means.append(sum(map(sum, picked)) / sum(map(len, picked)))
    means.sort()
    return [means[25], means[974]]


def main():
    import pickle

    manifest = json.loads(MANIFEST.read_text())
    if hashlib.sha256(DATA.read_bytes()).hexdigest() != manifest["data_sha256"]:
        raise ValueError("Dataset differs from archived cohort")
    raw = pickle.loads(DATA.read_bytes())
    first = max(value[2][0].time for value in raw.values())
    series = {symbol: [bar for bar in value[2] if bar.time >= first] for symbol, value in raw.items()}
    split = int(datetime.fromisoformat(manifest["split_utc"]).timestamp())
    end = min(rows[-1].time for rows in series.values()) + 900
    cohorts = defaultdict(list)
    for row in json.loads(COHORT.read_text()):
        variant = "adaptive" if row["arm"].startswith("exit_") else "orig"
        if row["arm"] in BASES and row["var"] == variant:
            cohorts[row["sym"]].append(row)
    setup_memo()
    pairs, attempts, baseline = [], [], []
    original_finish = learner.finish

    def finish(sample, direction, price, why, bar):
        measured_finish(sample, direction, price, why, bar, original_finish)

    learner.finish = finish
    try:
        for symbol, signals in cohorts.items():
            rows = series[symbol]
            positions = {bar.time: index for index, bar in enumerate(rows)}
            fair = [bar for bar in raw[symbol][3] if bar.time >= first]
            funding = raw[symbol][4]
            times = [record["time"] for record in funding]
            if [bar.time for bar in fair] != [bar.time for bar in rows]:
                raise ValueError("Trade/mark timestamps differ")
            if any(after.time - before.time != 900 for before, after in zip(rows, rows[1:])):
                raise ValueError("M15 data gap")

            @lru_cache(maxsize=256)
            def history(index):
                return rows[max(0, index - learner.WARMUP + 1):index + 1]

            for record in signals:
                index = positions[record["t"]]
                genome = manifest["candidates"][record["arm"] + ":" + record["var"]]["genome"]
                peers = {name: values[index - learner.WARMUP + 1:index + 1]
                         for name, values in series.items() if name != symbol}
                high = bisect.bisect_right(times, rows[index].time)
                with symbol_scope(symbol):
                    setup = strategy_setup(genome, history(index), funding[:high], benchmarks=peers,
                                           btc_candles=peers.get("BTC_USDT"))
                if setup is None or setup.side != record["side"]:
                    raise ValueError(f"Cannot reproduce signal: {record}")
                sample = dict(status="open", entry=None, side=setup.side, stop=setup.stop,
                              target=setup.target, exit_policy=genome.get("exit_policy", "fixed"),
                              exit_params=genome.get("exit_params"), exit_rule=genome["rule"])
                boundary = split if record["part"] == "train" else end
                m15 = simulate(sample, rows, fair, funding, times, index + 1, boundary, history)
                if m15["status"] != "resolved" or not math.isclose(m15["net_r"], record["r"], abs_tol=1e-9):
                    raise ValueError(f"Baseline replay mismatch: {record}; replay={m15}")
                baseline.append(dict(part=record["part"], seed=BASES[record["arm"]], result=m15))
                entry_time = next_hour(rows[index].time + 900)
                delayed = positions.get(entry_time)
                reason = "boundary" if delayed is None or entry_time >= boundary else wait_valid(
                    rows, index + 1, delayed, 1 if setup.side == "long" else -1, setup.stop, setup.target)
                h1 = None if reason else simulate(sample, rows, fair, funding, times, delayed, boundary, history)
                if h1 is not None and h1["status"] != "resolved":
                    reason = h1.get("reason") or h1.get("exit_reason") or h1["status"]
                attempt = dict(symbol=symbol, seed=BASES[record["arm"]], part=record["part"],
                               time=record["t"], delay_minutes=(entry_time - rows[index].time - 900) // 60,
                               outcome=reason or "resolved")
                attempts.append(attempt)
                if not reason:
                    pairs.append(dict(**attempt, m15=m15, h1=h1,
                                      h1_r_on_m15_risk=h1["net_r"] * h1["risk"] / m15["risk"]))
            print(f"{symbol}: {len(signals)} baseline signals verified", flush=True)
    finally:
        learner.finish = original_finish
    report = dict(meta=dict(data_sha256=manifest["data_sha256"], split=split, end=end,
                            fee_bps=learner.FEE * 10000, slippage_bps=learner.SLIP * 10000,
                            note=__doc__, selection="Archived resolved signals, not full bot population"),
                  baseline={}, paired={}, attempts=attempts, pairs=pairs)
    for part in ("train", "validation"):
        report["baseline"][part] = summary([row["result"] for row in baseline if row["part"] == part])
        part_pairs = [pair for pair in pairs if pair["part"] == part]
        rows = dict(m15=summary([pair["m15"] for pair in part_pairs]),
                    h1=summary([pair["h1"] for pair in part_pairs]),
                    delta_ci95_week_blocks=paired_interval(part_pairs),
                    outcomes=dict(Counter(row["outcome"] for row in attempts if row["part"] == part)),
                    by_seed={str(seed): {arm: summary([pair[arm] for pair in part_pairs if pair["seed"] == seed])
                                        for arm in ("m15", "h1")} for seed in BASES.values()})
        if part_pairs:
            rows["delta_mean_r"] = fmean(pair["h1"]["net_r"] - pair["m15"]["net_r"] for pair in part_pairs)
            rows["delta_same_cash_risk"] = fmean(pair["h1_r_on_m15_risk"] - pair["m15"]["net_r"] for pair in part_pairs)
        report["paired"][part] = rows
    OUTPUT.write_text(json.dumps(report, indent=2, allow_nan=False))
    print(json.dumps({key: report[key] for key in ("baseline", "paired")}, indent=2))


if __name__ == "__main__":
    main()
