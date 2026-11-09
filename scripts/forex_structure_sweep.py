"""Finite exploratory catalogue; reuses offline execution, never touches live trees."""

import argparse
import gzip
import hashlib
import json
import math
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from statistics import fmean, pstdev

from backend.app import forex_htf_v2 as engine
from backend.app.forex_detectors import H1_DETECTORS
from backend.app.forex_features import NEW_H1_DETECTORS
from backend.app.forex_indicators import rsi
from scripts import forex_mtf_investigation as replay

EXTRA = ("nr7", "bollinger_reclaim", "rsi_reclaim", "momentum6", "ema_cross",
         "asia_break", "london_orb_h1", "new_york_orb_h1")
FAMILIES = tuple(sorted(name for name in {**H1_DETECTORS, **NEW_H1_DETECTORS} if name not in EXTRA)) + EXTRA
REGIMES = ("unfiltered", "h4_aligned", "h4_counter", "h4_range")
FOLDS = ("discovery", "validation", "recent")


def regime_allows(regime, direction, close, fast, slow, width):
    trend = direction * (close - slow) > 0 and direction * (fast - slow) > 0
    counter = direction * (close - slow) < 0 and direction * (fast - slow) < 0
    return {"unfiltered": True, "h4_aligned": trend, "h4_counter": counter,
            "h4_range": abs(fast - slow) <= 0.3 * width}[regime]


def extra_match(family, history, direction, average, previous_average, width):
    previous = history[-2]
    closes = [bar.close for bar in history]
    current = history[-1]
    if family == "nr7":
        return current.high - current.low <= min(bar.high - bar.low for bar in history[-7:])
    if family == "bollinger_reclaim":
        mean, deviation = fmean(closes[-21:-1]), pstdev(closes[-21:-1])
        level = mean - direction * 2 * deviation
        return (current.low < level < current.close if direction == 1 else
                current.close < level < current.high)
    if family == "rsi_reclaim":
        before, after = rsi(closes[:-1]), rsi(closes)
        return before < 30 <= after if direction == 1 else before > 70 >= after
    if family == "momentum6":
        return direction * (current.close - closes[-7]) >= width and direction * (current.close - current.open) > 0
    if family == "ema_cross":
        return direction * (current.close - average) > 0 and direction * (previous.close - previous_average) <= 0
    day = current.time // 86400 * 86400
    if family == "asia_break":
        reference = [bar for bar in history if day <= bar.time < day + 6 * 3600]
        if [bar.time for bar in reference] != list(range(day, day + 6 * 3600, 3600)) or not 7 <= (current.time - day) // 3600 < 11:
            return False
    else:
        zone = replay.LONDON if family == "london_orb_h1" else replay.NEW_YORK
        local = datetime.fromtimestamp(current.time, zone)
        if not 9 <= local.hour < 12:
            return False
        reference = [bar for bar in history[:-1] if datetime.fromtimestamp(bar.time, zone).date() == local.date()
                     and datetime.fromtimestamp(bar.time, zone).hour == 8]
        if len(reference) != 1:
            return False
    level = max(bar.high for bar in reference) if direction == 1 else min(bar.low for bar in reference)
    return direction * (current.close - level) > 0 and direction * (previous.close - level) <= 0


def signals(symbol, frames):
    hours, higher = frames[("1h", "bid")], frames[("4h", "bid")]
    higher_times = [bar.time for bar in higher]
    fast, slow = replay.ema([bar.close for bar in higher], 20), replay.ema([bar.close for bar in higher], 50)
    higher_atr, ranges = replay.atr(higher), replay.atr(hours)
    average = replay.ema([bar.close for bar in hours], 20)
    detectors = {name: d for name, d in {**H1_DETECTORS, **NEW_H1_DETECTORS}.items() if name not in EXTRA}
    seen = set()
    for index in range(120, len(hours)):
        current = hours[index]
        available = current.time + 3600
        higher_index = replay.closed_index(higher_times, available, 14400)
        history = hours[index - 119:index + 1]
        if higher_index < 49 or available - higher_times[higher_index] - 14400 >= 14400:
            continue
        if not replay.clean_window(history, 3600, symbol) or not replay.clean_window(higher[higher_index - 49:higher_index + 1], 14400, symbol):
            continue
        for direction, side in ((1, "long"), (-1, "short")):
            for family in FAMILIES:
                if family in ("inside_bar", "nr7") and direction * (current.close - current.open) <= 0:
                    continue
                setup = None
                if family in detectors:
                    params = dict(engine.default_h1_params(family))
                    if family == "round_level":
                        params["step"] = engine.round_step(symbol)
                    if family == "pdh_pdl_sweep":
                        params["symbol"] = symbol
                    setup = detectors[family](history if family == "pdh_pdl_sweep" else history[-60:], side, **params)
                    if not setup:
                        continue
                elif not extra_match(family, history, direction, average[index], average[index - 1], ranges[index]):
                    continue
                origin = setup.get("origin_time", setup["setup_time"]) if setup else current.time
                identity = (family, direction, origin)
                if identity in seen:
                    continue
                seen.add(identity)
                recent = history[-4:]
                swing = (setup["invalidation"] if setup else
                         min(bar.low for bar in recent) if direction == 1 else max(bar.high for bar in recent))
                stop = current.close - direction * max(direction * (current.close - swing) + 0.2 * ranges[index], 0.8 * ranges[index])
                yield dict(symbol=symbol, family=family, direction=direction, available=available,
                           stop=stop, atr=ranges[index], close=current.close, open=current.open,
                           high=current.high, low=current.low, origin=origin,
                           regimes=[regime for regime in REGIMES if regime_allows(regime, direction,
                               higher[higher_index].close, fast[higher_index], slow[higher_index], higher_atr[higher_index])],
                           setup_id=f"{symbol}:{available}:{direction}:{family}")


def describe(trades, statuses=None, interval=False):
    values = [trade["net_r"] for trade in trades]
    losses = -sum(value for value in values if value < 0)
    return dict(n=len(values), mean_r=round(fmean(values), 5) if values else None,
                pf=round(sum(value for value in values if value > 0) / losses, 4) if losses else None,
                stress_r=round(fmean(trade["stress_r"] for trade in trades), 5) if trades else None,
                ambiguous=sum(trade["ambiguous"] for trade in trades),
                symbols=len({trade["symbol"] for trade in trades}),
                weeks=len({(trade["available"] + 3 * 86400) // (7 * 86400) for trade in trades}),
                statuses=dict(statuses or {}), weekly_bootstrap95=replay.weekly_interval(trades) if interval else None)


def audit_records(path):
    last_release, totals, resolved, sums = {}, Counter(), Counter(), defaultdict(float)
    with gzip.open(path, "rt") as stream:
        for line in stream:
            trade = json.loads(line)
            identity = (trade["symbol"], trade["config"])
            if trade["available"] < last_release.get(identity, 0):
                raise ValueError(f"Overlapping admission: {identity}")
            if trade["release"] <= trade["available"]:
                raise ValueError("Nonpositive reservation")
            last_release[identity] = trade["release"]
            totals[trade["status"]] += 1
            if trade["status"] != "resolved":
                continue
            if not trade["available"] <= trade["entry_time"] < trade["exit_time"] <= trade["release"]:
                raise ValueError("Noncausal execution timestamps")
            if "|m15_" in trade["config"] and trade["entry_time"] < trade["available"] + 900:
                raise ValueError("M15 confirmation executed before next open")
            if not math.isfinite(trade["net_r"]) or trade["stress_r"] > trade["net_r"]:
                raise ValueError("Invalid accounting")
            universe = "gold" if trade["symbol"] == "XAUUSD" else "fx"
            key = (universe, trade["config"], trade["fold"])
            resolved[key] += 1
            sums[key] += trade["net_r"]
    return totals, resolved, sums


def run(data, output, cutoff, slippage_bps=1.0):
    records_path = output.with_suffix(".records.jsonl.gz")
    manifest_path = output.with_suffix(".manifest.json")
    if any(path.exists() for path in (output, records_path, manifest_path)):
        raise ValueError("Output exists; choose new paths")
    rows, specs, paired = replay.load_data(data)
    for symbol, (bids, asks, _) in list(paired.items()):
        if any(ask.high < bid.high or ask.low < bid.low for bid, ask in zip(bids, asks)):
            raise ValueError(f"Crossed bid/ask extremes: {symbol}")
        closed = [(bid, ask) for bid, ask in zip(bids, asks) if bid.time + replay.STEP <= cutoff]
        paired[symbol] = ([item[0] for item in closed], [item[1] for item in closed], [item[0].time for item in closed])
    start = max(value[0][0].time for value in paired.values()) + 14 * 86400
    end = min(value[0][-1].time for value in paired.values()) + replay.STEP
    split1 = (start + int((end - start) * 0.6)) // 86400 * 86400
    split2 = (start + int((end - start) * 0.8)) // 86400 * 86400
    configs = [f"{family}|{regime}|{mode}|{target}R|{session}" for family in FAMILIES
               for regime in REGIMES for mode in replay.MODES for target in replay.TARGETS for session in ("all", "liquid")]
    manifest = dict(families=FAMILIES, regimes=REGIMES, modes=replay.MODES, targets=replay.TARGETS,
                    configs=configs, count=len(configs), start=start, end=end, split1=split1, split2=split2, as_of=cutoff,
                    slippage_bps_per_side=slippage_bps,
                    hashes={str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in
                            (data / "workspace_candles.sql.gz", data / "forex_specs.json", Path(__file__), Path(replay.__file__))},
                    selection="Discovery only: n>=40 FX/20 Gold, >=6 weeks, >=4 FX symbols, mean>0, stress>0; best mean. Report top five without promotion.",
                    caveats=["Exploratory reused history, not untouched holdout; no multiplicity-adjusted inference",
                             "Dated Oct23/24 fee specs; declared slippage per side; observed bid/ask; stress approximate, not resimulation",
                             "One position per symbol/config, not portfolio; fixed setup target except tight-stop entry",
                             "Direct detector events, deduplicated origin; not exact live AST lifecycle or exit-policy replay",
                             "Inside bar/NR7 use setup candle direction, not a two-sided OCO order",
                             "All positions exit before NY rollover; no carry/swap, no tick or news data"])
    manifest_path.write_text(json.dumps(manifest, indent=2))
    trades, counts, coverage = defaultdict(list), defaultdict(Counter), {}
    with gzip.open(records_path, "wt") as stream:
        for symbol in sorted(specs):
            bids, asks, times = paired[symbol]
            ranges, busy = replay.atr(bids), defaultdict(int)
            universe = "gold" if symbol == "XAUUSD" else "fx"
            coverage[symbol] = dict(first=times[0], last_close=times[-1] + 900, bars=len(times), signals=Counter())
            for signal in signals(symbol, rows[symbol]):
                available = signal["available"]
                fold = replay.partition(available, split1, split2)
                if not start <= available < end or fold == "embargo":
                    continue
                boundary = {"discovery": split1, "validation": split2, "recent": end}[fold]
                if available + (replay.HORIZON + 4) * replay.STEP > boundary:
                    continue
                coverage[symbol]["signals"][signal["family"]] += 1
                for mode in replay.MODES:
                    execution = replay.fill(signal, mode, bids, asks, times, ranges)
                    for session in ("all", "liquid"):
                        if session == "liquid" and not replay.liquid(available):
                            continue
                        for target in replay.TARGETS:
                            outcome = None
                            for regime in signal["regimes"]:
                                key = f"{signal['family']}|{regime}|{mode}|{target}R|{session}"
                                if available < busy[key]:
                                    counts[(universe, key, fold)]["busy"] += 1
                                    continue
                                if outcome is None:
                                    outcome = (replay.simulate(signal, execution, bids, asks, specs[symbol], paired, target, slippage_bps)
                                               if execution["status"] == "filled" else execution)
                                    if session == "liquid" and outcome.get("entry_time") and not replay.liquid(outcome["entry_time"]):
                                        outcome = dict(status="session_expired", release=outcome["entry_time"] + 900)
                                busy[key] = outcome["release"]
                                counts[(universe, key, fold)][outcome["status"]] += 1
                                stream.write(json.dumps({**signal, **outcome, "config": key, "fold": fold}, separators=(",", ":")) + "\n")
                                if outcome["status"] == "resolved":
                                    trades[(universe, key)].append({field: value for field, value in
                                        {**outcome, "symbol": symbol, "fold": fold, "available": available}.items()
                                        if field in ("symbol", "fold", "available", "net_r", "stress_r", "ambiguous")})
            print(symbol, "complete", sum(coverage[symbol]["signals"].values()), flush=True)
    summary = {}
    for universe in ("fx", "gold"):
        table = {}
        for key in configs:
            items = trades[(universe, key)]
            total_counts = sum((counts[(universe, key, fold)] for fold in FOLDS), Counter())
            table[key] = {"all": describe(items, total_counts),
                          **{fold: describe([item for item in items if item["fold"] == fold], counts[(universe, key, fold)]) for fold in FOLDS},
                          "by_symbol": {symbol: describe([item for item in items if item["symbol"] == symbol]) for symbol in sorted({item["symbol"] for item in items})}}
        eligible = [key for key, value in table.items() if value["discovery"]["n"] >= (20 if universe == "gold" else 40)
                    and value["discovery"]["symbols"] >= (1 if universe == "gold" else 4)
                    and value["discovery"]["weeks"] >= 6 and value["discovery"]["mean_r"] > 0
                    and value["discovery"]["stress_r"] > 0]
        shortlist = sorted(eligible, key=lambda key: (-table[key]["discovery"]["mean_r"], key))[:5]
        for key in shortlist:
            for fold in FOLDS:
                table[key][fold] = describe([item for item in trades[(universe, key)] if item["fold"] == fold], counts[(universe, key, fold)], interval=True)
        summary[universe] = dict(eligible=len(eligible), selected=shortlist[0] if shortlist else None,
                                 discovery_top5=shortlist, configs=table)
    output.write_text(json.dumps(dict(manifest=manifest, coverage=coverage, summary=summary, records=str(records_path)), indent=2, allow_nan=False))
    print("Saved", output, flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--as-of", type=int, required=True)
    parser.add_argument("--slippage-bps", type=float, default=1.0)
    args = parser.parse_args()
    if not math.isfinite(args.as_of) or args.as_of <= 0:
        parser.error("--as-of must be positive Unix seconds")
    if not math.isfinite(args.slippage_bps) or args.slippage_bps < 0:
        parser.error("--slippage-bps must be finite and nonnegative")
    run(args.data, args.output, args.as_of, args.slippage_bps)
