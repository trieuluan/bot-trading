# Futures audit runtime, 2026-11-11

## Behavior

- Population audit: 3,600 seconds. Pair draft: 1,200 seconds. Native universe: 3,600 seconds.
- CPU remains 0.50; no additional parallel workers.
- Failed audits wait per strategy, not per symbol: 300 seconds, 1,800 seconds, then
  86,400 seconds after the third failure. A new retry cycle starts after that day.
  Non-retryable failures wait a day. Untested trees across symbols take priority
  over due retries; interrupted jobs retain their snapshot without spending an attempt.
- Retry identity includes engine, genome fingerprint and birth time. Compatible
  retries retain the original cutoff/window. Incompatible snapshots are discarded,
  not combined with a new evaluation. Forward samples and old reports are untouched.
- Final progress survives timeout, including phase, counters, elapsed time, timeout
  budget, data/replay wall times, attempt and next retry time. Heartbeat does not
  bypass the wall-clock deadline or hide a dead child process.

## Optimization and evidence

Replay shares per-bar history slices. Its evaluation-local aggregate cache additionally
reuses slices that contain the exact same immutable Candle objects in the same order.
The key contains every participating candle identity, not just symbol/time or endpoints.
Incomplete edge buckets are excluded from that key, just as aggregation excludes them;
`until` bounds the eligible closed bucket. Input data is validated before replay.
The default live/Forex cache behavior is unchanged.

Frozen BTC strategy: `rule_tree:02952706c012`, cutoff `1794334500`, 90 days,
8,640 evaluation candles plus 1,600 warmup candles, original and signal-v2 arms,
real trade/fair/funding and nine benchmark series. Offline Docker local, same
dataset and sequential full-window runs, without profiler:

| Measurement | Baseline | Optimized |
| --- | ---: | ---: |
| Cache load, seconds | 0.392 | 0.374 |
| Replay, seconds | 159.696 | 131.755 |

Replay time falls 17.5% (1.21x speedup). These are local measurements, not a VPS
runtime promise. Profiling a separate 384-bar baseline found aggregate calls
accounted for 18.807 of 19.467 seconds; profiled and unprofiled times must not be
compared as a speedup claim.

Full report equality was asserted, including funnels, resolved trade records,
train/validation net R, drawdown, prior eligibility and dataset digest:
`4fc7a092a3cc81a1bf662b2cce608ddb7bc6ddd123dd297fbd78c82e85f4ed8f`.
Original arm: 2 closed trades; paired v2 arm: 0. No profitability conclusion follows.

## Reproduce and validate

Export the audit's frozen snapshot and public cache files; do not export credentials.
Use complete real inputs; the benchmark raises on incomplete candles/funding bounds
or OI-dependent genomes without an observation export. It does not read learner DB,
call the exchange, attach results, or grant execution authority.

```sh
python -m scripts.futures_audit_benchmark --snapshot /tmp/snapshot.json \
  --cache /tmp/audit-cache --symbol BTC_USDT --output /tmp/audit-result.json
```

Use `--history-source` with the preserved baseline `futures_history.py` for an
identical old replay, `--bars 384` for a short diagnostic, and `--profile` for cProfile.
Keep the corresponding baseline strategy source when benchmarking future cache changes.
Compare the entire `report` object, excluding outer timing fields.

Regression coverage includes timeout budgets, latest progress on exit, per-tree
backoff, cross-symbol priority, interrupted snapshots, identity changes, aggregate
cutoffs/gaps/interior OHLC/source separation, and exact replay report parity.

Deployment updates API, replay worker and frontend only. Do not restart trading bot,
Forex feed, learner or PostgreSQL; do not reset any candidate or sample data.
