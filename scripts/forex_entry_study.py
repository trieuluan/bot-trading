"""Replay kiểu vào lệnh: Market / Limit tại level setup / Buy-Sell Stop phá nến trigger / kết hợp.

Dùng đúng code live (diagnose + attach_execution + advance_learning_sample); SL/TP giữ nguyên từ signal,
R/R + chi phí được kiểm lại tại giá vào thật (poor_rr / sl_too_tight loại như live). Không ghi DB/ledger.
    python -m scripts.forex_entry_study [SYMBOL ...]
Quy ước khớp (nến M15 kế tiếp, tính cả bid/ask):
  limit : long khớp khi ask.low <= giá; short khi bid.high >= giá. Gap tốt hơn thì khớp tại giá mở.
  stop  : long khớp khi ask.high >= giá; short khi bid.low <= giá. Gap xấu hơn thì khớp tại giá mở.
  fallback "mkt": hết hạn chưa khớp thì vào Market ở nến mở kế tiếp (nếu SL chưa bị thủng).
"""
import json
import statistics
import sys
from bisect import bisect_right
from collections import defaultdict
from copy import copy
from multiprocessing import Pool
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.forex_persist_study import DATA, ROOT, SLIPPAGE_BPS, WARMUP, condition_for, load_cache  # noqa: E402

# tên: (kiểu, tham số, số nến chờ, fallback)
MODES = {
    "M_market": ("market", 0, 0, None),
    "L_level_n4": ("limit", 1.0, 4, None),
    "L_level_n8": ("limit", 1.0, 8, None),
    "L_mid_n4": ("limit", 0.5, 4, None),
    "L_level_n4+mkt": ("limit", 1.0, 4, "mkt"),
    "L_mid_n4+mkt": ("limit", 0.5, 4, "mkt"),
    "S_break_n2": ("stop", 0.05, 2, None),
    "S_break_n4": ("stop", 0.05, 4, None),
    "S_break_n4+mkt": ("stop", 0.05, 4, "mkt"),
    "L_mid_or_S_n4": ("both", 0.5, 4, None),  # chờ cái nào chạm trước: limit giữa close-level hoặc stop phá nến
}

def first_fill(kind, param, wait, long, candle, level, atr, bids, ask15, i):
    """Trả (chỉ số nến khớp, giá khớp lý tưởng) hoặc (None, None)."""
    if kind == "limit":
        price = candle.close - param * (candle.close - level) if long else candle.close + param * (level - candle.close)
    if kind in ("stop", "both"):
        brk = candle.high + 0.05 * atr if long else candle.low - 0.05 * atr
    if kind == "both":
        price = candle.close - param * (candle.close - level) if long else candle.close + param * (level - candle.close)
    for j in range(i + 1, min(i + 1 + wait, len(bids))):
        bar, aj = bids[j], ask15[bids[j].time]
        if bar.time != bids[j - 1].time + 900:
            return None, None
        if kind == "limit":
            if (long and aj.low <= price) or (not long and bar.high >= price):
                return j, (min(aj.open, price) if long else max(bar.open, price))
        elif kind == "stop":
            if (long and aj.high >= brk) or (not long and bar.low <= brk):
                return j, (max(aj.open, brk) if long else min(bar.open, brk))
        else:  # both: limit và stop cùng canh; cái chạm trước trong nến (open gần cái nào) thắng, hoà thì limit
            hit_l = (long and aj.low <= price) or (not long and bar.high >= price)
            hit_s = (long and aj.high >= brk) or (not long and bar.low <= brk)
            if hit_l:
                return j, (min(aj.open, price) if long else max(bar.open, price))
            if hit_s:
                return j, (max(aj.open, brk) if long else min(bar.open, brk))
    return None, None

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
            level = signal["htf_structure"]["level"]
            room = ((candle.close - level) if long else (level - candle.close)) / atr
            rec = {"symbol": symbol, "time": candle.time, "side": signal["side"], "room_atr": room,
                   "h1": tree["root"]["children"][1]["family"], "res": {}}
            for name, (kind, param, wait, fallback) in MODES.items():
                sample = {"symbol": symbol, "strategy": signal["strategy"], "tree": tree, "signal": dict(signal),
                          "side": signal["side"], "stop": signal["stop"], "confirm_time": candle.time,
                          "last_candle": candle.time, "status": "pending"}
                attach_execution(sample, [*history[-59:], candle], hours, higher, context(candle, hours, ask), candle, condition)
                if sample["status"] == "excluded":
                    rec["res"][name] = None
                    continue
                first, price = i + 1, None
                if kind != "market":
                    if kind in ("limit", "both") and room <= 0.05:  # level đã nằm sau giá: limit vô nghĩa -> coi như Market
                        pass
                    else:
                        j, price = first_fill(kind, param, wait, long, candle, level, atr, bids, ask15, i)
                        if j is not None:
                            first = j
                        elif fallback == "mkt":
                            first = i + 1 + wait
                            price = None
                            if first >= len(bids) or bids[first].time != bids[first - 1].time + 900:
                                rec["res"][name] = "unfilled"
                                continue
                        else:
                            rec["res"][name] = "unfilled"
                            continue
                    if first > i + 1:
                        sample["execution"]["last_candle"] = sample["last_candle"] = bids[first - 1].time
                for j in range(first, len(bids)):
                    if sample["status"] not in ("pending", "open"):
                        break
                    bar, aj = bids[j], ask15[bids[j].time]
                    if price is not None and j == first:
                        bar, aj = copy(bar), copy(aj)
                        spread = aj.open - bar.open
                        bar.open, aj.open = price - (spread if long else 0), price + (0 if long else spread)
                        if long:
                            bar.open, aj.open = price - spread, price
                        else:
                            bar.open, aj.open = price, price + spread
                        # giữ OHLC hợp lệ
                        bar.high, bar.low = max(bar.high, bar.open), min(bar.low, bar.open)
                        aj.high, aj.low = max(aj.high, aj.open), min(aj.low, aj.open)
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
    base = sorted((r for r in records if num(r["res"].get("M_market"))), key=lambda r: r["time"])
    chunks = [base[k * len(base) // 4:(k + 1) * len(base) // 4] for k in range(4)]
    out = {"signals": len(base), "market_sum_r": round(sum(r["res"]["M_market"] for r in base), 2),
           "room_atr_median": round(statistics.median(r["room_atr"] for r in base), 2), "modes": {}}
    for name in MODES:
        filled = [r for r in base if num(r["res"].get(name))]
        missed = [r for r in base if r["res"].get(name) == "unfilled"]
        other = [r for r in base if not num(r["res"].get(name)) and r["res"].get(name) != "unfilled"]
        quarters = [round(sum(r["res"][name] for r in ch if num(r["res"].get(name))), 1) for ch in chunks]
        out["modes"][name] = {
            "fill_rate": round(len(filled) / len(base), 3), "filled": stat([r["res"][name] for r in filled]),
            "market_same": stat([r["res"]["M_market"] for r in filled]),
            "market_on_missed": stat([r["res"]["M_market"] for r in missed]),
            "dropped_by_recheck": len(other), "sum_r_by_quarter": quarters,
            "market_by_quarter": [round(sum(r["res"]["M_market"] for r in ch), 1) for ch in chunks],
            "by_h1": {h: stat([r["res"][name] for r in filled if r["h1"] == h]) for h in sorted({r["h1"] for r in filled})},
        }
    return out

if __name__ == "__main__":
    symbols = sys.argv[1:] or list(json.load(open(DATA / "forex_specs.json")))
    with Pool() as pool:
        records = [r for part in pool.map(run, symbols) for r in part]
    report = summarize(records)
    (ROOT / "research-output/forex-entry-study.json").write_text(json.dumps({**report, "records": records}, indent=1))
    print("tín hiệu", report["signals"], "| market tổng", report["market_sum_r"], "| room median (ATR)", report["room_atr_median"])
    for name, m in report["modes"].items():
        print(f"{name:16} fill {m['fill_rate']:.0%} | {m['filled']['mean_r']}/{m['filled']['sum_r']}R n={m['filled']['n']}"
              f" | market cùng lệnh {m['market_same']['mean_r']}/{m['market_same']['sum_r']} | market lệnh lỡ {m['market_on_missed']['mean_r']}/{m['market_on_missed']['sum_r']}"
              f" | recheck loại {m['dropped_by_recheck']} | quý {m['sum_r_by_quarter']} vs market {m['market_by_quarter']}")
