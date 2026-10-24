# Futures H4 filter replay - 2026-10-24

## Scope

Research-only comparison of six additional H4 gates against the existing H4
EMA50 direction gate. No deployed rules, G0 genomes, position sizing, or VPS
services were changed. Each variant adds exactly one gate, not a combination.

| Variant | Additional requirement for the proposed trade direction |
| --- | --- |
| baseline | Existing live H4 EMA50 gate, unchanged |
| C_dist | Directional close-to-EMA50 distance >= 0.5 H4 ATR14 |
| I_market | BTC close is on the matching side of H4 EMA50, and >= 50% of the 10-coin universe agrees |
| B_structure | Last two confirmed H4 highs and lows both move in the trade direction; 20-bar pivot search, two-bar confirmation on either side |
| A_slope | H4 EMA50 change over three closed H4 bars agrees with trade direction |
| G_adx | H4 ADX14 > 25 and directional DI spread agrees |
| K_support | Close is above the latest confirmed low for longs, or below the latest confirmed high for shorts, within one H4 ATR14 |

These are operational definitions, not guarantees of trend persistence. K_support
is a proximity-to-pivot setup gate rather than a general trend-strength measure.

## Inputs and controls

- Historical universe: BTC, ETH, BNB, SOL, XRP, DOGE, AVAX, SUI, NEAR, LINK,
  quoted in USDT on MEXC.
- Common historical interval: 2025-11-12 10:00 UTC through
  2026-10-18 09:45 UTC, exclusive. Warmup: 1,600 M15 bars.
- Chronological train/validation split: 2026-07-08 09:45 UTC.
- Candidate cohort: frozen current VPS HTF genomes with adaptive exits only,
  deduplicated by genome name. Other historical exit policies are not covered.
- Existing `futures_history.replay` signal and execution logic is reused;
  H1/M15 conditions, entries, stops, targets, and adaptive exits stay unchanged.
- Fees: 8 bps per side. Adverse slippage: 2 bps per side. Settled funding comes
  from the historical cache. These are replay assumptions, not measured live costs.
- H4 states use only complete, closed bars in the same rolling 1,600-M15-bar
  window as the runtime. Confirmed pivots cannot use future decision-time data.
- Samples unresolved at the partition boundary or study end are censored by the
  existing engine, not marked as wins or losses.

Local inputs and machine-readable results are under
`research-output/futures-h4/` (git-ignored). `manifest.json` records input SHA-256
hashes and study settings. `bnb-pilot.json` contains the BNB replay;
`rest.json` contains the other nine coins. `live-compare-htf.json` is the corrected
live comparison. Do not use the earlier `live-compare.json`, which includes the
wrong cohort and incomplete market context.

## Interpretation limits

The current evolved genomes were selected using this historical dataset, which
has already been replayed repeatedly. The chronological "validation" partition
is therefore **not untouched out-of-sample evidence**. Results are diagnostic;
they cannot establish a profitable deployable edge.

Replay allows one active position per candidate/variant. It does not reproduce
the live adaptive selector, concurrent position stacking, wallet, margin,
leverage, or capital constraints. The baseline is a frozen-candidate research
baseline, not an exact simulation of the live portfolio. Summed R is not an
achievable portfolio return. Live quote-based fills also differ from replay's
next-bar-open execution model.

Candidate trades overlap heavily. "Events" group the same symbol, signal time,
and direction and average their candidate R; this reduces duplication but does
not make events independent. Serial and cross-symbol correlation remain.
Confidence intervals from the raw JSON are exploratory, not significance tests.
Baseline-kept/removed statistics also reflect changed trade scheduling, not
solely gate blocking.

## Historical results: all ten coins

Completed 70 symbol/variant replays, producing 150,283 resolved candidate-trade
rows across both partitions. `all-pairs.json` combines the BNB pilot and the
other nine coins. `comparison.csv` contains the aggregate statistics.

Mean R is net of the replay costs and uses the engine's initial-stop risk unit.
PF is gross positive net R divided by absolute negative net R, not monetary PF.

| Gate | Train trades | Train mean R | Validation trades | Validation mean R | Validation PF | Validation win rate | Validation events | Event mean R |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| baseline | 22,623 | -0.1460 | 9,350 | -0.0934 | 0.831 | 43.1% | 2,538 | -0.1796 |
| C_dist | 19,118 | -0.1149 | 7,932 | -0.0931 | 0.831 | 42.8% | 2,080 | -0.1808 |
| I_market | 19,140 | -0.1490 | 7,292 | -0.0815 | 0.852 | 43.1% | 1,911 | -0.1495 |
| B_structure | 9,215 | -0.1117 | 3,059 | -0.0183 | 0.965 | 47.1% | 783 | -0.1711 |
| A_slope | 21,561 | -0.1396 | 9,005 | -0.1064 | 0.810 | 42.5% | 2,459 | -0.1917 |
| G_adx | 11,233 | -0.0672 | 4,210 | -0.1152 | 0.802 | 40.5% | 1,207 | -0.1739 |
| K_support | 4,553 | -0.2431 | 1,992 | -0.2445 | 0.640 | 38.6% | 578 | -0.3863 |

Every variant remains negative in aggregate validation. B_structure improves
candidate-trade mean R on 8/10 symbols; I_market improves it on 7/10. For event
mean R, both improve on 7/10 symbols. These are descriptive counts, not tests of
significance or independent evidence.

### Per-symbol validation mean R

| Symbol | baseline | C_dist | I_market | B_structure | A_slope | G_adx | K_support |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| BTC | -0.2642 | -0.3252 | -0.2556 | -0.1699 | -0.2884 | -0.4293 | -0.5047 |
| ETH | -0.0607 | -0.0896 | +0.0161 | +0.2163 | -0.0421 | -0.4629 | -0.0158 |
| SOL | +0.0066 | +0.1286 | +0.0495 | +0.0774 | +0.0051 | +0.1230 | -0.3297 |
| XRP | -0.1598 | -0.2153 | -0.1688 | -0.2048 | -0.2085 | -0.1199 | -0.3277 |
| DOGE | +0.0303 | -0.0225 | +0.0448 | +0.1296 | -0.0223 | -0.0225 | +0.2169 |
| AVAX | +0.0221 | -0.0055 | +0.1181 | -0.5466 | -0.0700 | -0.0339 | -0.3124 |
| SUI | +0.0591 | +0.0988 | +0.2357 | +0.3237 | +0.0722 | +0.0897 | -0.3552 |
| NEAR | -0.0134 | -0.0552 | -0.1626 | +0.0050 | -0.0000 | +0.0057 | -0.0218 |
| BNB | -0.2540 | -0.2344 | -0.1995 | -0.0333 | -0.2590 | -0.2100 | -0.2909 |
| LINK | -0.1671 | -0.1280 | -0.2772 | -0.0962 | -0.1623 | -0.0684 | -0.3184 |

No per-symbol exceptions were selected or optimized after this result. For
example, B_structure has only 68 AVAX validation trades and substantially
worsens their mean R; it is not universally beneficial.

### BNB: overlapping trades versus events

| Gate | Validation trades | Mean R | Events | Event mean R |
| --- | ---: | ---: | ---: | ---: |
| baseline | 888 | -0.2540 | 314 | -0.3549 |
| C_dist | 731 | -0.2344 | 250 | -0.3604 |
| I_market | 624 | -0.1995 | 229 | -0.2729 |
| B_structure | 330 | -0.0333 | 108 | -0.2734 |
| A_slope | 860 | -0.2590 | 302 | -0.3622 |
| G_adx | 413 | -0.2100 | 151 | -0.2804 |
| K_support | 181 | -0.2909 | 66 | -0.3649 |

Structure's near-flat candidate-trade result does not survive as a near-flat
event average. Overlapping candidates materially affect apparent improvements.

## Recent live diagnostic

The snapshot includes 196 auto trades with signal times from 2026-10-12 through
2026-10-24. Restricting to the actual HTF rule pipeline leaves 63 trades from
2026-10-19 06:45 UTC through 2026-10-24 06:15 UTC: 49 closed and 14 still open.
Legacy trades are excluded. The live cohort is not restricted to adaptive exits.

Each gate is evaluated at the recorded signal time using then-available bars.
The table conditionally retains actual recorded paper fills and net PnL; it does
not rerun the portfolio or predict substitute trades. Open trades are excluded
from PnL. Net PnL uses the recorded costs. This is a **post-hoc diagnostic** after
observing the loss episode, not an independent forward test of these filters.

| Gate | Closed trades kept | Kept net USDT | Closed trades blocked | Blocked net USDT |
| --- | ---: | ---: | ---: | ---: |
| baseline | 49 | -1,459.06 | 0 | 0.00 |
| C_dist | 42 | -972.81 | 7 | -486.25 |
| I_market | 27 | -323.95 | 22 | -1,135.11 |
| B_structure | 11 | +91.97 | 38 | -1,551.03 |
| A_slope | 46 | -1,304.72 | 3 | -154.34 |
| G_adx | 24 | -158.70 | 25 | -1,300.36 |
| K_support | 4 | +221.36 | 45 | -1,680.42 |

Blocked winners: C_dist 1, I_market 3, B_structure 10, A_slope 1, G_adx 5,
K_support 13. Positive kept PnL for four or eleven trades is not sufficient
evidence of a durable improvement.

### BNB loss cluster

The snapshot contains **eight**, not seven, BNB long trades in the episode:
IDs 178 through 185, all closed by stop loss, totaling **-711.25 USDT** net.

- C_dist and A_slope would allow all eight. EMA distance and slope do not prevent
  this measured episode.
- I_market, B_structure, G_adx, and K_support would block all eight.
- Avoiding this one cluster does not establish general profitability. Stacked
  correlated exposure remains a separate portfolio-risk issue.

## Decision

Do not deploy any of these six gates as a proven improvement from this study.

- I_market is a reasonable first **shadow-only hypothesis**: validation event
  mean improves from -0.1796 to -0.1495, but remains negative. It would block the
  BNB cluster without removing as many recorded live trades as structure.
- B_structure is a second shadow-only hypothesis: best candidate-trade mean R
  (-0.0183), but event mean R remains -0.1711 and trade count drops about 67%.
  It needs separate per-symbol and fresh-forward evaluation, not promotion.
- C_dist adds virtually no aggregate candidate-trade improvement and slightly
  worsens event mean R. A_slope worsens both metrics. Neither blocks the BNB
  cluster.
- G_adx improves training results but worsens validation candidate-trade mean R
  from -0.0934 to -0.1152. Blocking one recent cluster is insufficient.
- K_support is the weakest aggregate result under this definition. Four kept
  live winners do not outweigh negative historical evidence and tiny sample size.

Next evidence should come from a frozen-definition shadow forward comparison
against the unchanged baseline, with actual fill costs and clustered exposure
reported separately. No shadow collector or runtime gate was enabled by this
task. Combining filters or searching thresholds would be a new experiment,
not a result already established here.

## Reproduction and checks

Run from the repository root with backend dependencies installed:

```sh
python -m scripts.futures_h4_variants run --data research-output/futures-h4 --output research-output/futures-h4/new-replay.json --workers 4
python -m scripts.futures_h4_variants live --data research-output/futures-h4 --output research-output/futures-h4/new-live.json
python -m pytest -q backend/tests/test_futures_h4_variants.py backend/tests/test_futures_history.py
```

Use a new output path to preserve old results. Only load the trusted local VPS
pickle; Python pickle is not safe for untrusted input. Replay does not fetch
fresh data automatically. Tests cover declared gates, overlapping-candidate
event aggregation, and closed-H4 window alignment without future leakage.
