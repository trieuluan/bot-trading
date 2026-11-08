"""Local gene ablation using the existing replay engine; no live population changes.

python -m scripts.futures_gene_ablation --data research-output/futures-nov01 \
    --output research-output/futures-ablation.json [--workers 4]

python -m scripts.futures_gene_ablation --summarize research-output/futures-gene-ablation.json
"""

import argparse
import copy
import hashlib
import json
import random
import time
from collections import defaultdict
from multiprocessing import Pool
from pathlib import Path

from backend.app import futures_history, futures_rule_tree
from backend.app.futures_learner import WARMUP
from backend.app.futures_strategies import RULE_SEEDS, validate_genome
from backend.app.futures_symbols import SYMBOLS, symbol_scope
from scripts.futures_h4_variants import load
from scripts.futures_replay_340d import setup_memo

# Arms test the 4 candidate genes:
# 1. volume_dryup vs breakout on seed 11
# 2. candle_direction vs volume on seed 10 (bb_pierce) and seed 23 (fvg)
# 3. exit=fixed vs adaptive vs trail on seed 0 (breakout) and seed 1 (ema_bounce)
def candidates():
    result = {}
    # Arm 1: H1 volume_dryup ablation
    s11 = copy.deepcopy(RULE_SEEDS[11])
    validate_genome(s11)
    result["dryup:orig"] = {"genome": s11}
    s11_alt = copy.deepcopy(s11)
    s11_alt["rule"]["children"][1] = {"feature": "breakout", "frame": "H1"}
    validate_genome(s11_alt)
    result["dryup:swap_breakout"] = {"genome": s11_alt}

    # Arm 2: M15 candle_direction ablation
    for idx in (10, 23):
        base = copy.deepcopy(RULE_SEEDS[idx])
        validate_genome(base)
        result[f"cdir_{idx}:orig"] = {"genome": base}
        alt = copy.deepcopy(base)
        alt["rule"]["children"][2] = {"feature": "volume", "frame": "M15"}
        validate_genome(alt)
        result[f"cdir_{idx}:swap_volume"] = {"genome": alt}

    # Arm 3: exit policy ablation (fixed vs adaptive vs trail)
    for idx in (0, 1):
        for policy in ("adaptive", "fixed", "trail"):
            g = copy.deepcopy(RULE_SEEDS[idx])
            g["exit_policy"] = policy
            validate_genome(g)
            result[f"exit_{idx}:{policy}"] = {"genome": g}
    return result

def run_pair(task):
    symbol, data = task
    setup_memo()
    raw, first, last, clip = load(data)
    series = {name: clip(raw[name][2]) for name in SYMBOLS}
    captured = []
    original_stats = futures_history.historical_stats
    original_h4 = futures_rule_tree.h4_matches
    cache, cache_stamp = {}, None

    def cached_h4(feature, rows, candles, direction, context):
        nonlocal cache_stamp
        stamp = candles[-1].time
        if stamp != cache_stamp:
            cache.clear()
            cache_stamp = stamp
        key = (feature, direction, tuple(bar.time for bar in rows))
        if key not in cache:
            cache[key] = original_h4(feature, rows, candles, direction, context)
        return cache[key]

    def capture(samples, cutoff=None):
        captured.append(samples)
        return original_stats(samples, cutoff)

    started = time.monotonic()
    population = candidates()
    futures_history.historical_stats = capture
    futures_rule_tree.h4_matches = cached_h4
    try:
        with symbol_scope(symbol):
            futures_history.replay(
                population, series[symbol], clip(raw[symbol][3]), raw[symbol][4],
                max(raw[symbol][0], first + WARMUP * 900), last + 900,
                btc_candles=series["BTC_USDT"] if symbol != "BTC_USDT" else None,
                benchmarks={name: rows for name, rows in series.items() if name != symbol},
            )
    finally:
        futures_history.historical_stats = original_stats
        futures_rule_tree.h4_matches = original_h4
    rows = []
    for index, name in enumerate(population):
        arm, variant = name.split(":")
        for offset, part in enumerate(("train", "validation")):
            for sample in captured[index * 2 + offset]:
                if sample["status"] == "resolved":
                    rows.append((symbol, arm, variant, part, sample["candle_time"],
                                 sample["side"], sample["net_r"], sample["exit_reason"]))
    print(f"{symbol}: DONE {time.monotonic() - started:.1f}s, {len(rows)} resolved", flush=True)
    return symbol, rows

def bootstrap_diff(a_vals, b_vals, seed=1):
    days = defaultdict(lambda: ([], []))
    for stamp, value in a_vals:
        days[stamp // 86400][0].append(value)
    for stamp, value in b_vals:
        days[stamp // 86400][1].append(value)
    if not a_vals or not b_vals:
        return None
    rng = random.Random(seed)
    groups = list(days.values())
    diffs = []
    for _ in range(1000):
        pick = [rng.choice(groups) for _ in groups]
        sa = sum(sum(g[0]) for g in pick)
        na = sum(len(g[0]) for g in pick)
        sb = sum(sum(g[1]) for g in pick)
        nb = sum(len(g[1]) for g in pick)
        if na and nb:
            diffs.append(sa / na - sb / nb)
    diffs.sort()
    return [diffs[int(len(diffs) * 0.025)], diffs[min(len(diffs) - 1, int(len(diffs) * 0.975))]]

def summary(rows):
    groups = defaultdict(list)
    for row in rows:
        groups[row['arm'], row['var'], row['part']].append(row)
    cohorts = {}
    for key, items in groups.items():
        values = [row['r'] for row in items]
        cohorts[':'.join(key)] = dict(
            n=len(values), mean_r=sum(values) / len(values),
            days=len({row['t'] // 86400 for row in items}),
            positive_symbols=sum(sum(row['r'] for row in items if row['sym'] == symbol) > 0
                                 for symbol in SYMBOLS),
        )
    comparisons = {}
    for arm, variant, baseline in (
        ('dryup', 'orig', 'swap_breakout'),
        ('cdir_10', 'orig', 'swap_volume'),
        ('cdir_23', 'orig', 'swap_volume'),
        ('exit_0', 'fixed', 'adaptive'), ('exit_0', 'trail', 'adaptive'),
        ('exit_1', 'fixed', 'adaptive'), ('exit_1', 'trail', 'adaptive'),
    ):
        for part in ('train', 'validation'):
            left, right = groups[arm, variant, part], groups[arm, baseline, part]
            left_values, right_values = ([(row['t'], row['r']) for row in items] for items in (left, right))
            paired = {}
            for label, items in (('left', left), ('right', right)):
                for row in items:
                    paired.setdefault((row['sym'], row['t'], row['side']), {})[label] = row['r']
            differences = [(stamp, values['left'] - values['right'])
                           for (_, stamp, _), values in paired.items() if len(values) == 2]
            comparisons[f'{arm}:{variant}-minus-{baseline}:{part}'] = dict(
                left_n=len(left), right_n=len(right),
                mean_difference=(sum(value for _, value in left_values) / len(left_values)
                                 - sum(value for _, value in right_values) / len(right_values))
                                if left_values and right_values else None,
                difference_ci95=bootstrap_diff(left_values, right_values),
                common_signals=len(differences),
                common_signal_mean_difference=sum(value for _, value in differences) / len(differences)
                                              if differences else None,
                common_signal_ci95=bootstrap_diff(differences, [(stamp, 0) for stamp, _ in differences]),
            )
    return dict(cohorts=cohorts, comparisons=comparisons)

def save_summary(path):
    result = summary(json.loads(path.read_text()))
    path.with_suffix('.summary.json').write_text(json.dumps(result, indent=2, allow_nan=False))
    for key, row in result['comparisons'].items():
        print(key, json.dumps(row))
    return result

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=Path("research-output/futures-nov01"))
    parser.add_argument("--output", type=Path, default=Path("research-output/futures-gene-ablation.json"))
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--summarize", type=Path)
    args = parser.parse_args()
    if args.summarize:
        save_summary(args.summarize)
        return
    if not 1 <= args.workers <= len(SYMBOLS):
        parser.error('workers must be between 1 and 10')
    manifest = dict(candidates=candidates(), data_sha256=hashlib.sha256(
        (args.data / 'mexc_10pairs_340d_compare.pkl').read_bytes()).hexdigest(),
        source_sha256={str(path): hashlib.sha256(path.read_bytes()).hexdigest()
                       for path in [Path(__file__), *Path('backend/app').glob('*.py'),
                                    Path('scripts/futures_replay_340d.py'), Path('scripts/futures_h4_variants.py')]})
    args.output.with_suffix('.manifest.json').write_text(json.dumps(manifest, indent=2))
    tasks = [(sym, str(args.data)) for sym in SYMBOLS]
    with Pool(args.workers) as pool:
        results = pool.map(run_pair, tasks)
    all_rows = [r for _, rows in results for r in rows]
    # Summarize by (arm, variant, part)
    by_cohort = defaultdict(list)
    for row in all_rows:
        _, arm, variant, part, stamp, _, net_r, _ = row
        by_cohort[arm, variant, part].append((stamp, net_r))
        by_cohort[arm, variant, "all"].append((stamp, net_r))
    arms = sorted({r[1] for r in all_rows})
    print("\n" + "=" * 90)
    print(f"{'cohort':26} | {'all n':>5} {'all mean':>8} {'win':>5} | {'train n':>7} {'train':>6} | {'test n':>6} {'test':>6}")
    print("-" * 90)
    for arm in arms:
        variants = sorted({r[2] for r in all_rows if r[1] == arm})
        for v in variants:
            a = by_cohort[arm, v, "all"]
            tr = by_cohort[arm, v, "train"]
            te = by_cohort[arm, v, "validation"]
            ma = sum(x[1] for x in a) / len(a) if a else 0
            mtr = sum(x[1] for x in tr) / len(tr) if tr else 0
            mte = sum(x[1] for x in te) / len(te) if te else 0
            win = sum(x[1] > 0 for x in a) / len(a) if a else 0
            print(f"{arm + ':' + v:26} | {len(a):5} {ma:+8.3f}R {win:5.1%} | {len(tr):7} {mtr:+6.3f} | {len(te):6} {mte:+6.3f}")
        print("-" * 90)
    args.output.write_text(json.dumps([{"sym": s, "arm": a, "var": v, "part": p, "t": t, "side": sd, "r": r, "why": w}
                                       for s, a, v, p, t, sd, r, w in all_rows], indent=1))
    save_summary(args.output)

if __name__ == "__main__":
    main()
