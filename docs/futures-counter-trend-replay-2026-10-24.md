# Futures counter-trend replay - 2026-10-24

Research only. No DB, VPS, G0 genome, or live rule is changed.

## Design (fixed before the reported run)

Three H1 setup + M15 trigger templates, same adaptive exits and execution costs as
the G0 replay: A `sweep`/H1 + `pin_bar`/M15; B `bb_pierce`/H1 + `engulfing`/M15;
C `rsi_divergence`/H1 + `pin_bar`/M15.

- `counter`: H4 ADX14 < 20 AND H4 close on the opposite side of EMA50 from the trade.
- `aligned`: H4 ADX14 < 20 AND H4 close on the same side of EMA50.

The H4 gate is a research-only root matcher; the serialized H4 `trend` leaf is a
schema placeholder. The first pair of arms (`counter`, `aligned`) uses the unchanged
adaptive exit. A second pair (`counter_mr`, `aligned_mr`) uses the same entries with a
mean-reversion exit: target = mean of the last 20 closed H1 closes (setup skipped if
reward < 1R; the legacy 1.5R barrier filter is bypassed), unchanged initial stop,
and the native `time_stop` (8 h, exit if progress < 0.5R). Parameters were fixed before
the run. A first `_mr` run was discarded because the legacy filter still rejected
setups before the new target applied; it is kept only as
`counter-mr-invalid-legacy-barrier/`. The adaptive arms reproduce exactly in the final run.

Same data, split, fees (8 bps), slippage (2 bps) and funding as
`docs/futures-h4-g0-replay-2026-10-24.md`. Runner: `scripts/futures_counter_replay.py`;
results (git-ignored): `research-output/futures-h4/counter-adx/`.

A first exploratory run used an H4 `compression` gate instead and was discarded
because that gate did not test the stated counter-trend idea. It is kept only as
`counter-exploratory-compression/` and is not reported as a result. Gate choice
was changed once after seeing results, so a further selection bias applies.

## Results

| Arm / partition | Trades | Mean R | PF | Win | Event mean R (CI95) |
| --- | ---: | ---: | ---: | ---: | --- |
| counter train | 278 | -0.113 | 0.820 | 46.0% | -0.116 (-0.322..0.091) |
| counter validation | 140 | -0.269 | 0.616 | 37.1% | -0.246 (-0.494..0.002) |
| aligned train | 63 | -0.199 | 0.675 | 41.3% | -0.199 (-0.484..0.086) |
| aligned validation | 25 | -0.934 | 0.037 | 4.0% | -0.934 (-1.193..-0.676) |

Counter validation by template: A 49 trades -0.371R, B 83 -0.222R, C 8 -0.130R.
By symbol only AVAX was positive (15 trades, +0.582R); BTC -1.028R (8), SOL -0.694R (13).
No template met the engine's prior-eligibility test.

### Mean-reversion exit (counter-mr2)

| Arm / partition | Trades | Mean R | PF | Win | Event mean R (CI95) |
| --- | ---: | ---: | ---: | ---: | --- |
| counter_mr train | 278 | -0.083 | 0.847 | 44.6% | -0.092 (-0.282..0.098) |
| counter_mr validation | 144 | -0.281 | 0.534 | 39.6% | -0.260 (-0.479..-0.041) |
| aligned_mr train | 67 | -0.272 | 0.554 | 43.3% | -0.272 (-0.515..-0.030) |
| aligned_mr validation | 23 | -0.631 | 0.250 | 21.7% | -0.631 (-1.030..-0.233) |

`counter_mr` validation exits: 78 stop, 37 target, 29 time stop. Templates:
A 54 trades -0.352R, B 82 -0.229R, C 8 -0.340R. Longs -0.185R, shorts -0.375R.
Only XRP and AVAX were marginally positive (+0.046R, 14 trades each). No template
passed prior eligibility. Results: `research-output/futures-h4/counter-mr2/`.

## Interpretation

The mean-reversion exit improved train slightly (-0.113R to -0.083R) but validation
did not improve (-0.269R to -0.281R; CI excludes zero on the negative side). Counter-trend entries lost in both partitions and are worse in validation. They
beat the aligned control, but that control is tiny (25 validation trades) and also
loses, so it does not show an edge. One mean-exit/time-stop parameter set was tested; other range definitions,
targets, and stops are untested. Sample sizes are small, validation is reused data, no
portfolio effects are modeled. Do not deploy.
