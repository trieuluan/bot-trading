"""MTF entry timing + realistic cross-margin portfolio replay on $100/$300/$1000.

Run: /tmp/btv/bin/python -m scripts.mtf_precision_replay
Reused history: exploratory, not proof.
ponytail: full cross-margin mark-to-market liquidation; uniform linear maintenance margin.
"""

import bisect
import json
import math
from collections import defaultdict
from pathlib import Path

from scripts import fee_edge_replay as edge
from scripts import htf_portfolio_replay as native
from scripts import small_capital_replay as sc
from scripts.price_action_replay import atr, bucket, ema

SPECS_PATH = Path("research-output/mexc-contract-specs-20261108.json")
OUTPUT = Path("research-output/mtf-cross-margin-replay-20261108.json")


def h1_levels(m15_rows):
    h1 = bucket(m15_rows, 3600)
    closes_at = [b.time + 3600 for b in h1]
    highs, lows = [b.high for b in h1], [b.low for b in h1]
    levels = []
    for k in range(24, len(h1)):
        levels.append((closes_at[k], max(highs[k - 24:k]), min(lows[k - 24:k])))
    return levels


def mtf_refined_signals(symbol, rows, wait_bars=8):
    """Seed H4/D1 signal, wait up to wait_bars M15 candles for dip + reclaim."""
    for s in native.seed_signals(symbol, rows):
        idx, d, stop = s["entry_idx"], s["direction"], s["stop"]
        for j in range(idx, min(idx + wait_bars, len(rows) - 1)):
            bar = rows[j]
            if (d == 1 and bar.low <= stop) or (d == -1 and bar.high >= stop):
                break
            bull = d * (bar.close - bar.open) > 0
            dip = (bar.low < bar.open if d == 1 else bar.high > bar.open) if j == idx else \
                  (bar.close > rows[j - 1].high if d == 1 else bar.close < rows[j - 1].low)
            if bull and dip:
                yield {**s, "seed": f"{s['seed']}_m15_refined", "entry_idx": j + 1,
                       "entry_time": bar.time + 900, "max_m15_bars": s["max_m15_bars"] - (j + 1 - idx)}
                break


def cross_margin_account(trades, data, specs, capital=100.0, risk_pct=0.02,
                         leverage=10, max_open=3):
    """Cross-margin portfolio replay on 15m intervals with real contract sizing."""
    if capital <= 0 or not trades:
        return dict(final=capital, taken=0, liquidations=0, max_drawdown_pct=0.0,
                    loss_day_share=0.0, per_day=0.0, active_days=0, worst_30d=0.0)

    trades_by_entry = defaultdict(list)
    for t in sorted(trades, key=lambda r: r["entry_time"]):
        trades_by_entry[t["entry_time"]].append(t)

    first_entry = min(trades_by_entry)
    last_exit = max(t["exit_time"] for t in trades)

    # Fast row maps
    first_pack = next(iter(data.values()))
    timeline = [b.time for b in first_pack[2] if first_entry - 900 <= b.time <= last_exit + 900]
    row_maps = {sym: {b.time: b for b in pack[2]} for sym, pack in data.items()}

    cash = peak = capital
    worst = 0.0
    open_pos = []  # dict of position state
    taken = liquidations = skipped_min = skipped_margin = 0
    daily = defaultdict(float)

    for stamp in timeline:
        # 1. Close positions scheduled to exit at or before stamp
        remaining = []
        for p in open_pos:
            t = p["trade"]
            if t["exit_time"] <= stamp:
                # Realize PnL
                pnl = p["qty"] * t["direction"] * (t["exit_fill"] - t["entry"]) - \
                      p["qty"] * (t["entry"] + t["exit_fill"]) * t["fee"] - \
                      p["qty"] * t["funding_per_unit"]
                cash += pnl
                daily[stamp // 86400] += pnl
            else:
                remaining.append(p)
        open_pos = remaining

        # 2. Mark-to-market equity & cross-margin liquidation check
        unrealized = sum(
            p["qty"] * p["trade"]["direction"] * (row_maps[p["trade"]["symbol"]][stamp].close - p["trade"]["entry"])
            for p in open_pos
        )
        total_equity = cash + unrealized
        peak = max(peak, total_equity)
        if peak > 0:
            worst = max(worst, 1.0 - total_equity / peak)

        # Cross maintenance margin = sum(notional * MMR)
        mmr_req = sum(
            p["qty"] * row_maps[p["trade"]["symbol"]][stamp].close * specs[p["trade"]["symbol"]]["maintenanceMarginRate"]
            for p in open_pos
        )
        if open_pos and total_equity <= mmr_req:
            # Entire portfolio liquidated at mark
            liquidations += len(open_pos)
            cash = 0.0
            open_pos = []
            worst = 1.0
            break

        # 3. Enter new trades
        for t in trades_by_entry.get(stamp, []):
            if total_equity <= 0:
                continue
            sym = t["symbol"]
            spec = specs[sym]
            cs, min_vol = spec["contractSize"], spec["minVol"]
            pair_max_lev = spec["maxLeverage"]
            eff_lev = min(leverage, pair_max_lev)

            risk_dollars = total_equity * risk_pct
            target_qty = risk_dollars / t["risk"]
            contracts = max(min_vol, round(target_qty / cs))
            actual_qty = contracts * cs

            # Skip if min contract size forces > 2x risk
            if actual_qty * t["risk"] > 2.0 * risk_dollars:
                skipped_min += 1
                continue

            notional = actual_qty * t["entry"]
            imr_req = notional / eff_lev
            total_used = sum(p["notional"] / min(leverage, specs[p["trade"]["symbol"]]["maxLeverage"]) for p in open_pos)
            if len(open_pos) >= max_open or (imr_req + total_used) > total_equity:
                skipped_margin += 1
                continue

            open_pos.append(dict(trade=t, qty=actual_qty, notional=notional))
            taken += 1

    series, months = list(daily.values()), defaultdict(float)
    for d, v in daily.items():
        months[d // 30] += v
    days = max(1, (timeline[-1] - timeline[0]) / 86400) if timeline else 1
    return dict(final=round(max(0.0, cash), 2), per_day=round((cash - capital) / days, 3),
                taken=taken, liquidations=liquidations, skipped_min=skipped_min,
                skipped_margin=skipped_margin, max_drawdown_pct=round(100 * worst, 1),
                loss_day_share=round(sum(x < 0 for x in series) / max(1, sum(x != 0 for x in series)), 3),
                active_days=sum(x != 0 for x in series), worst_30d=round(min(months.values(), default=0), 2))


def check():
    assert h1_levels([]) == []
    dummy = [{"entry_time": 0, "exit_time": 900, "symbol": "BTC_USDT", "risk": 1000.0,
              "entry": 80000.0, "exit_fill": 80200.0, "direction": 1, "fee": 0.0008,
              "funding_per_unit": 0.0}]
    specs = {"BTC_USDT": {"contractSize": 0.0001, "minVol": 1, "maxLeverage": 500,
                          "maintenanceMarginRate": 0.001}}
    rows = [type("C", (), {"time": 0, "close": 80000.0})(),
            type("C", (), {"time": 900, "close": 80200.0})()]
    data = {"BTC_USDT": (0, 900, rows, rows, [])}
    res = cross_margin_account(dummy, data, specs, 100.0, 0.02, 10, 3)
    assert res["taken"] == 1 and res["final"] > 100.0
    print("self-checks passed", flush=True)


def main():
    check()
    data, first, end, _ = native.load_data()
    fees = json.loads(edge.FEES.read_text())["fees"]
    specs = json.loads(SPECS_PATH.read_text())
    folds = native.build_folds(first, end)

    grouped = defaultdict(list)
    for s, p in sorted(data.items()):
        for x in native.seed_signals(s, p[2]):
            grouped[x["seed"]].append(x)
        for x in mtf_refined_signals(s, p[2], 8):
            grouped[x["seed"]].append(x)

    arms = {
        "prod_d1_be2r": ("d1_squeeze", 2, "be", 1, "all"),
        "prod_h4_3r_session": ("h4_sweep", 3, "fixed", 1, "utc_12_20"),
        "refined_d1_m15_be2r": ("d1_squeeze_m15_refined", 2, "be", 1, "all"),
        "refined_h4_m15_3r_session": ("h4_sweep_m15_refined", 3, "fixed", 1, "utc_12_20"),
    }

    trades_map = {}
    for name, (seed, tp, mode, hold, gate) in arms.items():
        t = edge.replay(data, grouped[seed], fees, folds, tp, mode, hold, gate)
        trades_map[name] = t
        m = edge.metrics(t)
        st = edge.replay(data, grouped[seed], fees, folds, tp, mode, hold, gate, stress=2)
        sm = edge.metrics(st)
        fr = [round(edge.metrics([x for x in t if x["fold"] == f])["mean_r"] or 0, 3) for f, _, _ in folds]
        print(f"{name:28s} n={m['n']:3d} R={m['mean_r']:.3f} 2xR={sm['mean_r']:.3f} folds={fr}", flush=True)

    # Combined portfolios
    trades_map["prod_both"] = trades_map["prod_d1_be2r"] + trades_map["prod_h4_3r_session"]
    trades_map["refined_both"] = trades_map["refined_d1_m15_be2r"] + trades_map["refined_h4_m15_3r_session"]

    print("\n--- Cross-margin Account Replay ($100, 2% risk, max 3 pos) ---")
    results = {}
    for arm in ("prod_d1_be2r", "prod_h4_3r_session", "prod_both",
                "refined_d1_m15_be2r", "refined_h4_m15_3r_session", "refined_both"):
        results[arm] = {}
        for lev in (5, 10, 20, 50, 200):
            acc = cross_margin_account(trades_map[arm], data, specs, capital=100.0,
                                       risk_pct=0.02, leverage=lev, max_open=3)
            results[arm][f"lev_{lev}"] = acc
            print(f"{arm:28s} lev={lev:3d}x -> final={acc['final']:6.2f} $/day={acc['per_day']:+.3f} "
                  f"maxDD%={acc['max_drawdown_pct']:4.1f} liq={acc['liquidations']} "
                  f"trades={acc['taken']} skippedMin={acc['skipped_min']}", flush=True)

    print("\n--- Scaling Capital ($300 / $1000 at 10x) ---")
    for cap in (300.0, 1000.0):
        for arm in ("prod_both", "refined_both"):
            acc = cross_margin_account(trades_map[arm], data, specs, capital=cap,
                                       risk_pct=0.02, leverage=10, max_open=3)
            print(f"{arm:28s} cap=${int(cap)} lev=10x -> final={acc['final']:7.2f} "
                  f"$/day={acc['per_day']:+.3f} maxDD%={acc['max_drawdown_pct']:4.1f} liq={acc['liquidations']}")

    OUTPUT.write_text(json.dumps(dict(trades={k: edge.metrics(v) for k, v in trades_map.items()},
                                      accounts=results), indent=2))
    print(f"\nSaved report to: {OUTPUT}")


if __name__ == "__main__":
    main()
