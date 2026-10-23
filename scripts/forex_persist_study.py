"""Replay nới `no_h1_setup` trên nến VPS: bao nhiêu tín hiệu vào lệnh và R sau chi phí (chưa swap).

Chạy đúng code live (diagnose + attach_execution + advance_learning_sample); không ghi ledger/DB.
    python scripts/forex_relax_study.py [SYMBOL ...]
"""
import gzip
import json
import statistics
import sys
from bisect import bisect_right
from collections import Counter, defaultdict
from multiprocessing import Pool
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
DATA = ROOT / "research-output/candles"
WARMUP = 9 * 86400
SLIPPAGE_BPS = 1.0  # giống ASSUMED_SLIPPAGE_BPS của live cho cặp chưa đo
VARIANTS = {"A_persist0": 0, "B_persist3": 3}  # SETUP_PERSIST_H1 của code live (B = mặc định live)

def load():
    rows = defaultdict(lambda: defaultdict(list))
    reading = False
    with gzip.open(DATA / "workspace_candles.sql.gz", "rt") as handle:
        for line in handle:
            if not reading:
                reading = line.startswith("COPY public.workspace_candles")
                continue
            if line.startswith("\\."):
                break
            f = line.rstrip("\n").split("\t")
            rows[f[1]][(f[2], f[3])].append(SimpleNamespace(
                time=int(f[4]), open=float(f[5]), high=float(f[6]), low=float(f[7]), close=float(f[8]),
                price_type=f[3], tick_volume=None if f[9] == "\\N" else int(f[9])))
    for frames in rows.values():
        for series in frames.values():
            series.sort(key=lambda row: row.time)
    return rows

def condition_for(symbol, specs, rows, stamp):
    """Quy đổi quote->USD như live: lỗ dùng bid/ask bất lợi, lãi dùng phía có lợi."""
    spec = dict(specs[symbol])
    quote = spec["quote_currency"]
    pairs = {"USD": None, "JPY": ("USDJPY", True), "CAD": ("USDCAD", True), "CHF": ("USDCHF", True),
             "EUR": ("EURUSD", False), "GBP": ("GBPUSD", False), "AUD": ("AUDUSD", False), "NZD": ("NZDUSD", False)}
    loss = gain = 1.0
    if pairs[quote]:
        pair, divide = pairs[quote]
        bid = next(r for r in reversed(rows[pair][("15m", "bid")]) if r.time <= stamp).close
        ask = next(r for r in reversed(rows[pair][("15m", "ask")]) if r.time <= stamp).close
        loss, gain = (1 / bid, 1 / ask) if divide else (ask, bid)
    spec.update(quote_to_account_loss=str(loss), quote_to_account_gain=str(gain),
                commission_per_unit=float(spec["commission_account_per_unit"]) / gain)
    return spec

def run(args):
    symbol, name = args
    from backend.app import forex_features, forex_htf_v2 as v
    from backend.app.forex_samples import advance_learning_sample, attach_execution, context
    persist = VARIANTS[name]
    rows, specs = load_cache()
    frames = rows[symbol]
    bid15 = frames[("15m", "bid")]
    ask15 = {r.time: r for r in frames[("15m", "ask")]}
    bids = [r for r in bid15 if r.time in ask15]
    h1 = frames[("1h", "bid")]
    h4 = frames[("4h", "bid")]
    h1t, h4t = [r.time for r in h1], [r.time for r in h4]
    reference = forex_features.SMT_REFERENCES.get(symbol)
    ref_h1 = rows[reference][("1h", "bid")] if reference else []
    reft = [r.time for r in ref_h1]
    v.SETUP_PERSIST_H1 = persist
    trees = v.g0_trees(symbol)
    used, samples, rejected = set(), [], Counter()
    start = bids[0].time + WARMUP
    for i, candle in enumerate(bids):
        if candle.time < start or i < 100:
            continue
        a = bisect_right(h1t, candle.time - 2700)
        b = bisect_right(h4t, candle.time - 13500)
        hours, higher = h1[max(0, a - 300):a], h4[max(0, b - 80):b]
        history = bids[max(0, i - 200):i]
        history96 = history[-96:]
        ask = ask15[candle.time]
        market = {"minutes": [*history, candle], "asks": {r.time: ask15[r.time] for r in history[-30:]} | {candle.time: ask}}
        if reference:
            c = bisect_right(reft, candle.time - 2700)
            market["reference_hours"] = ref_h1[max(0, c - 40):c]
        condition = condition_for(symbol, specs, rows, candle.time + 900)
        for tree in trees:
            signal, reason = v.diagnose(tree, candle, hours, history, higher=higher, ask=ask,
                                        slippage_bps=SLIPPAGE_BPS, commission=condition["commission_per_unit"],
                                        symbol=symbol, market=market)
            if signal is None:
                rejected[reason] += 1
                continue
            key = (signal["strategy"], signal["side"], signal["htf_structure"]["setup_time"])
            if key in used:
                rejected["setup_reused"] += 1
                continue
            used.add(key)
            closed_all = v._closed_rows(candle, hours, 3600, 50, symbol)
            closed_hi = [r for r in higher if r.time + 14400 <= candle.time + 900]
            feats = {}
            for fname in ("adx_regime", "price_zone", "adr_headroom", "spread_guard", "liquid_session"):
                try:
                    why, _ = forex_features.filter_result(v._filter(fname), candle, closed_all, closed_hi, signal["side"], symbol, market)
                except Exception:
                    why = "error"
                feats[fname] = why is None
            signal["symbol"] = symbol
            sample = {"symbol": symbol, "strategy": signal["strategy"], "tree": tree, "signal": signal,
                      "side": signal["side"], "stop": signal["stop"], "confirm_time": candle.time,
                      "last_candle": candle.time, "status": "pending"}
            attach_execution(sample, [*history96[-59:], candle], hours, higher, context(candle, hours, ask), candle, condition)
            for j in range(i + 1, len(bids)):
                if sample["status"] not in ("pending", "open"):
                    break
                nxt = bids[j]
                e = bisect_right(h1t, nxt.time - 2700)
                advance_learning_sample(sample, nxt, ask15[nxt.time], bids[max(0, j - 59):j + 1],
                                        SLIPPAGE_BPS, condition["commission_per_unit"], hours=h1[max(0, e - 200):e])
            samples.append({"symbol": symbol, "tree": signal["strategy"], "side": signal["side"],
                            "h1": tree["root"]["children"][1]["family"], "m15": tree["root"]["children"][2]["family"],
                            "policy": tree["root"]["children"][3]["policy"], "filters": [f["name"] for f in tree["root"]["children"][4:]],
                            "time": candle.time, "hour": (candle.time + 900) // 3600 % 24,
                            "age_h": (candle.time - signal["htf_structure"]["setup_time"]) / 3600,
                            "target_r": signal["target_r"], "feat": feats,
                            "risk_atr": abs(candle.close - signal["stop"]) / signal["htf_structure"]["atr"], "setup_time": signal["htf_structure"]["setup_time"],
                            "status": sample["status"], "r": sample.get("net_r_ex_swap"),
                            "reason": (sample.get("execution_result") or {}).get("reason") or sample.get("reason")})
    return {"symbol": symbol, "variant": name, "samples": samples, "rejected": dict(rejected)}

_cache = None
def load_cache():
    global _cache
    if _cache is None:
        _cache = (load(), json.load(open(DATA / "forex_specs.json")))
    return _cache

def summarize(results):
    out = {}
    for name in VARIANTS:
        samples = [s for r in results if r["variant"] == name for s in r["samples"]]
        resolved = [s for s in samples if s["status"] == "resolved"]
        values = [s["r"] for s in resolved]
        mid = sorted(s["time"] for s in samples)[len(samples) // 2] if samples else 0
        def stat(items):
            xs = [s["r"] for s in items]
            return {"n": len(xs), "mean_r": round(statistics.fmean(xs), 3) if xs else None,
                    "win": round(sum(x > 0 for x in xs) / len(xs), 3) if xs else None, "sum_r": round(sum(xs), 2)}
        setups = {}
        for s in resolved:
            setups.setdefault((s["symbol"], s["side"], s["setup_time"], s["h1"]), []).append(s["r"])
        by_family = {}
        for family in sorted({s["h1"] for s in samples}):
            by_family[family] = {"signals": sum(s["h1"] == family for s in samples), **stat([s for s in resolved if s["h1"] == family])}
        out[name] = {"samples": samples,
            "signals": len(samples), "status": dict(Counter(s["status"] for s in samples)),
            "skipped_reasons": dict(Counter(s["reason"] for s in samples if s["status"] not in ("resolved", "open", "pending"))),
            "all": stat(resolved), "first_half": stat([s for s in resolved if s["time"] < mid]),
            "second_half": stat([s for s in resolved if s["time"] >= mid]),
            "unique_setups": len(setups), "unique_setup_mean_r": round(statistics.fmean(statistics.fmean(v) for v in setups.values()), 3) if setups else None,
            "by_family": by_family, "by_symbol": {sym: stat([s for s in resolved if s["symbol"] == sym]) for sym in sorted({s["symbol"] for s in samples})},
            "by_policy": {p: stat([s for s in resolved if s["policy"] == p]) for p in sorted({s["policy"] for s in resolved})},
            "rejected": dict(sum((Counter(r["rejected"]) for r in results if r["variant"] == name), Counter()).most_common(8)),
        }
    return out

if __name__ == "__main__":
    all_symbols = [s for s in json.load(open(DATA / "forex_specs.json"))]
    symbols = sys.argv[1:] or all_symbols
    jobs = [(s, n) for n in VARIANTS for s in symbols]
    with Pool() as pool:
        results = pool.map(run, jobs)
    report = summarize(results)
    (ROOT / "research-output/forex-persist-study.json").write_text(json.dumps(report, indent=1))
    for name, r in report.items():
        print(name, "signals", r["signals"], r["status"], "| resolved", r["all"], "| setups", r["unique_setups"], r["unique_setup_mean_r"])
        print("   half1", r["first_half"], "half2", r["second_half"])
