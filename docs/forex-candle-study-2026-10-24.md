# Forex M15 candlestick replay - 2026-10-24

## Decision

Do not change the live Forex tree based on this study. None of the 56 primary
configurations made positive net R in the chronological holdout at 1 bps adverse
slippage per side. This is evidence about these entry/exit combinations and this
sample, not proof that all candlestick strategies fail.

## Data

- Read-only `pg_dump` of VPS `workspace_candles`; no VPS deployment or broker calls.
- Snapshot: `research-output/candles/2026-10-24/workspace_candles.sql.gz`.
- Separate instrument/commission snapshot excludes account identifiers and credentials.
- 11 symbols: AUDUSD, EURGBP, EURJPY, EURUSD, GBPJPY, GBPUSD, NZDUSD, USDCAD,
  USDCHF, USDJPY, XAUUSD. Includes XAUUSD gold, not only currency pairs.
- 250,930 candle rows across all frames/sides; 70,160 M15 bid candles with paired asks.
- Versus previous local cache: 407 additional M15 timestamps, zero removed or
  revised rows across all frames. Original cache remains untouched.
- Raw history starts 2026-07-15 for XAUUSD and 2026-07-23 for other pairs.
- Common evaluated interval after warmup: 2026-08-01 17:00 UTC through
  2026-10-23 20:00 UTC, exclusive at the end.
- Train cutoff: 2026-09-28 21:30 UTC. Holdout starts after a 98-M15-bar embargo,
  at 2026-09-29 22:00 UTC (2026-09-30 05:00 Vietnam time).

## Experiment

`scripts/forex_candle_study.py` operates offline, reusing the current Forex
`diagnose`, H4 context, default H1 setups and default ADX gate. It temporarily
substitutes a trigger callable only inside its research process. Production
registries, schemas, trees, DB and ledgers are not written.

The six tested patterns reuse the existing futures predicates without rewriting
their definitions: Engulfing, Hammer/Shooting Star (`pin_bar`), Morning/Evening
Star (`star`), Doji reversal, Harami and Tweezer. These are the repository's
gap-independent definitions, not every textbook Japanese candlestick pattern.
Pattern entries also require price to hold the H1 setup level.

The control is the union of unfiltered G0 M15 triggers for each H1 setup family.
Duplicate exit-policy trees collapse to one setup. This is a fixed-exit entry
control, **not** the exact current evolved population or adaptive-exit portfolio.

- Keep H1 structure stop and live 0.5-3 H1 ATR entry-risk bounds.
- Enter at next M15 open, or place a breakout stop beyond the signal candle by
  0.05 H1 ATR, valid for two subsequent M15 bars. No entry at the known signal close.
- Compare gross targets 1.5R, 2R, 3R and the live structural target: 7 trigger
  groups x 2 entries x 4 targets = 56 configurations per slippage scenario.
- One first trigger per setup/pattern; one open trade per symbol/configuration.
  Simultaneous H1 families use deterministic alphabetical ordering.
- Use executable bid/ask, adverse slippage on both sides, commission on both
  sides, and only completed FX conversion bars. Account P&L is normalized by
  initial entry-to-stop risk in USD; fees are included in P&L, not the R denominator.
- Stop wins if SL and TP touch in one candle. For intrabar breakout fills,
  ambiguous prior stop touches invalidate the order; fill-bar TP is not credited
  unless entry was already executable at the open.
- Close before a scheduled session break or after 96 M15 bars. Missing candles
  censor that trade; no synthetic fill or forward-filled OHLC. Research resumes
  at the next observed bar, without assuming the missing trade's P&L.
- Select one pattern configuration using train mean net R only, requiring at
  least 30 completed training trades and positive train mean. No holdout tuning.

## Primary Results: 1 bps Per Side

The primary run produces 2,718 trigger events. Executions across configurations
are overlapping research alternatives, not independent trades to sum together.

| Configuration                              | Train trades | Train mean R | Test trades | Test mean R | Test total R | Test PF |
| ------------------------------------------ | -----------: | -----------: | ----------: | ----------: | -----------: | ------: |
| Control, market, structural TP             |          280 |      -0.0711 |         119 |     -0.2126 |     -25.3037 |  0.7010 |
| Control, breakout stop, 3R                 |          206 |      +0.1168 |          78 |     -0.2305 |     -17.9788 |  0.6602 |
| Engulfing, breakout stop, 3R               |          166 |      +0.0526 |          71 |     -0.0913 |      -6.4831 |  0.8600 |
| Hammer/Shooting Star, market, 3R           |           82 |      +0.0836 |          34 |     -0.0856 |      -2.9096 |  0.8810 |
| Star, breakout stop, 3R: selected on train |           79 |      +0.1167 |          34 |     -0.3276 |     -11.1398 |  0.5644 |

The train-selected Star configuration wins 26.47% of completed holdout trades;
closed-trade drawdown is 12.6692R. A 3R gross target does not imply a realized
3R average win: session/time exits and costs affect the outcome.

## Cost Sensitivity

Spread and commission remain enabled in all scenarios.

| Adverse slippage per side | Positive holdout configs / 56 | Same Star config: test trades | Same Star config: test mean R |
| ------------------------- | ----------------------------: | ----------------------------: | ----------------------------: |
| 0 bps, optimistic         |                            11 |                            61 |                       -0.3309 |
| 1 bps, primary assumption |                             0 |                            34 |                       -0.3276 |
| 2 bps, stress             |                             2 |                            22 |                       -0.1891 |

These are full replays with cost-aware eligibility, not paired repricing of
identical trades. Higher cost changes both the signal gate and subsequent
one-open-position scheduling, so sample counts and averages need not move
monotonically.

At 0 bps, Tweezer + breakout stop + 3R has train mean +0.2743R and holdout
+0.3416R over 30 completed holdout trades. At 1 bps, the same configuration has
train +0.0134R and holdout -0.5662R over 21 trades. This is fragile sensitivity,
not a robust entry rule. The two positive 2-bps results have only 15/17 holdout
trades and negative training means; neither is an eligible train-selected rule.

## Limits And Artifacts

- No swap, historical commission changes, lot rounding, margin constraints,
  capital weighting or portfolio exposure model. Current specs apply historically.
- Reported drawdown uses closed equal-risk trade R, not marked-to-market equity.
- Correlated currency pairs and multiple tests reduce effective sample size.
  Holdout is about 24 days; no statistical significance claim is made.
- The primary alternatives contain 71 gap-censored and 115 end-open attempts
  across configurations. These are excluded from resolved P&L, not counted as zero.
- This experiment replaces the entry trigger; it does not test every possible
  additional candle filter, M15-based stop, or retracement-limit entry.
- Detailed reports: `research-output/forex-candle-study-2026-10-24-{0,1,2}bps.json`.
- Refresh audit: `research-output/candles/2026-10-24/sync-audit.json`.
- Full configuration table: `research-output/forex-candle-study-2026-10-24.csv`.

Run with installed backend requirements:

```sh
python -m scripts.forex_candle_study \
  --data research-output/candles/2026-10-24 \
  --output research-output/forex-candle-study-new.json \
  --workers 4 --slippage-bps 1
python -m pytest -q backend/tests/test_forex_candle_study.py
```

Output paths must be new. For another valid holdout assessment after changing
entry/stop rules, reserve new future data; the current holdout has now been seen.
