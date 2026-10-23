# Forex forward audit - 2026-10-24 (read-only)

Snapshot: 2026-10-23 20:48 UTC from VPS `664aa58`. No config, ledger or order changed.
Raw numbers: `research-output/forex-forward-audit-2026-10-23.json` (git-ignored).
Script: `scripts/forex_forward_audit.py`.

## Verdict
Forward evidence is effectively zero. Nothing to conclude about profitability; keep collecting.

## 1. Sample quality
- V3 live ledger (11 symbols x 56 trees): started 2026-10-21 13:57 UTC (~2.3 days). 15 samples total:
  3 resolved, 4 pending, 8 skipped (all `session_close_near`).
- The 3 resolved (USDCHF x2, EURJPY x1, +5.9R, all short, one tree `barrier_partial_h1`) come from an OLD
  logic version `18ed9601c313fb49` with `costs.verified=false`, slippage 0.
  `parent_stats()` ignores them (requires current version + verified costs). Do not quote that +1.97R.
- Current version `0570692d5bd85380`: 0 resolved, 4 pending. Qualifying count is 0 of required 10 per tree.
- Entry pilot V4 (`7b9f7ce2047f635d`, enabled, since 2026-10-23 ~15:46 UTC): 12 samples, 0 resolved,
  8 excluded `session_close_near`, 4 pending. Intents table: 2 cancelled.
- 4 symbols produced any sample; 7 of 11 symbols have zero. 8 of 12 current-version samples are 4 trees x 2 symbols
  on one candle (1792784700): one setup counted several times. Effective independent setups ~ 3-4.
- `session_close_near` = 8/12 current samples excluded. All on Friday 2026-10-23 19:45 UTC, right before weekly
  close: expected behaviour, not a bug. Re-check only if it keeps firing on weekdays.
- No stuck waiting/pending > 48h. No non-finite R.

## 2. Costs
- Snapshot flags: `swap_included=False`, `margin_verified=False`, `costs_calibrated=False`; R is ex-swap.
- Slippage: per-symbol report exists only for XAUUSD (`exness-slippage-report.json`): 14 fills / 7 round trips,
  mean -0.025 bps per side (demo, 2026-10-16). Other 10 symbols use assumed 1.0 bps. XAU 0 bps used in forward samples.
- 7 round trips on a single symbol on demo are too few; the candle study showed result flips between 0 and 1 bps.
  Not calibrated for FX pairs or JPY crosses.
- Commission: operator override `EXNESS_COMMISSION_PER_LOT_USD=7` when API omits it (round-turn). Verify against statement.
- Feed: XAUUSD 1m currently reports `Exness rate limit; waiting before retry`. Recheck for gaps in the data window.

## 3. Qualification gate
- `qualified_trees()` (`backend/app/forex_htf_v2.py:1083`) only sorts by `eligible`/mean R; it does not filter.
  `FOREX_HTF_V2_EXECUTION_ENABLED=true` + `EXNESS_DEMO_EXECUTION_ENABLED=true`: a tree with zero history can
  open demo orders. Fine for data collection, but demo P&L is not evidence of a qualified strategy.
- `parent_stats`: eligible = >=10 resolved AND mean R > 0. No lower confidence bound, no min days or distinct
  setups, no cost-calibration condition. Evolution parents use the same rule, so 10 trades can drive mutation.
- Execution cap: 66 positions, 6 per symbol, $25 risk, 0.10 max lot (demo). Correlated pairs (EURJPY/GBPJPY/USD pairs)
  can stack the same macro bet.
- Separate legacy executions (`exness_execution_trades`): 5 XAUUSD trades on 2026-10-19, `structural_tp_v1`, old pipeline.

## 4. Recommended next steps (none applied)
1. Do not change signal logic. Version `0570692d5bd85380` and `7b9f7ce2047f635d` must stay fixed for clean data.
2. Report only: counts of distinct setups (symbol, time, side), not trees. Require e.g. >=30 distinct setups per
   comparison before any decision; threshold is a judgement, not a guarantee.
3. Fix measurement, which does not change signals: slippage per symbol (collect >=20 fills/pair), swap, commission check.
4. Before any promotion: gate on lower bound and distinct setups, with costs calibrated.
