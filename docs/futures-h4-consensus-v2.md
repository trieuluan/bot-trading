# Futures H4 consensus v2

Implemented 2026-10-24. Deployed for forward data; not profitability-qualified.
The preceding six-filter replay does not test this combined architecture.

## Entry contract

```text
ALL(
  H4: ALL(EMA50 direction, confirmation A, confirmation B),
  H1: existing setup subtree,
  M15: existing trigger subtree
)
```

Each genome chooses exactly two distinct confirmation feature IDs from the H4
catalog. All three H4 leaves must pass for the same trade direction. There is
no OR or 2-of-N vote inside H4, no duplicate confirmation, and EMA50 cannot be
removed, mutated, or moved to another frame. Exactly two confirmations satisfy
the requested minimum without adding an operator or changing the JSON schema.

All 26 existing G0 entry templates retain their H1/M15 subtrees. Their initial
H4 pair is `swing_structure` + `market_alignment`. Each evolved child may mutate
one confirmation or its H1/M15 subtree. Crossover copies the mate's complete H4
pair and H1 subtree while retaining the parent's M15 trigger. The common
depth limit stays three; a validated H4 consensus slot permits three extra
nodes relative to the old 11-node budget.

Stops, targets, sizing, execution costs, and exit-policy algorithms are not
changed. Losing an H4 confirmation after entry does not itself create a new
early-exit policy. Existing signal-invalidation exits retain their own rules.
The upgrade does not cap stacked positions or correlated portfolio risk.

## Finite confirmation catalog

There are 40 available H4 confirmation features, not 40 simultaneous votes:

| Group | Feature IDs |
| --- | --- |
| EMA | `ema_distance`, `ema_slope`, `ema_cross`, `ema_cross_n`, `ema_bounce`, `ema_touch` |
| Trend/market | `swing_structure`, `market_alignment`, `adx_regime`, `relative_strength` |
| Structure/location | `support_proximity`, `breakout`, `bos`, `swing_zone`, `fib_retrace`, `fvg`, `wedge` |
| Reclaim/sweep/momentum | `rsi_divergence`, `session_vwap`, `pdh_pdl`, `pdh_pdl_sweep`, `sweep`, `wick_sweep`, `bb_pierce`, `rsi`, `volume_dryup` |
| Candles | `engulfing`, `pin_bar`, `star`, `soldiers`, `doji_reversal`, `inverted_pin`, `marubozu`, `harami`, `harami_cross`, `piercing`, `tweezer`, `three_inside`, `three_outside`, `three_methods` |

This reuses all selected directional price/candle confirmations already in the
engine, plus five new H4-only predicates. It is not a claim to include every
indicator that could ever be written. Neutral context such as compression,
volume alone, session availability, news safety, or funding costs does not
count as an H4 directional confirmation. H1 context features remain available
in their existing slot.

Distinct feature IDs do not mean statistically independent evidence: two EMA
features or overlapping candle patterns can be highly redundant. No automatic
claim of stronger confidence or higher win rate follows from reaching 3/3.

### Five new predicates

- `ema_distance`: directional distance from close to EMA50 >= 0.5 ATR14.
- `ema_slope`: directional EMA50 change over three closed H4 bars > 0.
- `swing_structure`: last two confirmed highs and last two confirmed lows both
  move in the direction. Search last 20 H4 bars; pivots require two bars on
  either side and use no bars after decision time.
- `market_alignment`: BTC is on the matching side of its EMA50 and at least
  five of all ten configured coins agree, including the traded coin. Requires
  all ten aligned histories with at least 50 closed H4 bars; missing/stale
  peer history fails closed, without shrinking the denominator.
- `support_proximity`: close is above the latest confirmed low for a long, or
  below the latest confirmed high for a short, at most one ATR14 away. Uses
  the same 20-bar pivot search and confirmation as structure.

All predicates use complete, closed H4 bars. The live rolling window remains
1,600 M15 bars. Cross-coin context now loads all nine other symbols for every
coin. H1 relative strength still compares altcoins to BTC; BTC still compares
to its peers. The historical replay CLI also supplies all nine peers.

## State transition

The learner version changes from `futures_htf_v1` to
`futures_htf_h4_consensus_v2` when the updated code reads old state:

1. Archive old candidates under their existing IDs without changing genomes,
   qualification windows, promotion flags, or recorded scores.
2. Start a new active seed/child pool with distinct genome IDs, empty windows,
   and no promotions. Birth is the next M15 boundary; do not backfill new
   forward results into the old epoch.
3. Preserve samples, open-sample exit rules, historical reports, funding,
   cursor, enable flag, and auto mode. Clear the obsolete last decision.
4. On persistence, subsequent reads do not repeat the transition.

Legacy H4-only-EMA trees remain readable for old trade charts, exits, lineage,
and replay. They are not active candidates after the v1 state transition.
Historical priors remain attached to old IDs; new genomes do not inherit their
qualification. Persisted transition rollback requires restoring the prior
state snapshot; downgrading code is not a safe state rollback procedure.

No local development DB or VPS state was migrated by this task. Tests use an
isolated SQLite database. No commit, push, deploy, or restart was performed.

## Validation before rollout

Tests cover strict consensus validation, missing/duplicate/wrong-frame leaves,
both mandatory confirmations, closed-bar behavior, exact market breadth,
missing/stale/future peers, mutation/crossover, unchanged relative-strength
reference, and idempotent migration preserving open samples and old results.

The next performance experiment must replay the combined 3/3 seeds and then
collect a separately labeled forward epoch. The earlier B/I single-filter
results cannot be reused as results of B AND I. This change may substantially
reduce signal opportunities and provides no established net-profit benefit.

## Kronos integration assessment

Official sources checked on 2026-10-24:

- Repository and MIT license: <https://github.com/shiyu-coder/Kronos>
- Model card: <https://huggingface.co/NeoQuasar/Kronos-small>
- Predictor implementation: <https://github.com/shiyu-coder/Kronos/blob/master/model/kronos.py>
- Dependencies: <https://github.com/shiyu-coder/Kronos/blob/master/requirements.txt>
- Paper: <https://arxiv.org/abs/2508.02739>

Kronos is an autoregressive financial candlestick foundation model, not a
ready-made trading bot. Its tokenizer and model forecast OHLC price paths;
volume/amount can also be supplied. Published variants include mini (4.1M,
2,048 context), small (24.7M, 512), and base (102.3M, 512), with matching
tokenizers. These are maximum contexts, not minimum requirements. The current
bot's roughly 100 closed H4 bars can be used without inventing longer history.

The Python predictor accepts historical OHLC, historical timestamps, future
timestamps, forecast horizon and sampling controls. Its default prediction
aggregates sampled paths. It does not return a calibrated trade win
probability or account for this bot's fills, SL/TP, fees, funding, and leverage.
Its paper reports forecasting benchmarks, not validation of this MEXC bot.

Integration is technically plausible, but not implemented here. Proposed first
experiment: a separate, local shadow inference worker invoked on each H4 close,
predicting the next one to three H4 bars from closed historical data. Persist
model/tokenizer revision, input cutoff/hash, generation time, sampling seed,
and forecast before any predicted bar is observed. Measure inference time and
memory on the actual VPS before choosing a model or hardware. PyTorch >=2.0,
pandas and the upstream code/checkpoints add a separate dependency stack;
do not install these into the trading process during this change.

Keep the 3/3 tree independent during the first evaluation. Compare its signals
with and without a frozen Kronos-derived directional filter only in research.
Do not count multiple Kronos samples as multiple confirmations, or interpret
their directional fraction as calibrated confidence. Reject invalid OHLC,
stale forecasts, wrong cutoffs, failed inference, and non-finite outputs. If a
future version makes Kronos mandatory, missing inference must block new entries,
not silently weaken the gate; open-position risk management must continue.

No package or checkpoint was downloaded, no inference was run, and no VPS
latency or profitability claim has been established. Any historical evaluation
must also audit checkpoint training overlap with the evaluation interval.

## Update 2026-10-25: 2/3

H4 gate relaxed from 3/3 to 2/3: EMA50 trend is mandatory plus at least one of
the two confirmations. Genome schema, fingerprints and learner version are
unchanged; only `matches()` evaluation changed. Forward samples collected
before this change used 3/3. 2/3 is weaker evidence than 3/3.
