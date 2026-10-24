"""Futures H4 gate replay: 6 pre-declared variants vs baseline. Research only: no DB or bot writes.

python -m scripts.futures_h4_variants run --data research-output/futures-h4 --output <json> [--workers N]
python -m scripts.futures_h4_variants live --data research-output/futures-h4 --output <json>
"""

import argparse
import bisect
import json
import pickle
import sys
from collections import defaultdict
from math import sqrt
from multiprocessing import Pool
from pathlib import Path

from backend.app import futures_history
from backend.app.futures_learner import WARMUP
from backend.app.futures_rule_tree import adx_state, confirmed_pivots, is_htf_rule
from backend.app.futures_rules import atr14, ema50
from backend.app.futures_strategies import genome_name
from backend.app.futures_symbols import SYMBOLS
from backend.app.strategy import aggregate
from scripts.futures_replay_340d import setup_memo

VARIANTS = ("baseline", "C_dist", "I_market", "B_structure", "A_slope", "G_adx", "K_support")
H4 = 14400


def h4_state(rows):
    """Direction-free facts from closed H4 rows (same window the live trend rule sees)."""
    if len(rows) < 50:
        return None
    ema, ema_old = ema50(rows), ema50(rows[:-3])
    highs = [i for i in confirmed_pivots(rows, 1, 20)]
    lows = [i for i in confirmed_pivots(rows, -1, 20)]
    return {
        "close": rows[-1].close,
        "ema": ema,
        "ema_old": ema_old,
        "atr": atr14(rows),
        "adx": adx_state(rows),
        "high_piv": [rows[i].high for i in highs[-2:]],
        "low_piv": [rows[i].low for i in lows[-2:]],
    }


def trend_ok(state, d):
    return d * (state["close"] - state["ema"]) > 0


def allowed(variant, d, state, btc=None, breadth=None):
    """d=+1 long, -1 short. state/btc from h4_state; breadth maps side -> share of coins aligned."""
    if variant == "baseline":
        return True
    if state is None:
        return False
    if variant == "C_dist":
        return d * (state["close"] - state["ema"]) >= 0.5 * state["atr"]
    if variant == "I_market":
        return btc is not None and trend_ok(btc, d) and breadth[d] >= 0.5
    if variant == "B_structure":
        hp, lp = state["high_piv"], state["low_piv"]
        return len(hp) == 2 and len(lp) == 2 and d * (hp[1] - hp[0]) > 0 and d * (lp[1] - lp[0]) > 0
    if variant == "A_slope":
        return d * (state["ema"] - state["ema_old"]) > 0
    if variant == "G_adx":
        return state["adx"] is not None and state["adx"][0] > 25 and d * state["adx"][1] > 0
    if variant == "K_support":
        pivots = state["low_piv"] if d == 1 else state["high_piv"]
        if not pivots:
            return False
        gap = d * (state["close"] - pivots[-1])
        return 0 <= gap <= state["atr"]
    raise ValueError(variant)


def aligned_share(states):
    live = [s for s in states if s is not None]
    return {d: (sum(trend_ok(s, d) for s in live) / len(live) if live else 0.0) for d in (1, -1)}


def build_states(series):
    """symbol -> {bar_time: state}; recomputed only when the H4 window changes (every 16 bars)."""
    result = {}
    for symbol, candles in series.items():
        full = aggregate(candles, H4)
        times = [row.time for row in full]
        cache, table = {}, {}
        for index in range(WARMUP - 1, len(candles)):
            now = candles[index].time + 900
            first = candles[index - WARMUP + 1].time
            lo = bisect.bisect_left(times, -(-first // H4) * H4)
            hi = bisect.bisect_right(times, now - H4)
            if (lo, hi) not in cache:
                cache[(lo, hi)] = h4_state(full[lo:hi])
            table[candles[index].time] = cache[(lo, hi)]
        result[symbol] = table
    return result


def load(data):
    raw = pickle.loads((Path(data) / "mexc_10pairs_340d_compare.pkl").read_bytes())
    first = max(raw[s][2][0].time for s in SYMBOLS)
    last = min(raw[s][2][-1].time for s in SYMBOLS)
    def clip(rows):
        return [bar for bar in rows if first <= bar.time <= last]
    return raw, first, last, clip


def run_task(task):
    symbol, variant, data, candidates = task
    setup_memo()
    raw, first, last, clip = load(data)
    series = {s: clip(raw[s][2]) for s in SYMBOLS}
    states = build_states(series)
    real, captured = futures_history.strategy_setup, []
    original_stats = futures_history.historical_stats
    side_cache = {}

    def gated(genome, history, funding, **kwargs):
        stamp = history[-1].time
        if stamp not in side_cache:
            shares = aligned_share([states[s].get(stamp) for s in SYMBOLS])
            side_cache[stamp] = [
                d
                for d in (1, -1)
                if allowed(
                    variant, d, states[symbol].get(stamp), states["BTC_USDT"].get(stamp), shares
                )
            ]
        sides = side_cache[stamp]
        if not sides:
            return None
        setup = real(genome, history, funding, **kwargs)
        return setup if setup and (1 if setup.side == "long" else -1) in sides else None

    def capture(samples):
        captured.append(samples)
        return original_stats(samples)

    futures_history.strategy_setup, futures_history.historical_stats = gated, capture
    try:
        _, _, _, fair, funding = raw[symbol]
        start = max(raw[symbol][0], first + WARMUP * 900)
        names = {genome_name(g): g for g in candidates}
        futures_history.replay(
            {name: {"genome": g} for name, g in names.items()},
            series[symbol],
            clip(fair),
            funding,
            start,
            last + 900,
            btc_candles=series["BTC_USDT"] if symbol != "BTC_USDT" else None,
            benchmarks={s: series[s] for s in SYMBOLS if s != symbol},
        )
    finally:
        futures_history.strategy_setup, futures_history.historical_stats = real, original_stats
    rows = []
    for index, name in enumerate(names):
        for offset, part in enumerate(("train", "validation")):
            for sample in captured[index * 2 + offset]:
                if sample["status"] == "resolved":
                    rows.append(
                        (
                            symbol,
                            variant,
                            part,
                            name,
                            sample["candle_time"],
                            sample["side"],
                            sample["net_r"],
                            sample["exit_reason"],
                        )
                    )
    return rows


def summarize(rows):
    """rows -> trade stats plus event-level stats (one event per symbol/time/side, overlapping candidates averaged)."""
    if not rows:
        return {"trades": 0}
    values = [r[6] for r in rows]
    gains, losses = sum(v for v in values if v > 0), -sum(v for v in values if v < 0)
    events = defaultdict(list)
    for r in rows:
        events[(r[0], r[4], r[5])].append(r[6])
    event_values = {key: sum(v) / len(v) for key, v in events.items()}
    days = defaultdict(float)
    day_counts = defaultdict(int)
    for (_, stamp, _), value in event_values.items():
        days[stamp // 86400] += value
        day_counts[stamp // 86400] += 1
    n = len(event_values)
    mean = sum(event_values.values()) / n
    se = (
        sqrt(
            sum(
                (total - mean * day_counts[day]) ** 2
                for day, total in days.items()
            )
        )
        / n
    )
    return {
        "trades": len(values),
        "mean_r": sum(values) / len(values),
        "total_r": sum(values),
        "pf": gains / losses if losses else None,
        "win": sum(v > 0 for v in values) / len(values),
        "events": n,
        "event_mean_r": mean,
        "event_ci95": [mean - 1.96 * se, mean + 1.96 * se],
        "event_win": sum(v > 0 for v in event_values.values()) / n,
    }


def report(rows):
    out = {}
    base = {
        part: {(r[0], r[3], r[4], r[5]): r for r in rows if r[1] == "baseline" and r[2] == part}
        for part in ("train", "validation")
    }
    for variant in VARIANTS:
        out[variant] = {}
        for part in ("train", "validation"):
            mine = [r for r in rows if r[1] == variant and r[2] == part]
            entry = summarize(mine)
            kept = {(r[0], r[3], r[4], r[5]) for r in mine}
            removed = [r for key, r in base[part].items() if key not in kept]
            retained = [r for key, r in base[part].items() if key in kept]
            entry["baseline_removed"] = summarize(removed)
            entry["baseline_kept"] = summarize(retained)
            entry["by_symbol"] = {s: summarize([r for r in mine if r[0] == s]) for s in SYMBOLS}
            out[variant][part] = entry
    return out


def live(data, output):
    """Would each gate have blocked recent live auto trades? Uses read-only live-trades.json from the VPS."""
    snap = json.loads((Path(data) / "live-trades.json").read_text())
    from backend.app.feed import Candle

    raw, _, _, _ = load(data)
    series = {}
    for symbol, rows in snap["candles"].items():
        merged = {bar.time: bar for bar in raw[symbol][2]}
        merged.update({row["time"]: Candle(**row) for row in rows})
        series[symbol] = [merged[stamp] for stamp in sorted(merged)]
    stamps = {t["signal_time"] for t in snap["trades"]}
    states = {s: {} for s in series}
    for stamp in stamps:
        for symbol, candles in series.items():
            window = [c for c in candles if c.time <= stamp][-WARMUP:]
            states[symbol][stamp] = (
                h4_state(aggregate(window, H4)) if window and window[-1].time == stamp else None
            )
    rows = []
    for trade in snap["trades"]:
        if not is_htf_rule((trade.get("monitoring") or {}).get("exit_rule", {})):
            continue
        stamp, d = trade["signal_time"], 1 if trade["side"] == "long" else -1
        if states[trade["symbol"]].get(stamp) is None:
            continue
        risk = trade["monitoring"].get("risk")
        r = (
            trade["net_pnl"] / (risk * trade["quantity"])
            if trade["net_pnl"] is not None and risk
            else None
        )
        shares = aligned_share([states[s].get(stamp) for s in states])
        verdict = {
            v: allowed(v, d, states[trade["symbol"]][stamp], states["BTC_USDT"].get(stamp), shares)
            for v in VARIANTS
        }
        rows.append(
            {
                "id": trade["id"],
                "symbol": trade["symbol"],
                "side": trade["side"],
                "signal_time": stamp,
                "net_pnl": trade["net_pnl"],
                "r": r,
                "allowed": verdict,
            }
        )
    summary = {}
    for variant in VARIANTS:
        blocked = [x for x in rows if not x["allowed"][variant] and x["net_pnl"] is not None]
        kept = [x for x in rows if x["allowed"][variant] and x["net_pnl"] is not None]
        summary[variant] = {
            "blocked": len(blocked),
            "blocked_pnl": sum(x["net_pnl"] for x in blocked),
            "kept": len(kept),
            "kept_pnl": sum(x["net_pnl"] for x in kept),
        }
    Path(output).write_text(
        json.dumps(
            {
                "trades": rows,
                "summary": summary,
                "note": "Post-hoc filter of recorded HTF fills, not portfolio resimulation.",
            },
            indent=1,
            allow_nan=False,
        )
    )
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("run", "live"))
    parser.add_argument("--data", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--symbols", default=",".join(SYMBOLS))
    args = parser.parse_args(argv)
    if args.workers < 1 or any(symbol not in SYMBOLS for symbol in args.symbols.split(",")):
        parser.error("workers must be positive and symbols must belong to the configured universe")
    if Path(args.output).exists():
        parser.error("output exists; choose a new path to preserve prior results")
    if args.mode == "live":
        print(json.dumps(live(args.data, args.output), indent=1))
        return
    snap = json.loads((Path(args.data) / "live-snapshot.json").read_text())
    tasks = []
    for symbol in args.symbols.split(","):
        genomes = [
            g for g in snap["candidates"][symbol].values() if g.get("exit_policy") == "adaptive"
        ]
        unique = {genome_name(g): g for g in genomes}
        tasks += [(symbol, v, args.data, list(unique.values())) for v in VARIANTS]
    with Pool(args.workers) as pool:
        rows = []
        for completed, part in enumerate(pool.imap_unordered(run_task, tasks), 1):
            rows.extend(part)
            print(f"Completed {completed}/{len(tasks)} symbol/variant replays", flush=True)
    results = report(rows)
    Path(args.output).write_text(json.dumps({"rows": rows, "report": results}, allow_nan=False))
    for variant, parts in results.items():
        for part, s in parts.items():
            if s["trades"]:
                print(
                    f"{variant:12} {part:10} trades {s['trades']:6} events {s['events']:5} mean {s['mean_r']:+.4f} "
                    f"event {s['event_mean_r']:+.4f}",
                    file=sys.stdout,
                    flush=True,
                )


if __name__ == "__main__":
    main()
