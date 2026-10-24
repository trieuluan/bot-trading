# Futures G0 entry replay: market vs limit vs retest (2026-10-24)

Research only. Script: `scripts/futures_entry_replay.py`. Output: `research-output/futures-h4/g0-entry/`.

## Design
- Cohort: fixed G0 `consensus` signals from `research-output/futures-h4/g0-consensus/` (2,196 signals, 10 pairs). Engine hash + cache SHA verified; `market` arm reproduces source `net_r` exactly.
- Same setup, stop, target, exit policy. Only entry changes. Wait window `WAIT_BARS=4` M15 bars.
- Arms: `market`; `body38`/`body50` (limit 38%/50% back into signal body); `ema20_m15` (limit at M15 EMA20); `breakout_h1` (limit at broken prior 20-H1 high/low); `retest_h1` (touch level, close beyond with same-direction body, enter next open).
- Fill: trade-through only, fill at limit, no entry slippage, taker fee kept. Stop checked first on fill bar; target on fill bar only if close beyond target.
- Key metric `ref/sig`: net R rescaled to market risk, averaged over all signals (unfilled = 0). `missed` = market R of cancelled signals.

## Results
| arm | split | signals | fill | mean R (filled) | PF | ref R/signal | missed market R |
|---|---|---:|---:|---:|---:|---:|---:|
| market | train | 1727 | 100% | -0.130 | 0.76 | -0.130 | - |
| market | validation | 469 | 100% | +0.069 | 1.14 | +0.069 | - |
| body38 | train | 1727 | 84% | -0.131 | 0.76 | -0.095 | +0.162 (272) |
| body38 | validation | 469 | 80% | +0.108 | 1.23 | +0.090 | +0.399 (93) |
| body50 | train | 1727 | 80% | -0.121 | 0.78 | -0.077 | +0.043 (349) |
| body50 | validation | 469 | 75% | +0.118 | 1.26 | +0.091 | +0.328 (118) |
| ema20_m15 | train | 1727 | 11% | +0.028 | 1.05 | -0.001 | -0.118 (1537) |
| ema20_m15 | validation | 469 | 13% | -0.216 | 0.68 | -0.024 | +0.175 (410) |
| breakout_h1 | train | 1727 | 26% | -0.129 | 0.77 | -0.027 | -0.071 (1275) |
| breakout_h1 | validation | 469 | 27% | -0.064 | 0.88 | -0.033 | +0.216 (342) |
| retest_h1 | train | 1727 | 8% | -0.127 | 0.78 | -0.011 | -0.135 (1581) |
| retest_h1 | validation | 469 | 5% | +0.104 | 1.16 | -0.000 | +0.073 (445) |

## Read
- `body50`/`body38`: only arms better than market in both splits per signal (+0.02..+0.05R). Gain comes from better price on filled trades; missed trades are winners (adverse selection), so edge is thin. CIs overlap market heavily.
- `ema20_m15`, `breakout_h1`, `retest_h1`: fill 5-27%. Filled trades no better than market. Per-signal near 0 only because most signals skipped. Not an entry improvement.
- Retest does not fix trend-trap entries in this cohort.

## Caveats
OHLC intrabar ambiguity; no queue/partial-fill model; maker fee not modelled (would help limits slightly); validation data reused from previous studies; no portfolio/concurrency effects; small validation sample (469 signals, ~ events halved).

## Follow-up: `body50`, `WAIT_BARS=4`, paired vs market (`scripts/futures_entry_paired.py`)
Same-signal difference (body50 minus market, unfilled = 0 R), block bootstrap by H4 bucket, 2000 draws. Maker fee column changes only limit entry fee (taker 0.08%).

| entry fee | split | n | diff R/signal | CI95 | symbols better |
|---|---|---:|---:|---|---:|
| taker 0.08% | train | 1727 | +0.053 | [+0.001, +0.110] | 8/10 |
| taker 0.08% | validation | 469 | +0.022 | [-0.130, +0.165] | 6/10 |
| maker 0.02% | train | 1727 | +0.079 | [+0.027, +0.137] | 8/10 |
| maker 0.02% | validation | 469 | +0.055 | [-0.098, +0.197] | 6/10 |
| maker 0.00% | train | 1727 | +0.088 | [+0.036, +0.146] | 9/10 |
| maker 0.00% | validation | 469 | +0.066 | [-0.089, +0.207] | 6/10 |

Verdict: direction consistent and positive, significant only on train; validation CI spans 0. Not proof. Data ends 2026-10-18; not refreshed.

## Refresh with newer MEXC data (`research-output/futures-h4-oct24/`)
Candles, fair price and funding extended from 2026-10-18 to 2026-10-24 via `scripts/futures_extend_cache.py` (VPS cache hash was identical to local, so data pulled from public MEXC API). G0 `consensus` re-replayed, then `scripts/futures_entry_replay.py` (output `g0-entry-v2`; market arm exact). The 70/30 split moved to 2026-07-12, so train/validation are not identical to the first run. Signals still open at dataset end are excluded (censored), not scored.

| arm | split | signals | fill | PF | ref R/signal |
|---|---|---:|---:|---:|---:|
| market | train | 1752 | 100% | 0.76 | -0.130 |
| market | validation | 486 | 100% | 1.03 | +0.015 |
| body38 | validation | 486 | 81% | 1.11 | +0.047 |
| body50 | train | 1752 | 80% | 0.78 | -0.079 |
| body50 | validation | 486 | 76% | 1.13 | +0.051 |

Paired body50 minus market: validation taker +0.036 [-0.123, +0.174]; maker 0.02% +0.069 [-0.092, +0.208]; train taker +0.051 [-0.002, +0.104]. Only the 42 signals after 2026-10-18 (out-of-sample vs previous run): body50 +0.128 R/signal over market (market -0.687 on them); far too few to conclude.

## Entry policy gene (code, not deployed)
- `backend/app/futures_entries.py`: `ENTRY_POLICIES = market, body38, body50, ema20_m15, breakout_h1, retest_h1`; optional genome field `entry_policy` (absent = `market`, so every existing genome and name is unchanged).
- `ENTRY_SEEDS` = one G0 (`RULE_SEEDS[0]` tree) with `entry_policy="body50"`. Other policies arrive only via `mutate` (new `entry_policy` gene) or `crossover` (child inherits mate's non-market policy).
- Learner and history replay build samples with `entry_fields`; `advance` calls `wait` until filled. Unfilled signals resolve as 0R so they are scored, not hidden.
- Non-market genomes can now be selected in `paper_learning`. Qualified mode still blocks them (`entry_windows_supported=false`): its validation windows currently simulate market entries, so those results cannot qualify limit/retest entries.
- `scripts/futures_entry_replay.py` now imports the same functions; BNB re-run reproduces all six arms exactly.

## Paper pending orders (local implementation)
- Migration `0026` persists pending intents separately from filled `paper_trades`; no exchange endpoint is called. Existing market entries remain unchanged.
- Auto Paper queues the selected non-market policy with frozen signal level, SL/TP and genome. One pending intent per pair; no margin reserved. Repeated decisions, cancel requests and worker ticks cannot fill the same intent twice (common wallet lock).
- Evaluate closed M15 bars in order, for four bars after the signal. Require strict trade-through; an exact touch does not fill. Same-bar exits are conservative: SL first; TP only when the fill bar closes beyond it. Fees use the configured taker rate, no entry slippage for limit fills. Retest requires a same-direction confirmation close, then uses fresh market bid/ask plus slippage, not a limit fill.
- Fill-time checks: Auto still enabled, MEXC source, fresh quotes for the portfolio, funding available, daily loss/trade caps, free margin, per-order margin and portfolio SL risk. Size uses current equity; queued levels and strategy do not change. Missing bars are never skipped. Fills discovered more than 60 seconds after bar close are cancelled rather than backdated after an outage. Turning Auto off or using the cancel endpoint cancels pending intents without closing existing positions.
- Wallet/stream expose the latest 100 intents per pair, including filled/cancelled/expired outcomes. UI has a pending tab for one/all pairs, cancellation, expiry countdown, gross RR, and dashed pending entry/SL/TP lines on the chart.
- Limitations: OHLC simulation, not exchange matching; no queue priority, partial fills or maker rebates. Intrabar ordering is unknown; the first eligible bar is the bar following the signal. Positions created from closed-bar fills use that bar's open timestamp as a proxy. This is not evidence that body50 outperforms market.
