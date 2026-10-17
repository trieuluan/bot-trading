# Forex learner v7

## Changes

- Structural targets must be at least 1.5R after buffer and price rounding.
  A nearby barrier rejects the setup; it is not ignored to target a farther barrier.
- Evolution ranks training labels by `mean(R) - 1.96 * stdev(R) / sqrt(n)`.
  Fewer than 15 training labels gives no fitness priority. This is an approximate
  uncertainty penalty, not a guarantee or a multiple-testing correction.
- Generated strategies evolve `stop_atr`, `target_r` (1.5 to 3.0 in 0.5 steps),
  and boolean `partial_be`. Names include exit settings, so changed exits do not
  inherit another strategy's labels or promotion.
- `partial_be=true` uses broker-size-aware TP1 at 1R and a managed runner.
  `partial_be=false` keeps the whole position to its genome TP/SL without BE or
  trailing. Both still obey early-risk exits, session closures and safety limits.
- Generated historical and forward labels use the same sizing, split, BE,
  trailing and early-exit helpers as execution. Missing instrument specs exclude
  labels rather than inventing sizes. M15 OHLC replay is not tick/live execution.
- Existing non-generated strategies retain their default exit selection. The
  exploratory 30-day audit does not justify adopting its best training variant.
- Policy version changes invalidate earlier simulated exit labels. Promotion
  still requires sufficient train/validation labels and verified costs.
- Scalping stays shadow-only; no M1 broker execution is added.

## Data and validation

M15/H1/H4 already span roughly 93 calendar days in the local database as of
October 17, 2026. `load_frames()` reads all stored closed candles, and the learner
does not impose the 30-day cutoff used by the earlier audit. No duplicate download
is needed to use the existing 90+ day history.

Run regression tests:

```sh
docker exec bot-trading-api-1 python3 -m pytest -q
```

Rebuild the simulated learner/policy from stored history:

```sh
docker exec bot-trading-api-1 python3 -c 'from backend.app.forex_learner import learning_tick; print(learning_tick(force=True))'
```

This writes the policy consumed by the demo worker, not broker orders. Reload
API/worker processes after code changes. Existing positions retain their recorded
exit settings. Reports remain in `research-output/`.

Historical evolution and repeated validation inspection are exploratory, not
untouched out-of-sample evidence. Overlapping signals are correlated. Do not
lower promotion gates or increase risk merely to collect more trades.
