"""Local candle-feature ridge experiment; no production changes or external APIs.

Run: /private/tmp/btvenv3/bin/python -m scripts.candle_ml_decision
Archived stop/risk/entry fields can contain future information: labels only.
"""

import bisect
import hashlib
import json
import math
import pickle
import random
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import fmean, median

DATA = Path('research-output/futures-nov01/mexc_10pairs_340d_compare.pkl')
DIRECTORY = DATA.parent / 'confirmation'
OUTPUT = Path('research-output/candle-ml-decision.json')
ARMS = ('market', 'retest', 'sweep')


def features(candles, stamp, side, template):
    times = [bar.time for bar in candles]
    end = bisect.bisect_right(times, stamp)
    rows = candles[max(0, end - 256):end]
    if len(rows) < 256 or rows[-1].time != stamp:
        raise ValueError('Insufficient point-in-time candle coverage')
    direction = 1 if side == 'long' else -1
    current = rows[-1]
    ranges = [max(bar.high - bar.low, 1e-12) for bar in rows]
    atr = fmean(max(bar.high - bar.low, abs(bar.high - prev.close),
                    abs(bar.low - prev.close)) for prev, bar in zip(rows[-15:-1], rows[-14:]))
    atr = max(atr, 1e-12)
    width = ranges[-1]
    values = [direction, direction * (current.close - current.open) / width,
              (current.high - max(current.open, current.close)) / width,
              (min(current.open, current.close) - current.low) / width,
              width / atr, atr / current.close,
              current.volume / max(fmean(bar.volume for bar in rows[-21:-1]), 1e-12),
              fmean(ranges[-4:]) / max(fmean(ranges[-32:]), 1e-12)]
    for length in (4, 16, 64, 192):
        window = rows[-length:]
        highest, lowest = max(bar.high for bar in window), min(bar.low for bar in window)
        values.extend([direction * (current.close - rows[-length-1].close) / atr,
                       direction * (current.close - fmean(bar.close for bar in window)) / atr,
                       (current.close - lowest) / max(highest - lowest, 1e-12)])
    hour = (stamp + 900) % 86400 / 86400 * 2 * math.pi
    values.extend([math.sin(hour), math.cos(hour)])
    values.extend(float(template == candidate) for candidate in range(26))
    return values


def fit(rows, targets, penalty):
    count, size = len(rows), len(rows[0])
    means = [fmean(row[column] for row in rows) for column in range(size)]
    scales = [max(math.sqrt(fmean((row[column] - means[column]) ** 2 for row in rows)),
                  1e-8) for column in range(size)]
    design = [[1.0] + [max(-5, min(5, (value - mean) / scale))
                        for value, mean, scale in zip(row, means, scales)] for row in rows]
    size += 1
    matrix = [[0.0] * (size + 1) for _ in range(size)]
    for row, target in zip(design, targets):
        for column in range(size):
            weighted = row[column] / count
            matrix[column][-1] += weighted * target
            for other in range(column, size):
                matrix[column][other] += weighted * row[other]
    for column in range(size):
        for other in range(column):
            matrix[column][other] = matrix[other][column]
        matrix[column][column] += penalty if column else 1e-10
    for column in range(size):
        pivot = max(range(column, size), key=lambda index: abs(matrix[index][column]))
        matrix[column], matrix[pivot] = matrix[pivot], matrix[column]
        divisor = matrix[column][column]
        matrix[column] = [value / divisor for value in matrix[column]]
        for other in range(size):
            if other != column:
                factor = matrix[other][column]
                matrix[other] = [value - factor * source
                                 for value, source in zip(matrix[other], matrix[column])]
    return means, scales, [row[-1] for row in matrix]


def predict(model, row):
    means, scales, weights = model
    return weights[0] + sum(weight * max(-5, min(5, (value - mean) / scale))
                           for value, mean, scale, weight in zip(row, means, scales, weights[1:]))


def eligible(rows, boundary):
    return [row for row in rows if row['available'] <= boundary and row['time'] + 900 < boundary]


def metrics(values):
    losses = -sum(value for value in values if value < 0)
    return dict(n=len(values), mean_r=fmean(values) if values else None, total_r=sum(values),
                pf=sum(value for value in values if value > 0) / losses if losses else None)


def interval(rows, deltas):
    weeks = defaultdict(list)
    for row, delta in zip(rows, deltas):
        weeks[row['time'] // (86400 * 7)].append(delta)
    blocks, rng = list(weeks.values()), random.Random(42)
    draws = []
    for _ in range(1000):
        selected = [rng.choice(blocks) for _ in blocks]
        draws.append(sum(map(sum, selected)) / sum(map(len, selected)))
    draws.sort()
    return [draws[25], draws[974]]


def evaluate(rows, models, threshold):
    scores = [predict(models[0], row['x']) for row in rows]
    baseline = [row['y'][0] for row in rows]
    choices = [max(range(3), key=lambda arm: predict(models[arm], row['x'])) for row in rows]
    selected = [row['y'][arm] for row, arm in zip(rows, choices)]
    raw_weights = [1.5 if score >= threshold else 0.5 for score in scores]
    normalizer = fmean(raw_weights)
    weighted = [value * weight / normalizer for value, weight in zip(baseline, raw_weights)]
    ordered = sorted(range(len(rows)), key=lambda index: scores[index])
    quartiles = [metrics([baseline[index] for index in ordered[len(rows)*part//4:len(rows)*(part+1)//4]])
                 for part in range(4)]
    return dict(baseline=metrics(baseline), fixed_arms={arm: metrics([row['y'][index] for row in rows])
                for index, arm in enumerate(ARMS)}, quartiles_low_to_high=quartiles,
                all_signals_sizing=metrics(weighted), sizing_delta_ci95=interval(rows, [new-old for new, old in zip(weighted, baseline)]),
                dynamic_arm=metrics(selected), dynamic_delta_ci95=interval(rows, [new-old for new, old in zip(selected, baseline)]),
                dynamic_vs_fixed={arm: dict(mean_delta_r=fmean(value-row['y'][index] for row, value in zip(rows, selected)),
                                            delta_ci95=interval(rows, [value-row['y'][index] for row, value in zip(rows, selected)]))
                                  for index, arm in enumerate(ARMS[1:], 1)},
                dynamic_filled_reference_r=metrics([value for row, arm, value in zip(rows, choices, selected) if row['filled'][arm]]),
                choices={arm: choices.count(index) for index, arm in enumerate(ARMS)},
                dynamic_filled=sum(row['filled'][arm] for row, arm in zip(rows, choices)),
                symbols={symbol: dict(baseline=metrics([value for row, value in zip(rows, baseline) if row['symbol'] == symbol]),
                                     sizing=metrics([value for row, value in zip(rows, weighted) if row['symbol'] == symbol]))
                         for symbol in sorted({row['symbol'] for row in rows})})


def main():
    raw = pickle.loads(DATA.read_bytes())
    grouped = defaultdict(list)
    split = None
    for symbol, data in raw.items():
        payload = json.loads((DIRECTORY / (symbol + '.json')).read_text())
        if split is not None and split != payload['split']:
            raise ValueError('Unaligned train/validation split')
        split = payload['split']
        cohorts = defaultdict(dict)
        for sample in payload['samples']:
            key = sample['gate'], sample['template'], sample['candle_time'], sample['side']
            cohorts[key][sample['arm']] = sample
        cache = {}
        for (gate, template, stamp, side), arms in cohorts.items():
            if set(arms) != set(ARMS) or any(sample['status'] not in ('resolved', 'cancelled') for sample in arms.values()):
                continue
            key = stamp, side, template
            if key not in cache:
                cache[key] = features(data[2], stamp, side, template)
            available = max(sample['label_available_at'] for sample in arms.values())
            targets = [0.0 if arms[arm]['status'] == 'cancelled' else
                       arms[arm]['net_r'] * arms[arm]['risk'] / arms[arm]['reference_risk'] for arm in ARMS]
            grouped[gate].append(dict(time=stamp, available=available, symbol=symbol, x=cache[key],
                                      y=targets, filled=[arms[arm]['status'] == 'resolved' for arm in ARMS]))
    report = dict(data_sha256=hashlib.sha256(DATA.read_bytes()).hexdigest(),
                  code_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), split=split,
                  validation_last_signal_utc=datetime.fromtimestamp(max(row['time'] for rows in grouped.values() for row in rows), timezone.utc).isoformat(),
                  split_utc=datetime.fromtimestamp(split, timezone.utc).isoformat(),
                  method='Ridge; 48 closed-M15 rolling features; 3 arm models; chronological inner holdout selects penalty by MSE; label availability purged at both boundaries',
                  limitations=['Signal cohort conditioned on historical baseline occupancy; not independent candle opportunities or a portfolio replay',
                               'Baseline is archived frozen seed replay, not the live UCB/genetic allocation or a fresh VPS snapshot',
                               'R uses common archived reference risk; net of replay costs; cancellation is zero and is NOT a filled trade',
                               'Sizing keeps every market signal; normalized to equal total validation risk, descriptive not executable portfolio sizing',
                               'No LLM/vision model tested; no inference on all AI methods; validation already used in prior research',
                               'Rolling 16/64-bar windows are not aligned closed H4/D1 bars; symbol not used as predictor'], gates={})
    for gate, rows in grouped.items():
        rows.sort(key=lambda row: row['time'])
        train, validation = eligible(rows, split), [row for row in rows if row['time'] >= split]
        inner_split = sorted(row['time'] for row in train)[len(train) * 3 // 4]
        inner = eligible(train, inner_split)
        holdout = [row for row in train if row['time'] >= inner_split]
        models, penalties = [], []
        for arm in range(3):
            candidates = []
            for penalty in (0.01, 0.1, 1.0):
                model = fit([row['x'] for row in inner], [row['y'][arm] for row in inner], penalty)
                error = fmean((predict(model, row['x']) - row['y'][arm]) ** 2 for row in holdout)
                candidates.append((error, penalty))
            penalty = min(candidates)[1]
            penalties.append(penalty)
            models.append(fit([row['x'] for row in train], [row['y'][arm] for row in train], penalty))
        threshold = median(predict(models[0], row['x']) for row in train)
        report['gates'][gate] = dict(train_n=len(train), validation_n=len(validation), penalties=penalties,
                                    train=evaluate(train, models, threshold), validation=evaluate(validation, models, threshold))
        print(gate, json.dumps(report['gates'][gate]['validation']), flush=True)
    OUTPUT.write_text(json.dumps(report, indent=2, allow_nan=False) + '\n')


if __name__ == '__main__':
    main()
