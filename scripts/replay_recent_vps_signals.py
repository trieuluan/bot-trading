"""Local paired replay of emitted VPS signals and immutable paper-entry snapshots.

Run: /private/tmp/btvenv3/bin/python -m scripts.replay_recent_vps_signals
Uses downloaded VPS candles with public MEXC mark/funding and historical warmup.
Never substitutes a learner sample's possibly moved stop for original geometry.
"""

import bisect
import hashlib
import json
import math
import pickle
from collections import Counter, defaultdict
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

from backend.app import futures_learner as learner
from backend.app.futures_strategies import strategy_setup
from backend.app.futures_symbols import SYMBOLS, symbol_scope
from backend.app.market import Candle
from backend.app.mexc import validate_candles
from scripts.futures_replay_340d import setup_memo
from scripts.structure_entries_replay import POLICIES, simulate_policy, summarize_arm

DIRECTORY = Path("research-output/futures-nov04")
DATA = DIRECTORY / "mexc_10pairs_340d_compare.pkl"
VPS_LEARN = DIRECTORY / "vps_learning_snapshot.json"
VPS_MARKET = DIRECTORY / "vps_market_snapshot.json"
OUTPUT = DIRECTORY / "recent_epoch_replay.json"
PRICE_FIELDS = ("open", "high", "low", "close", "volume")


def merge_candles(public, snapshot):
    first = max(value[2][0].time for value in public.values())
    end = min(snapshot["closed_before"], min(value[2][-1].time + 900 for value in public.values()))
    vps = defaultdict(dict)
    for record in snapshot["candles"]:
        symbol = record["symbol"].split(":")[0].replace("/", "_")
        if symbol not in SYMBOLS or record["source"] != "mexc_futures" or record["timeframe"] != "15m":
            raise ValueError("Unexpected source in VPS candle snapshot")
        if record["time"] + 900 > snapshot["closed_before"]:
            raise ValueError("VPS snapshot contains an unfinished candle")
        vps[symbol][record["time"]] = Candle(record["time"], *(record[field] for field in PRICE_FIELDS))
    merged = {}
    overlap = Counter()
    for symbol in SYMBOLS:
        if not vps[symbol] or max(vps[symbol]) + 900 < end:
            raise ValueError(f"VPS candles do not cover replay end: {symbol}")
        rows = {bar.time: bar for bar in public[symbol][2] if first <= bar.time < end}
        for stamp, bar in vps[symbol].items():
            if stamp not in rows:
                continue
            if any(not math.isclose(getattr(rows[stamp], field), getattr(bar, field),
                                    rel_tol=1e-12, abs_tol=1e-12) for field in PRICE_FIELDS):
                raise ValueError(f"VPS/public candle mismatch: {symbol}/{stamp}")
            rows[stamp] = bar
            overlap[symbol] += 1
        candles = [rows[stamp] for stamp in sorted(rows)]
        fair = [bar for bar in public[symbol][3] if first <= bar.time < end]
        expected = list(range(first, end, 900))
        for series in (candles, fair):
            validate_candles(series)
            if [bar.time for bar in series] != expected:
                raise ValueError(f"Missing trade/mark candle: {symbol}")
        merged[symbol] = (first, end, candles, fair,
                          [record for record in public[symbol][4] if record["time"] < end])
    return merged, dict(overlap)


def snapshot_geometry(decision):
    audit = decision.get("audit") or {}
    setup = decision.get("setup") or {}
    genome = audit.get("genome")
    side = setup.get("side")
    stop, target = audit.get("initial_stop"), audit.get("initial_target")
    if not genome or side not in ("long", "short") or any(
            not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0
            for value in (stop, target)):
        raise ValueError("Paper signal lacks immutable initial geometry/genome")
    return genome, side, stop, target


def summarize_cohort(results):
    baseline = results["market"]
    summary = {}
    for policy in POLICIES:
        summary[policy] = summarize_arm(results[policy], baseline)
        if policy.endswith("_confirm"):
            control = policy.removesuffix("_confirm")
            summary[policy]["passive_control"] = {
                "policy": control, **summarize_arm(results[policy], results[control])}
    return summary


def main():
    public = pickle.loads(DATA.read_bytes())
    market = json.loads(VPS_MARKET.read_text())
    learning = json.loads(VPS_LEARN.read_text())
    merged, overlap = merge_candles(public, market)
    end = merged[SYMBOLS[0]][1]
    (DIRECTORY / "vps_merged_history.pkl").write_bytes(pickle.dumps(merged))
    series = {symbol: value[2] for symbol, value in merged.items()}
    decisions = {(row["symbol"], row["candle_time"]): row for row in market["decisions"]}
    results = {cohort: {policy: [] for policy in POLICIES} for cohort in ("learner", "paper_opened")}
    exclusions, source_checks = Counter(), Counter()
    checks = []
    setup_memo()

    for symbol in SYMBOLS:
        state = learning[symbol]
        epoch = state["entry_mix_started_at"]
        rows, fair, funding = merged[symbol][2:]
        positions = {bar.time: index for index, bar in enumerate(rows)}
        times = [record["time"] for record in funding]

        @lru_cache(maxsize=512)
        def history(index):
            return rows[max(0, index - learner.WARMUP + 1):index + 1]

        records = [("learner", sample) for sample in state["recent_samples"]
                   if sample["candle_time"] >= epoch]
        records.extend(("paper_opened", decision) for decision in market["decisions"]
                       if decision["symbol"] == symbol and decision["action"] == "opened"
                       and decision["candle_time"] >= epoch)
        seen = set()
        for cohort, record in records:
            stamp = record["candle_time"]
            strategy = record.get("strategy") or (record.get("audit") or {}).get("strategy")
            key = (symbol, strategy, stamp)
            if (cohort, key) in seen:
                raise ValueError(f"Duplicate source signal: {cohort}/{key}")
            seen.add((cohort, key))
            index = positions.get(stamp)
            if index is None or index + 1 >= len(rows):
                exclusions[f"{cohort}:no_next_closed_bar"] += 1
                continue
            decision = decisions.get((symbol, stamp))
            audit = (decision or {}).get("audit") or {}
            if audit.get("strategy") == strategy and audit.get("genome"):
                genome, side, stop, target = snapshot_geometry(decision)
                source_checks[f"{cohort}:immutable_audit"] += 1
            else:
                genome = state["candidates"].get(strategy)
                if genome is None:
                    exclusions[f"{cohort}:missing_genome"] += 1
                    continue
                if genome["rule"] != record.get("exit_rule"):
                    exclusions[f"{cohort}:genome_rule_mismatch"] += 1
                    continue
                peers = {name: values[index - learner.WARMUP + 1:index + 1]
                         for name, values in series.items() if name != symbol}
                known = bisect.bisect_right(times, stamp)
                with symbol_scope(symbol):
                    setup = strategy_setup(genome, history(index), funding[:known],
                                           benchmarks=peers, btc_candles=peers.get("BTC_USDT"),
                                           fair_candles=[fair[index]])
                if setup is None or setup.side != record["side"]:
                    exclusions[f"{cohort}:cannot_reproduce_setup"] += 1
                    continue
                side, stop, target = setup.side, setup.stop, setup.target
                source_checks[f"{cohort}:reproduced_setup"] += 1
            direction = 1 if side == "long" else -1
            risk = direction * (rows[index + 1].open * (1 + direction * learner.SLIP) - stop)
            if risk <= 0:
                exclusions[f"{cohort}:invalid_baseline_risk"] += 1
                continue
            base = dict(status="open", entry=None, side=side, stop=stop, target=target,
                        reference_risk=risk, exit_policy=genome.get("exit_policy", "fixed"),
                        exit_params=genome.get("exit_params"), exit_rule=genome["rule"])
            outcomes = {}
            for policy in POLICIES:
                outcome = simulate_policy(base, policy, rows, fair, funding, times, index, end, history)
                outcome.update(key=key, time=stamp, source_trade_id=record.get("trade_id"), side=side)
                results[cohort][policy].append(outcome)
                outcomes[policy] = outcome
            if cohort == "learner" and record["status"] == "resolved":
                original_policy = record.get("entry_policy", "market")
                outcome = outcomes[original_policy]
                matched = math.isclose(outcome["net_r"], record["net_r"], abs_tol=1e-8)
                source_checks["learner:resolved_match" if matched else "learner:resolved_mismatch"] += 1
                if not matched:
                    checks.append(dict(key=key, policy=original_policy,
                                       expected_r=record["net_r"], replay_r=outcome["net_r"],
                                       expected_reason=record.get("exit_reason"), replay_reason=outcome["exit_reason"]))
        print(f"{symbol}: source signals checked", flush=True)

    summaries = {cohort: summarize_cohort(policies) for cohort, policies in results.items()}
    for cohort, policies in summaries.items():
        print(f"\n{cohort}: {policies['market']['n_signals']} paired signals")
        for policy, metrics in policies.items():
            print(policy, metrics["n_filled"], round(metrics["total_r"], 4),
                  metrics["mean_r_when_filled"], metrics["pf_when_filled"], metrics["reasons"])
    fields = ("key", "time", "side", "net_r", "ref_r", "filled", "exit_reason", "entry",
              "risk", "reference_risk", "source_trade_id")
    OUTPUT.write_text(json.dumps(dict(
        provenance={str(path): hashlib.sha256(path.read_bytes()).hexdigest()
                    for path in (DATA, VPS_LEARN, VPS_MARKET, Path(__file__),
                                 Path("scripts/structure_entries_replay.py"))},
        last_candle_utc=datetime.fromtimestamp(end - 900, timezone.utc).isoformat(),
        closed_through_utc=datetime.fromtimestamp(end, timezone.utc).isoformat(),
        vps_candles_verified_and_used=overlap,
        epoch_started_at={symbol: state["entry_mix_started_at"] for symbol, state in learning.items()},
        exclusions=dict(exclusions), source_checks=dict(source_checks), source_mismatches=checks,
        limitations=[
            "Only actual emitted signals or actually opened paper signals; not a re-selection/portfolio simulation",
            "All four structure families and seven existing controls unchanged from prior replay; four M15 bars to confirm",
            "No moved-stop fallback; immutable entry audits or successfully reproduced original setup only",
            "Public MEXC warmup/mark/funding; downloaded VPS trade candles verified identical and explicitly merged",
            "Learner 48h holding cap retained; paper production has no universal 48h cap",
            "Boundary_mark is hypothetical mark-to-market with closing costs, not realized strategy profit; no future candles used",
            "New window is only a few days; week bootstrap intervals are not reliable evidence on this window",
            "Unfilled signals score zero; 0 fills never prove profitability; overlapping signals are not portfolio ROI",
        ],
        cohorts=summaries,
        samples={cohort: {policy: [{field: outcome.get(field) for field in fields}
                                  for outcome in outcomes]
                          for policy, outcomes in policies.items()}
                 for cohort, policies in results.items()},
    ), indent=2, allow_nan=False) + "\n")
    print("source_checks", dict(source_checks), "exclusions", dict(exclusions))


if __name__ == "__main__":
    main()
