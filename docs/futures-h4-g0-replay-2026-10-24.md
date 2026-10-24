# Futures G0 H4 consensus replay - 2026-10-24

## Matched experiment

Research only. No learner DB, wallet, forward cursor, VPS service, or deployment
is changed by this experiment.

Compare all 26 current `RULE_SEEDS` templates on each of the ten futures symbols:

- `ema_only`: identical current G0 genome, replacing only its first root child
  with the legacy `H4 trend` leaf (close versus EMA50).
- `consensus`: current G0 unchanged. H4 requires EMA50 direction AND confirmed
  swing structure AND market alignment, all in the same direction.

Market alignment requires BTC direction and at least five of ten symbols to
agree with the trade direction, with all nine peer histories present and their
last 50 closed H4 timestamps aligned. Swing structure requires both the last
two confirmed highs and the last two confirmed lows to progress in direction.
H4 uses the same rolling 1,600 M15 bars as runtime; incomplete H4 candles are
not used. The runner memoizes H4 predicates only within each decision timestamp
and each fixed symbol/context; parity against runtime is tested.

H1/M15 conditions, adaptive exit policy, exit parameters, and SL/TP algorithms
are identical across arms. Entry/exit prices and realized R can still differ
because filtering changes signal times and subsequent trade availability.
There is no evolution, candidate selection, extra exit-policy seed, or tuning
after the results. This is not the earlier evolved-candidate six-filter study.

## Data and execution

- Ten MEXC USDT pairs: BTC, ETH, SOL, XRP, DOGE, AVAX, SUI, NEAR, BNB, LINK.
- Signal interval: 2025-11-12 10:00 UTC to 2026-10-18 09:45 UTC, exclusive.
- Chronological 70/30 split: 2026-07-08 09:45 UTC; warmup: 1,600 M15 bars.
- Fee: 8 bps per side. Adverse slippage: 2 bps per side. Historical settled
  funding included. No order-book or live fill calibration.
- Signals use closed candles; replay entry is next M15 open. Intrabar conflicts
  are stop-first, following the existing replay engine.
- One active sample per template/arm. Open samples are excluded at the split
  and dataset end; invalid entry geometry is excluded by the existing engine.
- Historical OI is absent. OI-dependent templates fail closed; zero trades are
  not evidence of their profitability or lack thereof.
- Macro context uses the bundled official-release snapshot for 2025-2026,
  not a historical revision archive.
- Trusted local candle cache SHA-256:
  `aac0232e9fabe13d01211028083c75fcb5dc16eb2ab2f20b134850d75e608fdc`.

```sh
/private/tmp/bot-trading-candle-replay-venv/bin/python -m scripts.futures_g0_replay \
  --data research-output/futures-h4 \
  --output research-output/futures-h4/g0-consensus --workers 5
```

Machine results are git-ignored under `research-output/futures-h4/g0-consensus/`.
`manifest.json` pins all G0 genomes, source cache hash, and code hashes. Each
symbol has an atomic checkpoint containing the engine report, resolved samples,
exclusion counts, and runtime. `summary.json` aggregates both arms. An existing
checkpoint is reused only with identical inputs/code.

## Interpretation limits

The historical dataset has already informed the choice of structure and market
confirmations. The chronological validation partition is reused, not untouched
out-of-sample evidence. Fixed G0 removes the evolved-genome cohort selection of
the preceding study but does not remove this post-selection bias.

Replay does not reproduce live adaptive selection, concurrent position limits,
wallet sizing, leverage, margin, liquidation, or portfolio risk. Total R sums
overlapping research samples and is not an attainable account return.

Events group the same symbol, signal timestamp, and side, averaging overlapping
template results. Events remain correlated across time and symbols. Reported
event confidence intervals are exploratory, not proof of statistical significance.
Trade-count changes include changed position scheduling, not only rejected gates.

No claim of profitability or production qualification follows from this replay.

## Results (completed 2026-10-24)

Resolved trades only. Events deduplicate same symbol/time/side; CI is exploratory.

| Arm / partition | Trades | Mean R | PF | Win | Events | Event mean R | Event CI95 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| ema_only train | 4,942 | -0.147 | 0.742 | 40.3% | 2,608 | -0.153 | -0.238..-0.068 |
| consensus train | 1,727 | -0.130 | 0.758 | 41.5% | 860 | -0.142 | -0.265..-0.019 |
| ema_only validation | 2,073 | -0.049 | 0.908 | 44.4% | 1,067 | -0.109 | -0.258..0.040 |
| consensus validation | 469 | +0.069 | 1.144 | 49.7% | 217 | +0.044 | -0.275..0.364 |

Consensus cut validation trades by 77.4% and train trades by 65.1%. Train stays
negative. No template passed the engine's prior-eligibility test in either arm.

Validation by side: consensus long 317 trades, +0.272R, PF 1.761; short 152
trades, -0.355R, PF 0.516. Train: long 646 trades, -0.310R; short 1,081 trades,
-0.022R. The side result is therefore not stable across partitions.

Validation by symbol (trades, mean R): BTC 50/+0.105, ETH 38/+0.674, SOL 49/+0.296,
XRP 50/-0.165, DOGE 34/+0.041, AVAX 17/-0.495, SUI 40/+0.759, NEAR 68/-0.154,
BNB 45/-0.080, LINK 78/-0.179. Most per-symbol samples are small and not selected
or tuned. Templates 17 and 20 produced zero validation trades in both arms.

Only template 18 had at least 30 trades and positive mean R in both train and
validation for consensus; no ema_only template did. This is exploratory and was
not used to alter G0.

Conclusion: the 3/3 gate improves validation and slightly improves train versus
matched EMA-only, but remains negative in train and the validation CI includes
zero. Treat as diagnostic, not qualification or deployment evidence.
