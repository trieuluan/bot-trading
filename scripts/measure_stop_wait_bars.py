"""Đo lường tác động của wait_bars (4, 6, 8 nến) cho Stop xác nhận đóng nến M15 trên 11 cặp tiền."""

import copy
import json
import math
import statistics
import sys
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.forex_persist_study import SLIPPAGE_BPS, WARMUP, condition_for, load_cache
from backend.app import forex_features, forex_htf_v2
from backend.app.forex_entry_policy import make_v4_g0_bundle
from backend.app.forex_entry_replay import run as production_run

WAIT_OPTIONS = [2, 4, 6, 8]


def closed_stop_replay(signal, bids, asks, minutes, hours, condition, costs, now, wait_bars):
    available = signal["available_at"]
    setup_time = signal["htf_structure"]["setup_time"]
    setup_deadline = setup_time + 14400 + 900
    deadline = min(available + wait_bars * 900, setup_deadline)
    direction = 1 if signal["side"] == "long" else -1
    stop_buffer = 0.05 * signal["htf_structure"]["atr"]
    trigger_candle = next(bar for bar in bids if bar.time == signal["candle_time"])
    stop_price = (trigger_candle.high if direction == 1 else trigger_candle.low) + direction * stop_buffer

    ask_map = {bar.time: bar for bar in asks if bar.time + 900 <= now}
    previous = available - 900

    def invalidated(stamp):
        return any(available < hour.time + 3600 <= stamp and
                   direction * (hour.close - signal["htf_structure"]["invalidation"]) <= 0 for hour in hours)

    for bid in sorted((b for b in bids if available <= b.time and b.time + 900 <= now), key=lambda b: b.time):
        if bid.time >= deadline:
            break
        ask = ask_map.get(bid.time)
        if bid.time != previous + 900 or ask is None:
            return {"status": "excluded", "reason": "replay_data_gap"}
        previous = bid.time
        entry_time = bid.time + 900
        if invalidated(bid.time) or invalidated(entry_time):
            return {"status": "excluded", "reason": "setup_invalidated"}
        # Stop xác nhận đóng nến M15: đóng nến vượt stop_price
        if direction * (bid.close - stop_price) < 0:
            continue
        # Xác nhận thành công -> vào lệnh tại mở nến tiếp theo
        if entry_time >= setup_deadline:
            return {"status": "expired", "reason": "setup_expired"}
        following = [b for b in bids if b.time >= entry_time]
        if not following or following[0].time != entry_time:
            return {"status": "excluded", "reason": "replay_data_gap"}
        delegated = copy.deepcopy(signal)
        delegated["available_at"] = entry_time
        delegated["entry_policy"] = {
            "policy": "market", "available_at": entry_time,
            "expires_at": min(entry_time + 120, setup_deadline),
        }
        return production_run(delegated, following, asks, minutes, hours, condition, costs, now, duration=900)

    covered = previous + 900 >= deadline
    if now >= deadline and not covered:
        return {"status": "excluded", "reason": "replay_data_gap"}
    return {"status": "expired" if now >= deadline and covered else "pending", "reason": "stop_close_not_confirmed"}


def measure_symbol(symbol):
    rows, specs = load_cache()
    frames = rows[symbol]
    ask15 = {r.time: r for r in frames[("15m", "ask")]}
    bids = [r for r in frames[("15m", "bid")] if r.time in ask15]
    h1 = frames[("1h", "bid")]
    h4 = frames[("4h", "bid")]
    h1t, h4t = [r.time for r in h1], [r.time for r in h4]
    reference = forex_features.SMT_REFERENCES.get(symbol)
    ref_h1 = rows[reference][("1h", "bid")] if reference else []
    reft = [r.time for r in ref_h1]

    # Dùng 14 bundle V4 G0
    pop, bundles = make_v4_g0_bundle(symbol)
    trees = [b["base"] for b in bundles.values()]
    now = bids[-1].time + 900
    start = bids[0].time + WARMUP
    from bisect import bisect_right

    signals = []
    used = set()
    for i, candle in enumerate(bids):
        if candle.time < start or i < 100:
            continue
        a = bisect_right(h1t, candle.time - 2700)
        b = bisect_right(h4t, candle.time - 13500)
        hours = h1[max(0, a - 300):a]
        higher = h4[max(0, b - 80):b]
        history = bids[max(0, i - 200):i]
        ask = ask15[candle.time]
        market = {"minutes": [*history, candle], "asks": {r.time: ask15[r.time] for r in history[-30:]} | {candle.time: ask}}
        if reference:
            c = bisect_right(reft, candle.time - 2700)
            market["reference_hours"] = ref_h1[max(0, c - 40):c]
        condition = condition_for(symbol, specs, rows, candle.time + 900)
        commission = condition["commission_per_unit"]

        for tree in trees:
            sig, reason = forex_htf_v2.diagnose(
                tree, candle, hours, history, higher=higher, ask=ask,
                slippage_bps=SLIPPAGE_BPS, commission=commission, symbol=symbol, market=market,
            )
            if sig is None:
                continue
            key = (sig["strategy"], sig["side"], sig["htf_structure"]["setup_time"])
            if key in used:
                continue
            used.add(key)
            sig["symbol"] = symbol
            signals.append({
                "signal": sig, "candle": candle, "condition": condition,
                "costs": {"commission_per_unit": commission, "slippage_bps": SLIPPAGE_BPS},
            })

    results = {w: [] for w in WAIT_OPTIONS}
    for item in signals:
        sig = item["signal"]
        for w in WAIT_OPTIONS:
            res = closed_stop_replay(
                sig, bids, list(ask15.values()), bids, h1, item["condition"], item["costs"], now, wait_bars=w,
            )
            results[w].append(res)

    return symbol, len(signals), results


def main():
    specs = json.loads((Path("research-output/candles") / "forex_specs.json").read_text())
    symbols = list(specs.keys())
    print(f"Bắt đầu đo trên {len(symbols)} cặp: {symbols}...", flush=True)

    all_results = {w: [] for w in WAIT_OPTIONS}
    total_signals = 0

    with ProcessPoolExecutor(max_workers=4) as executor:
        futures = {executor.submit(measure_symbol, s): s for s in symbols}
        for f in as_completed(futures):
            sym, count, res = f.result()
            total_signals += count
            print(f"  {sym}: {count} signals hoàn thành.", flush=True)
            for w in WAIT_OPTIONS:
                all_results[w].extend(res[w])

    print("\n" + "=" * 80)
    print(f"KẾT QUẢ REPLAY ĐO CHỜ NẾN STOP (Tổng tín hiệu: {total_signals})")
    print("=" * 80)
    print(f"{'Wait bars':<12} | {'Filled':<8} | {'FillRate':<9} | {'WinRate':<9} | {'Total R':<10} | {'Mean R':<9} | {'Profit Factor':<14} | {'Expired':<8}")
    print("-" * 80)

    for w in WAIT_OPTIONS:
        records = all_results[w]
        filled = [r for r in records if r.get("status") in ("resolved", "open") and "net_r_ex_swap" in r]
        expired = sum(1 for r in records if r.get("status") == "expired")
        fill_rate = len(filled) / len(records) * 100 if records else 0
        wins = [r["net_r_ex_swap"] for r in filled if r["net_r_ex_swap"] > 0]
        losses = [abs(r["net_r_ex_swap"]) for r in filled if r["net_r_ex_swap"] < 0]
        win_rate = len(wins) / len(filled) * 100 if filled else 0
        total_r = sum(r["net_r_ex_swap"] for r in filled)
        mean_r = total_r / len(filled) if filled else 0
        pf = sum(wins) / sum(losses) if sum(losses) > 0 else (999.0 if sum(wins) > 0 else 0)

        print(f"{w} bars ({w*15:2d}m) | {len(filled):<8} | {fill_rate:6.1f}%  | {win_rate:6.1f}%  | {total_r:+9.2f}R | {mean_r:+7.3f}R | {pf:13.2f}  | {expired:<8}")
    print("=" * 80)


if __name__ == "__main__":
    main()
