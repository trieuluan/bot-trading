"""Offline $100/$300/$1000 account replay: production H4/D1 seeds vs M15/H1 scalps.

Run: /tmp/btv/bin/python -m scripts.small_capital_replay
No orders, DB or production changes. Reused history: exploratory, not proof.
ponytail: ignores exchange min contract size/margin tiers; add when picking a live setup.
"""

import json
import math
from collections import defaultdict
from pathlib import Path

from scripts import fee_edge_replay as edge
from scripts import htf_portfolio_replay as native
from scripts.price_action_replay import atr, ema

OUTPUT = Path("research-output/small-capital-replay-20261108.json")
RISK, MAX_OPEN, LEVERAGE_CAP = 0.02, 3, 10  # 2% equity risk, 3 positions, 10x notional cap


def m15_signals(symbol, rows):
    closes, highs, lows = ([getattr(b, k) for b in rows] for k in ("close", "high", "low"))
    fast, slow, ranges = ema(closes, 20), ema(closes, 50), atr(rows)
    for i in range(60, len(rows) - 1):
        a, bar = ranges[i], rows[i]
        for d in (1, -1):
            bull = d * (bar.close - bar.open) > 0
            trend = d * (bar.close - slow[i]) > 0 and d * (fast[i] - slow[i]) > 0
            prior = highs[i - 20:i] if d == 1 else lows[i - 20:i]
            breakout = bar.close > max(prior) if d == 1 else bar.close < min(prior)
            sweep = (bar.low < min(lows[i - 20:i]) < bar.close if d == 1
                     else bar.high > max(highs[i - 20:i]) > bar.close)
            pull = (bar.low <= fast[i] < bar.close if d == 1 else bar.high >= fast[i] > bar.close)
            for name, ok in (("m15_breakout", breakout), ("m15_sweep", sweep), ("m15_pullback", pull)):
                if ok and bull and (trend or name == "m15_sweep"):
                    swing = min(lows[i - 4:i + 1]) if d == 1 else max(highs[i - 4:i + 1])
                    dist = max(d * (bar.close - swing) + 0.25 * a, a)
                    yield dict(symbol=symbol, seed=name, entry_idx=i + 1, entry_time=bar.time + 900,
                               direction=d, stop=bar.close - d * dist, max_m15_bars=16)


def account(trades, capital, days):
    equity, peak, worst, open_, daily, taken = capital, capital, 0.0, [], defaultdict(float), 0
    for t in sorted(trades, key=lambda r: r["entry_time"]):
        for o in sorted(o for o in open_ if o[0] <= t["entry_time"]):
            equity += o[1]
            daily[o[0] // 86400] += o[1]
            open_.remove(o)
            peak, worst = max(peak, equity), max(worst, 1 - equity / peak)
        if equity <= 0:
            break
        notional = sum(o[2] for o in open_)
        need = RISK * equity / (t["risk_pct"] / 100)
        room = LEVERAGE_CAP * equity - notional
        if len(open_) >= MAX_OPEN or room <= 0:
            continue
        size = min(need, room)
        open_.append((t["exit_time"], RISK * equity * (size / need) * t["net_r"], size))
        taken += 1
    for o in sorted(open_):
        equity += o[1]
        daily[o[0] // 86400] += o[1]
        peak, worst = max(peak, equity), max(worst, 1 - equity / peak)
    series, months = list(daily.values()), defaultdict(float)
    for d, v in daily.items():
        months[d // 30] += v
    return dict(final=round(equity, 2), per_day=round((equity - capital) / days, 3), trades=taken,
                max_drawdown_pct=round(100 * worst, 1), loss_day_share=round(
                    sum(x < 0 for x in series) / max(1, sum(x != 0 for x in series)), 3),
                active_days=sum(x != 0 for x in series), worst_30d=round(min(months.values(), default=0), 2))


def check():
    rows = [{"entry_time": 0, "exit_time": 900, "risk_pct": 1.0, "net_r": 1.0}]
    assert account(rows, 100, 1)["final"] == 102.0
    rows = [{"entry_time": 0, "exit_time": 900, "risk_pct": 0.1, "net_r": -1.0}]
    assert math.isclose(account(rows, 100, 1)["final"], 99.0)  # 10x cap halves 2% risk -> -1
    print("self-checks passed", flush=True)


def main():
    data, first, end, _ = native.load_data()
    fees = json.loads(edge.FEES.read_text())["fees"]
    folds, days = native.build_folds(first, end), (end - first) / 86400
    grouped = defaultdict(list)
    for symbol, pack in sorted(data.items()):
        for s in [*native.seed_signals(symbol, pack[2]), *edge.hourly_signals(symbol, pack[2]), *m15_signals(symbol, pack[2])]:
            grouped[s["seed"]].append(s)
    arms = {"prod_d1_be2r": ("d1_squeeze", 2, "be", 1, "all"),
            "prod_h4_3r_session": ("h4_sweep", 3, "fixed", 1, "utc_12_20"),
            "h1_sweep_down_3r_session": ("h1_sweep_reclaim_-1_down", 3, "fixed", 1, "utc_12_20"),
            "h1_bos_up_2r": ("h1_bos_retest_1_up", 2, "fixed", 1, "all")}
    for name in ("m15_breakout", "m15_sweep", "m15_pullback"):
        for tp, mode in ((1, "fixed"), (2, "fixed"), (2, "trail")):
            arms[f"{name}_tp{tp}_{mode}"] = (name, tp, mode, 1, "all")
    report = {}
    for label, (seed, tp, mode, hold, gate) in arms.items():
        trades = edge.replay(data, grouped[seed], fees, folds, tp, mode, hold, gate)
        stressed = edge.replay(data, grouped[seed], fees, folds, tp, mode, hold, gate, stress=2)
        report[label] = dict(metrics=edge.metrics(trades), double_cost_r=edge.metrics(stressed)["mean_r"],
                             folds={f: edge.metrics([t for t in trades if t["fold"] == f]) for f, _, _ in folds},
                             accounts={str(c): account(trades, c, days) for c in (100, 300, 1000)})
        m = report[label]["metrics"]
        print(label, "n", m["n"], "R", round(m["mean_r"], 3), "costR", round(m["mean_fee_r"] + m["mean_funding_r"], 3),
              "2xR", round(report[label]["double_cost_r"], 3), "folds",
              [round(report[label]["folds"][f]["mean_r"] or 0, 3) for f, _, _ in folds],
              "$100:", report[label]["accounts"]["100"], flush=True)
    both = [t for k in ("prod_d1_be2r", "prod_h4_3r_session")
            for t in edge.replay(data, grouped[arms[k][0]], fees, folds, *arms[k][1:])]
    report["prod_combined"] = dict(metrics=edge.metrics(both),
                                   accounts={str(c): account(both, c, days) for c in (100, 300, 1000)},
                                   mtm_uncapped_100=native.simulate_event_portfolio(both, data, RISK, 100.0)["final_equity"])
    print("prod_combined $100:", report["prod_combined"]["accounts"]["100"],
          "MTM uncapped $100:", round(report["prod_combined"]["mtm_uncapped_100"], 2), flush=True)
    OUTPUT.write_text(json.dumps(dict(period_days=days, risk=RISK, max_open=MAX_OPEN,
                                      leverage_cap=LEVERAGE_CAP, arms=report), indent=2, allow_nan=False) + "\n")


if __name__ == "__main__":
    check()
    main()
