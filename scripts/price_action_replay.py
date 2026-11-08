"""Offline Price Action archetype replay on MEXC M15 data. Research only: no DB or bot writes.

python -m scripts.price_action_replay --history research-output/price-action/vps-history.pkl \
    --recent research-output/price-action/vps-recent.json --output research-output/price-action/report.json

Signals use closed bars only; entry is next M15 open; one position per (symbol, setup, filter, stop).
"""

import argparse
import json
import pickle
import random
from collections import defaultdict
from pathlib import Path

from backend.app.futures_learner import FEE, SLIP
from backend.app.market import Candle

SETUPS = ("random_ctrl", "squeeze_sweep", "fvg", "order_block", "breaker", "bos_retest",
          "choch_retest", "equal_sweep", "asia_sweep", "pdh_pdl_sweep")
STOPS = ("wick", "h1_swing")
TARGETS = (1.0, 2.0, 3.0)
MAX_BARS = 96  # ponytail: fixed 24h time stop; replace with live exit policies if a setup survives
WARMUP = 1600

def load(history, recent):
    raw = pickle.loads(Path(history).read_bytes())
    series = {symbol: {bar.time: bar for bar in rows[2]} for symbol, rows in raw.items()}
    if recent:
        for row in json.loads(Path(recent).read_text()):
            symbol = row["symbol"].split(":")[0].replace("/", "_")
            if symbol in series and row["timeframe"] == "15m":
                series[symbol].setdefault(row["time"], Candle(*(row[key] for key in (
                    "time", "open", "high", "low", "close", "volume"))))
    result = {}
    for symbol, bars in series.items():
        rows = [bars[time] for time in sorted(bars)]
        end = len(rows)
        while end > 1 and rows[end - 1].time - rows[end - 2].time == 900:
            end -= 1
        result[symbol] = rows[end - 1:]  # longest gap-free tail
    first = max(rows[0].time for rows in result.values())
    return {symbol: [bar for bar in rows if bar.time >= first] for symbol, rows in result.items()}

def ema(values, period):
    weight, value, result = 2 / (period + 1), values[0], []
    for item in values:
        value += weight * (item - value)
        result.append(value)
    return result

def atr(rows, period=14):
    result, value = [], None
    for index, row in enumerate(rows):
        previous = rows[index - 1].close if index else row.close
        true_range = max(row.high - row.low, abs(row.high - previous), abs(row.low - previous))
        value = true_range if value is None else value + (true_range - value) / period
        result.append(value)
    return result

def bucket(rows, seconds):
    """Complete higher-timeframe candles only."""
    groups = defaultdict(list)
    for row in rows:
        groups[row.time // seconds * seconds].append(row)
    size = seconds // 900
    return [Candle(start, group[0].open, max(r.high for r in group), min(r.low for r in group),
                   group[-1].close, sum(r.volume for r in group))
            for start, group in sorted(groups.items()) if len(group) == size]

def trade(rows, index, direction, stop, target_r):
    """Enter at rows[index].open; stop wins when stop and target share a bar."""
    entry = rows[index].open * (1 + direction * SLIP)
    risk = direction * (entry - stop)
    if risk <= 0:
        return None
    target = entry + direction * target_r * risk
    end = min(len(rows) - 1, index + MAX_BARS - 1)
    for cursor in range(index, end + 1):
        row = rows[cursor]
        if row.low <= stop if direction == 1 else row.high >= stop:
            price, reason = stop, "stop"
            break
        if row.high >= target if direction == 1 else row.low <= target:
            price, reason = target, "target"
            break
    else:
        price, reason = rows[end].close, "time"
    exit_price = price * (1 - direction * SLIP)
    net = direction * (exit_price - entry) - (entry + exit_price) * FEE
    return net / risk, reason, cursor

def h1_swing_stop(entry, direction, h1_window, h1_atr_val):
    """Same formula as live adaptive_levels: 5-bar H1 swing + 0.25 ATR_H1, floored at 1.0 ATR_H1."""
    if len(h1_window) < 5 or not h1_atr_val:
        return None
    swing = min(r.low for r in h1_window[-5:]) if direction == 1 else max(r.high for r in h1_window[-5:])
    risk = max(direction * (entry - swing) + 0.25 * h1_atr_val, h1_atr_val)
    return entry - direction * risk

def pivot(rows, index, direction):
    value = (lambda row: row.high) if direction == 1 else (lambda row: row.low)
    return all(direction * (value(rows[index]) - value(rows[index + step])) > 0 for step in (-2, -1, 1, 2))

def replay(rows, seed=42):
    """Yield (setup, gate, stop_mode, candle_time, side, {target: R}, (entry_index, direction, stop))."""
    rng = random.Random(seed)
    closes = [row.close for row in rows]
    m15_atr, m15_ema = atr(rows), ema(closes, 21)
    h1, h4 = bucket(rows, 3600), bucket(rows, 14400)
    h1_atr = atr(h1)
    h4_ema = ema([row.close for row in h4], 50)
    h1_at = {row.time + 3600: index for index, row in enumerate(h1)}  # close time -> index
    h4_at = {row.time + 14400: index for index, row in enumerate(h4)}
    days = {row.time // 86400 for row in rows}
    day_hl = {}
    for day in days:
        group = [row for row in rows if row.time // 86400 == day]
        if len(group) == 96:
            day_hl[day] = (max(r.high for r in group), min(r.low for r in group))
    zones, levels = [], []
    highs, lows = [], []  # confirmed H1 swing (index, price)
    h1_index = max((i for i, r in enumerate(h1) if r.time + 3600 <= rows[WARMUP].time), default=None)
    h4_index = max((i for i, r in enumerate(h4) if r.time + 14400 <= rows[WARMUP].time), default=None)
    if h1_index is not None:
        for idx in range(2, h1_index - 1):
            for direction, pivots in ((1, highs), (-1, lows)):
                if pivot(h1, idx, direction):
                    pivots.append((idx, h1[idx].high if direction == 1 else h1[idx].low))
    busy = {}
    for index in range(WARMUP, len(rows) - 1):
        bar, close_time = rows[index], rows[index].time + 900
        if close_time in h4_at:
            h4_index = h4_at[close_time]
        if close_time in h1_at:
            k = h1_at[close_time]
            h1_index = k
            last = h1[k]
            if k >= 4:
                for direction, pivots in ((1, highs), (-1, lows)):
                    if pivot(h1, k - 2, direction):
                        pivots.append((k - 2, h1[k - 2].high if direction == 1 else h1[k - 2].low))
                        if len(pivots) >= 2 and abs(pivots[-1][1] - pivots[-2][1]) <= 0.1 * h1_atr[k]:
                            levels.append(("equal_sweep", -direction, pivots[-1][1], index + 1, index + 96))
            a, b, c = h1[k - 2:k + 1]
            for direction in (1, -1):
                lower, upper = (a.high, c.low) if direction == 1 else (c.high, a.low)
                if lower < upper and direction * (b.close - b.open) > 0:
                    zones.append(dict(kind="fvg", d=direction, lo=lower, hi=upper,
                                      born=index + 1, until=index + 96))
                pivots = highs if direction == 1 else lows
                if not pivots:
                    continue
                level = pivots[-1][1]
                if direction * (last.close - level) > 0 and direction * (h1[k - 1].close - level) <= 0:
                    same = (lows if direction == 1 else highs)
                    trend = len(pivots) >= 2 and len(same) >= 2 and direction * (pivots[-1][1] - pivots[-2][1]) > 0 \
                        and direction * (same[-1][1] - same[-2][1]) > 0
                    reversal = len(pivots) >= 2 and direction * (pivots[-1][1] - pivots[-2][1]) < 0
                    pad = 0.2 * h1_atr[k]
                    kind = "bos_retest" if trend else "choch_retest" if reversal else None
                    if kind:
                        zones.append(dict(kind=kind, d=direction, lo=level - pad, hi=level + pad,
                                          born=index + 1, until=index + 96))
                    for back in range(k - 1, max(pivots[-1][0], k - 10) - 1, -1):
                        candle = h1[back]
                        if direction * (candle.close - candle.open) < 0:
                            zones.append(dict(kind="order_block", d=direction, lo=candle.low,
                                              hi=candle.high, born=index + 1, until=index + 192))
                            break
        utc = bar.time % 86400
        day = bar.time // 86400
        if utc == 7 * 3600 - 900:
            asia = rows[index - 27:index + 1]
            levels += [("asia_sweep", 1, min(r.low for r in asia), index + 1, index + 36),
                       ("asia_sweep", -1, max(r.high for r in asia), index + 1, index + 36)]
        if utc == 0 and day - 1 in day_hl:
            high, low = day_hl[day - 1]
            levels += [("pdh_pdl_sweep", 1, low, index, index + 96),
                       ("pdh_pdl_sweep", -1, high, index, index + 96)]
        box = rows[index - 16:index]
        top, bottom = max(r.high for r in box), min(r.low for r in box)
        signals = []
        if rng.random() < 0.02:
            d_rand = 1 if bar.close >= bar.open else -1
            signals.append(("random_ctrl", d_rand, bar.low if d_rand == 1 else bar.high))
        if top - bottom <= 2.0 * m15_atr[index - 1]:
            pad = 0.05 * m15_atr[index - 1]
            if bar.low < bottom - pad and bar.close > bottom and bar.close > m15_ema[index]:
                signals.append(("squeeze_sweep", 1, bar.low))
            if bar.high > top + pad and bar.close < top and bar.close < m15_ema[index]:
                signals.append(("squeeze_sweep", -1, bar.high))
        alive = []
        for zone in zones:
            direction, lower, upper = zone["d"], zone["lo"], zone["hi"]
            if index > zone["until"]:
                continue
            if index < zone["born"]:
                alive.append(zone)
                continue
            if direction * (bar.close - (lower if direction == 1 else upper)) < 0:
                if zone["kind"] == "order_block":
                    alive.append(dict(kind="breaker", d=-direction, lo=lower, hi=upper,
                                      born=index + 1, until=index + 192))
                continue
            touched = bar.low <= upper and bar.high >= lower
            if touched and direction * (bar.close - bar.open) > 0 and lower <= bar.close <= upper \
                    or touched and direction * (bar.close - (upper if direction == 1 else lower)) > 0 \
                    and direction * (bar.close - bar.open) > 0:
                signals.append((zone["kind"], direction, min(lower, bar.low) if direction == 1 else max(upper, bar.high)))
                continue  # one trigger per zone
            alive.append(zone)
        zones = alive[-60:]
        kept = []
        for kind, direction, level, born, until in levels:
            if index > until:
                continue
            if index < born:
                kept.append((kind, direction, level, born, until))
                continue
            if (bar.low < level < bar.close if direction == 1 else bar.close < level < bar.high) \
                    and direction * (bar.close - bar.open) > 0:
                signals.append((kind, direction, bar.low if direction == 1 else bar.high))
                continue
            kept.append((kind, direction, level, born, until))
        levels = kept[-60:]
        for kind, direction, extreme in signals:
            aligned = h4_index is not None and h4_index >= 50 and direction * (h4[h4_index].close - h4_ema[h4_index]) > 0
            wick_stop = extreme - direction * 0.1 * m15_atr[index]
            swing_stop = (h1_swing_stop(bar.close, direction, h1[max(0, h1_index - 4):h1_index + 1],
                                        h1_atr[h1_index]) if h1_index is not None and h1_index >= 4 else None)
            for stop_mode, stop in (("wick", wick_stop), ("h1_swing", swing_stop)):
                if stop is None or direction * (bar.close - stop) <= 0:
                    continue
                for gate in ("all", "h4"):
                    if gate == "h4" and not aligned or busy.get((kind, gate, stop_mode), -1) >= index + 1:
                        continue
                    if abs(rows[index + 1].open - stop) < 0.0015 * rows[index + 1].open:
                        continue  # ponytail: fees eat >~13% R below 0.15% stop distance
                    results = {target: trade(rows, index + 1, direction, stop, target) for target in TARGETS}
                    if results[2.0] is None:
                        continue
                    busy[kind, gate, stop_mode] = results[2.0][2]
                    yield kind, gate, stop_mode, bar.time, "long" if direction == 1 else "short", \
                        {target: result[0] for target, result in results.items()}, (index + 1, direction, stop)

def stats(values):
    wins = [value for value in values if value > 0]
    loss = -sum(value for value in values if value <= 0)
    return dict(n=len(values), win=len(wins) / len(values) if values else None,
                mean_r=sum(values) / len(values) if values else None,
                pf=sum(wins) / loss if loss else None, total_r=sum(values))

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history", required=True)
    parser.add_argument("--recent")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    data = load(args.history, args.recent)
    first, last = min(r[0].time for r in data.values()), max(r[-1].time for r in data.values())
    split = first + int((last - first) * 0.7)
    groups = defaultdict(list)
    for symbol, rows in data.items():
        for kind, gate, stop_mode, stamp, side, results, _ in replay(rows):
            part = "train" if stamp < split else "test"
            for target, value in results.items():
                groups[kind, gate, stop_mode, target, part].append(value)
                groups[kind, gate, stop_mode, target, part + ":" + symbol].append(value)
    report = {"|".join(map(str, key)): stats(values) for key, values in sorted(groups.items())}
    report["meta"] = dict(first=first, last=last, split=split, symbols=sorted(data))
    Path(args.output).write_text(json.dumps(report, indent=2))
    print("split", split, "last", last)
    print(f"{'setup':14} {'gate':4} {'stop':8} {'R':>2} | {'train n':>6} {'win':>5} {'meanR':>6} {'pf':>5} | {'test n':>6} {'win':>5} {'meanR':>6} {'pf':>5}")
    for stop_mode in STOPS:
        print("-" * 86)
        for kind in SETUPS:
            for gate in ("all", "h4"):
                a, b = (stats(groups[kind, gate, stop_mode, 2.0, part]) for part in ("train", "test"))
                if a["n"] or b["n"]:
                    cell = lambda s: f"{s['n']:6} {s['win'] or 0:5.1%} {s['mean_r'] or 0:+6.2f} {s['pf'] or 0:5.2f}"
                    print(f"{kind:14} {gate:4} {stop_mode:8}  2 | {cell(a)} | {cell(b)}")

if __name__ == "__main__":
    main()
