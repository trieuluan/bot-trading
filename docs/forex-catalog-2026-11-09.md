# Demo Forex catalogue cutover: 2026-11-09

Catalogue `independent_entries_20261109_v1` replaces the active V4 cohort with
56 independent trees per symbol, initially seeded from 14 setup/exit combinations
and four entry policies. H4 context, H1 setups and M15 confirmations remain.
No bundle-level fitness, four-way completion gate or policy-slot remapping remains.
Each tree needs 10 valid outcomes of its own; positive fitness makes it eligible
as a parent. Children inherit and can mutate entry policies and parameters, so
the initial equal policy counts are not maintained as an evolution constraint.
NR7, Asian range breakout and London opening-range breakout join the root
catalogue; FVG and MSS no longer appear in initial roots. Existing mutation and
crossover remain enabled and can still create other supported families.

There is no global London/New York overlap restriction. Session-specific setups
use their own clocks; London opening times use Europe/London DST. Broker session,
spread, data freshness and risk guards remain unchanged.

These production AST entry/exit rules are not identical to the research sweep
configurations. Passing tests or historical sweep results do not establish that
this cohort is profitable. Measure new demo outcomes separately from old samples.

## Cutover

Verify broker-confirmed demo identity. Pause account execution, cancel waiting
intents and reconcile bot-owned positions using existing execution management.
Do not close external positions. Stop exness-feed before running:

```sh
python -m scripts.forex_demo_catalog_cutover --apply
```

The script refuses a non-flat account, enabled execution or reserved intents.
It validates existing ledgers, archives old bytes as gzip with a SHA-256 audit,
then atomically replaces each active ledger. Repeating a completed symbol is a
no-op; interruption between symbols can be resumed. Historical broker/DB trade
records remain. Old ledger samples, members and assignments leave the active
cohort; compressed recovery archives remain outside the active ledger paths.

Rebuild and restart API/exness-feed with matching code, verify every catalogue
and archive, then re-enable demo execution. Do not restart futures services.
