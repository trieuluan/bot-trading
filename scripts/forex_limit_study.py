"""Replay: vào Market (live) so với Limit hồi về sau trigger M15. Dùng đúng code live, không ghi DB/ledger.

    python scripts/forex_limit_study.py [SYMBOL ...]
Limit: buy limit dưới / sell limit trên giá đóng nến trigger; fill khi giá chạm trong N nến M15 kế tiếp,
gap mở cửa tốt hơn thì khớp tại giá mở; không khớp thì bỏ. SL/TP giữ nguyên từ signal.
"""
import json
import statistics
import sys
from bisect import bisect_right
from collections import Counter
from copy import copy
from multiprocessing import Pool
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.forex_persist_study import DATA, ROOT, SLIPPAGE_BPS, WARMUP, condition_for, load_cache  # noqa: E402

# tên: (kiểu, hệ số, số nến chờ). atr = ATR H1 của setup; c50 = 50% biên độ nến trigger.
LIMITS = {"M_market": None, "L_atr25_n2": ("atr", .25, 2), "L_atr25_n4": ("atr", .25, 4),
          "L_atr50_n4": ("atr", .5, 4), "L_atr100_n8": ("atr", 1.0, 8), "L_c50_n4": ("candle", .5, 4)}

def run(symbol):
    from backend.app import forex_features, forex_htf_v2 as v
    from backend.app.forex_samples import advance_learning_sample, attach_execution, context
    rows, specs = load_cache()
    frames = rows[symbol]
    ask15 = {r.time: r for r in frames[("15m", "ask")]}
    bids = [r for r in frames[("15m", "bid")] if r.time in ask15]
    h1, h4 = frames[("1h", "bid")], frames[("4h", "bid")]
    h1t, h4t = [r.time for r in h1], [r.time for r in h4]
    reference = forex_features.SMT_REFERENCES.get(symbol)
    ref_h1 = rows[reference][("1h", "bid")] if reference else []
    reft = [r.time for r in ref_h1]
    trees, used, out = v.g0_trees(symbol), set(), []
    start = bids[0].time + WARMUP
    for i, candle in enumerate(bids):
        if candle.time < start or i < 100:
            continue
        a, b = bisect_right(h1t, candle.time - 2700), bisect_right(h4t, candle.time - 13500)
        hours, higher = h1[max(0, a - 300):a], h4[max(0, b - 80):b]
        history = bids[max(0, i - 200):i]
        ask = ask15[candle.time]
        market = {"minutes": [*history, candle], "asks": {r.time: ask15[r.time] for r in history[-30:]} | {candle.time: ask}}
        if reference:
            c = bisect_right(reft, candle.time - 2700)
            market["reference_hours"] = ref_h1[max(0, c - 40):c]
        condition = condition_for(symbol, specs, rows, candle.time + 900)
        for tree in trees:
            signal, _ = v.diagnose(tree, candle, hours, history, higher=higher, ask=ask, slippage_bps=SLIPPAGE_BPS,
                                   commission=condition["commission_per_unit"], symbol=symbol, market=market)
            if signal is None:
                continue
            key = (signal["strategy"], signal["side"], signal["htf_structure"]["setup_time"])
            if key in used:
                continue
            used.add(key)
            signal["symbol"] = symbol
            long = signal["side"] == "long"
            atr = signal["htf_structure"]["atr"]
            rec = {"symbol": symbol, "time": candle.time, "h1": tree["root"]["children"][1]["family"], "res": {},
                   "body_atr": abs(candle.close - candle.open) / atr, "range_atr": (candle.high - candle.low) / atr,
                   "close_pos": ((candle.close - candle.low) if long else (candle.high - candle.close)) / max(candle.high - candle.low, 1e-12),
                   "side": signal["side"], "m15": tree["root"]["children"][2]["family"],
                   "mom3": (candle.close - bids[i - 3].close) * (1 if long else -1) / atr}
            for name, spec in LIMITS.items():
                sample = {"symbol": symbol, "strategy": signal["strategy"], "tree": tree, "signal": dict(signal),
                          "side": signal["side"], "stop": signal["stop"], "confirm_time": candle.time,
                          "last_candle": candle.time, "status": "pending"}
                attach_execution(sample, [*history[-59:], candle], hours, higher, context(candle, hours, ask), candle, condition)
                if sample["status"] == "excluded":
                    rec["res"][name] = None
                    continue
                first = i + 1
                if spec:
                    kind, k, wait = spec
                    dist = k * atr if kind == "atr" else k * (candle.high - candle.low)
                    level = candle.close - dist if long else candle.close + dist
                    fill = None
                    for j in range(i + 1, min(i + 1 + wait, len(bids))):
                        bar, aj = bids[j], ask15[bids[j].time]
                        if bar.time != bids[j - 1].time + 900:
                            break
                        if (long and aj.low <= level) or (not long and bar.high >= level):
                            fill = j
                            break
                    if fill is None:
                        rec["res"][name] = "unfilled"
                        continue
                    first = fill
                    sample["execution"]["last_candle"] = sample["last_candle"] = bids[fill - 1].time
                for j in range(first, len(bids)):
                    if sample["status"] not in ("pending", "open"):
                        break
                    bar, aj = bids[j], ask15[bids[j].time]
                    if spec and j == first:  # entry bar: mở cửa tại limit (hoặc gap tốt hơn)
                        bar, aj = copy(bar), copy(aj)
                        spread = aj.open - bar.open
                        if long:
                            aj.open = min(aj.open, level)
                            bar.open = aj.open - spread
                        else:
                            bar.open = max(bar.open, level)
                            aj.open = bar.open + spread
                    e = bisect_right(h1t, bar.time - 2700)
                    advance_learning_sample(sample, bar, aj, bids[max(0, j - 59):j + 1], SLIPPAGE_BPS,
                                            condition["commission_per_unit"], hours=h1[max(0, e - 200):e])
                rec["res"][name] = sample.get("net_r_ex_swap") if sample["status"] == "resolved" else sample["status"]
            out.append(rec)
    return out

def stat(xs):
    return {"n": len(xs), "mean_r": round(statistics.fmean(xs), 3) if xs else None,
            "win": round(sum(x > 0 for x in xs) / len(xs), 3) if xs else None, "sum_r": round(sum(xs), 2)}

def summarize(records):
    num = lambda x: isinstance(x, (int, float))  # noqa: E731
    base = [r for r in records if num(r["res"].get("M_market"))]
    out = {"signals_with_market_result": len(base), "variants": {}}
    for name in LIMITS:
        filled = [r for r in base if num(r["res"].get(name))]
        missed = [r for r in base if r["res"].get(name) == "unfilled"]
        mid = sorted(r["time"] for r in base)[len(base) // 2]
        out["variants"][name] = {
            "fill_rate": round(len(filled) / len(base), 3),
            "limit_filled": stat([r["res"][name] for r in filled]),
            "market_same_signals": stat([r["res"]["M_market"] for r in filled]),
            "market_on_missed": stat([r["res"]["M_market"] for r in missed]),
            "sum_r_all_signals": round(sum(r["res"][name] for r in filled), 2),
            "first_half": stat([r["res"][name] for r in filled if r["time"] < mid]),
            "second_half": stat([r["res"][name] for r in filled if r["time"] >= mid]),
            "by_symbol": {s: stat([r["res"][name] for r in filled if r["symbol"] == s]) for s in sorted({r["symbol"] for r in filled})},
        }
    out["market_all_sum_r"] = round(sum(r["res"]["M_market"] for r in base), 2)
    return out

if __name__ == "__main__":
    symbols = sys.argv[1:] or list(json.load(open(DATA / "forex_specs.json")))
    with Pool() as pool:
        records = [r for part in pool.map(run, symbols) for r in part]
    report = summarize(records)
    (ROOT / "research-output/forex-limit-study.json").write_text(json.dumps({**report, "records": records}, indent=1))
    print("market tổng:", report["market_all_sum_r"], "| tín hiệu:", report["signals_with_market_result"])
    for name, v in report["variants"].items():
        print(f"{name:12} fill {v['fill_rate']:.0%} | limit {v['limit_filled']} | market cùng lệnh {v['market_same_signals']} | market lệnh lỡ {v['market_on_missed']} | halves {v['first_half']['mean_r']}/{v['second_half']['mean_r']}")
