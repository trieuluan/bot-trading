"""Offline matched-entry study across EMA-only, H4 2/3 and H4 3/3 gates."""

import argparse
import copy
import hashlib
import json
import math
import time
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from backend.app.futures_exits import h1_view
from backend.app.futures_learner import FEE, SLIP, WARMUP, advance
from backend.app.futures_rule_tree import breakout_level, matches
from backend.app.futures_strategies import RULE_SEEDS, strategy_setup
from backend.app.futures_symbols import SYMBOLS, symbol_scope
from backend.app.mexc import validate_candles
from scripts.futures_h4_variants import load, summarize
from scripts.futures_replay_340d import setup_memo

GATES = ("ema_only", "h4_2of3", "h4_3of3")
ENTRIES = ("market", "retest", "sweep")
WAIT_BARS = 4
RECENT = 1792886400


def gate_flags(slot, history, side, context):
    direction = 1 if side == "long" else -1
    flags = [matches(node, history, direction, 20, context=context)
             for node in slot["children"]]
    return (flags[0], flags[0] and any(flags[1:]), all(flags))


def confirmation_zone(genome, history, setup):
    hours, atr = h1_view(history)
    if atr is None:
        return None
    direction = 1 if setup.side == "long" else -1
    level = breakout_level(genome["rule"]["children"][1], history, direction,
                           genome["lookback"])
    if level is None:
        level = (hours[-1].high + hours[-1].low) / 2
    lower, upper = level - 0.05 * atr, level + 0.05 * atr
    near = upper if direction == 1 else lower
    far = lower if direction == 1 else upper
    if (not all(math.isfinite(value) and value > 0 for value in (lower, upper))
            or direction * (setup.entry - near) <= 0
            or direction * (far - setup.stop) <= 0):
        return None
    return lower, upper


def cancel(sample, reason, available):
    sample.update(status="cancelled", exit_reason=reason, net_r=0.0,
                  label_available_at=available)


def advance_confirmation(sample, bar, fair, funding, history):
    if sample["status"] != "open":
        return
    if sample["arm"] == "market" or sample.get("confirmed"):
        advance(sample, bar, fair, funding, history)
        return
    if bar.time != sample["next_confirmation"]:
        sample.update(status="excluded", exit_reason="confirmation_data_gap")
        return
    if bar.time >= sample["candle_time"] + (WAIT_BARS + 1) * 900:
        cancel(sample, "confirmation_expired", bar.time)
        return
    sample["next_confirmation"] += 900
    direction = 1 if sample["side"] == "long" else -1
    lower, upper = sample["zone"]
    near, far = (upper, lower) if direction == 1 else (lower, upper)
    if (bar.low <= sample["stop"] if direction == 1 else bar.high >= sample["stop"]):
        cancel(sample, "stop_before_confirmation", bar.time + 900)
    elif (bar.high >= sample["target"] if direction == 1 else bar.low <= sample["target"]):
        cancel(sample, "target_before_confirmation", bar.time + 900)
    elif direction * (bar.close - far) < 0:
        cancel(sample, "closed_through_zone", bar.time + 900)
    else:
        touched = bar.low <= upper and bar.high >= lower
        swept = bar.low < lower if direction == 1 else bar.high > upper
        confirmed = (touched and direction * (bar.close - bar.open) > 0
                     if sample["arm"] == "retest" else swept)
        if confirmed and direction * (bar.close - near) > 0:
            sample.update(confirmed=True, confirmed_at=bar.time + 900,
                          entry_time=bar.time + 900)


def new_sample(symbol, gate, arm, template, genome, history, setup, risk):
    stamp = history[-1].time
    sample = dict(symbol=symbol, gate=gate, arm=arm, template=template,
                  candle_time=stamp, entry_time=stamp + 900, side=setup.side,
                  stop=setup.stop, target=setup.target, entry=None, status="open",
                  exit_policy=genome["exit_policy"], exit_rule=genome["rule"],
                  net_r=None, reference_risk=risk, next_confirmation=stamp + 900)
    if arm != "market":
        sample["zone"] = confirmation_zone(genome, history, setup)
        if sample["zone"] is None:
            cancel(sample, "no_valid_zone", stamp + 900)
    return sample


def run_pair(task):
    symbol, data, output = task
    started = time.monotonic()
    setup_memo()
    raw, first, last, clip = load(data)
    series = {name: clip(raw[name][2]) for name in SYMBOLS}
    candles, fair, funding = series[symbol], clip(raw[symbol][3]), raw[symbol][4]
    expected = list(range(first, last + 900, 900))
    for rows in [*series.values(), fair]:
        validate_candles(rows)
        if [bar.time for bar in rows] != expected:
            raise ValueError(f"Incomplete aligned coverage: {symbol}")
    split = first + WARMUP * 900 + int((len(candles) - WARMUP) * 0.7) * 900
    genomes = copy.deepcopy(RULE_SEEDS)
    for genome in genomes:
        genome["rule"]["children"][0] = {"feature": "trend", "frame": "H4"}
    samples, active, baseline = [], [], {}
    slot = RULE_SEEDS[0]["rule"]["children"][0]
    with symbol_scope(symbol):
        for index in range(WARMUP, len(candles)):
            bar, mark = candles[index], fair[index]
            if bar.time == split:
                for sample in active:
                    sample.update(status="excluded", exit_reason="split_boundary")
                active.clear()
                baseline.clear()
            history = candles[index - WARMUP + 1:index + 1]
            known = [record for record in funding if record["time"] <= bar.time]
            for sample in active:
                advance_confirmation(sample, bar, mark, known, history)
            active = [sample for sample in active if sample["status"] == "open"]
            peers = {name: rows[index - WARMUP + 1:index + 1]
                     for name, rows in series.items() if name != symbol}
            context = {"benchmarks": peers, "htf": True}
            flags = {side: gate_flags(slot, history, side, context)
                     for side in ("long", "short")}
            for template, genome in enumerate(genomes):
                free = [gate for gate in GATES if baseline.get((gate, template), {}).get("status") != "open"]
                if not free:
                    continue
                setup = strategy_setup(genome, history, known, benchmarks=peers,
                                       btc_candles=peers.get("BTC_USDT"), fair_candles=[mark])
                if setup is None or index + 1 == len(candles):
                    continue
                direction = 1 if setup.side == "long" else -1
                risk = direction * (candles[index + 1].open * (1 + direction * SLIP) - setup.stop)
                if risk <= 0:
                    continue
                for gate, allowed in zip(GATES, flags[setup.side]):
                    if gate not in free or not allowed:
                        continue
                    for arm in ENTRIES:
                        sample = new_sample(symbol, gate, arm, template, genome, history, setup, risk)
                        sample["part"] = "train" if bar.time < split else "validation"
                        samples.append(sample)
                        if sample["status"] == "open":
                            active.append(sample)
                        if arm == "market":
                            baseline[gate, template] = sample
            if index % 9600 == 0:
                print(symbol, index, "/", len(candles), flush=True)
    for sample in active:
        sample.update(status="excluded", exit_reason="dataset_boundary")
    payload = dict(symbol=symbol, first=first, last=last, split=split, samples=samples,
                   seconds=time.monotonic() - started)
    Path(output, symbol + ".json").write_text(json.dumps(payload, allow_nan=False))
    print(symbol, "DONE", round(payload["seconds"], 1), "seconds", len(samples), "samples", flush=True)
    return payload


def value(sample):
    if sample["status"] == "cancelled":
        return 0.0
    return sample["net_r"] * sample["risk"] / sample["reference_risk"]


def metrics(samples):
    filled = [sample for sample in samples if sample["status"] == "resolved"]
    rows = [(sample["symbol"], sample["arm"], sample["part"], sample["template"],
             sample["candle_time"], sample["side"], value(sample), sample["exit_reason"])
            for sample in filled]
    result = summarize(rows)
    result.update(signals=len(samples), filled=len(filled),
                  fill_rate=len(filled) / len(samples) if samples else None,
                  reference_r_per_signal=sum(value(sample) for sample in samples) / len(samples) if samples else None,
                  reasons=dict(Counter(sample["exit_reason"] for sample in samples)))
    return result


def report(payloads):
    groups = defaultdict(dict)
    for payload in payloads:
        for sample in payload["samples"]:
            key = (sample["symbol"], sample["gate"], sample["template"], sample["candle_time"], sample["side"])
            groups[key][sample["arm"]] = sample
    complete = [group for group in groups.values() if len(group) == len(ENTRIES)
                and all(sample["status"] in ("resolved", "cancelled") for sample in group.values())]
    result = {"matched_cohorts": len(complete), "censored_cohorts": len(groups) - len(complete), "arms": {}}
    for gate in GATES:
        for arm in ENTRIES:
            selected = [group[arm] for group in complete if group[arm]["gate"] == gate]
            result["arms"][gate + "/" + arm] = {
                part: metrics([sample for sample in selected if
                               (sample["candle_time"] >= RECENT if part == "recent_oct25" else sample["part"] == part)])
                for part in ("train", "validation", "recent_oct25")}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=5, choices=range(1, 11))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    identity = dict(data_sha256=hashlib.sha256((args.data / "mexc_10pairs_340d_compare.pkl").read_bytes()).hexdigest(),
                    code_sha256={str(path): hashlib.sha256(path.read_bytes()).hexdigest()
                                 for path in [Path(__file__), *Path("backend/app").glob("*.py")]},
                    gates=GATES, entries=ENTRIES, wait_bars=WAIT_BARS, fee=FEE, slip=SLIP,
                    definition="H1 breakout level if present, otherwise latest closed H1 midpoint; +/-0.05 H1 ATR; next-open market fills",
                    cohort="Each gate schedules nonoverlapping market trades per G0; retest/sweep follow exactly those signals independently; not a portfolio")
    manifest = args.output / "manifest.json"
    identity = json.loads(json.dumps(identity))
    if manifest.exists() and json.loads(manifest.read_text()) != identity:
        raise ValueError("Inputs changed; use a fresh output directory")
    manifest.write_text(json.dumps(identity, indent=2))
    pending = [(symbol, str(args.data), str(args.output)) for symbol in SYMBOLS
               if not (args.output / (symbol + ".json")).exists()]
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        for future in as_completed([pool.submit(run_pair, task) for task in pending]):
            future.result()
    payloads = [json.loads((args.output / (symbol + ".json")).read_text()) for symbol in SYMBOLS]
    (args.output / "summary.json").write_text(json.dumps(report(payloads), indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
