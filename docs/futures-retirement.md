# Futures population retirement

Applies independently to every Futures symbol. G0, children and promoted trees
share the same retirement rules. Signals, entries, exits and qualification are
unchanged. No learner reset or database migration is required.

Replacement runs at the existing hourly evolution boundary, only when the active
population reaches its cap and a valid, unique child has been prepared. If no
tree qualifies for retirement, evolution stops rather than removing a good tree.
Failed or duplicate mutations do not remove an existing tree.

## Forward evidence

Use at least 50 distinct resolved teaching samples with a valid filled entry,
positive risk, finite net R and an outcome available by the current cutoff.
Cancelled, unfilled, excluded, duplicate-key and future-labelled samples do not
count. Replay can still rank parents, but cannot qualify a tree for retirement.

Retirement priority is negative mean forward R, then cumulative drawdown of at
least 10R, then confirmed redundancy. Within each group, lower mean R comes
first, followed by larger drawdown and stable strategy ID. Drawdown starts from
zero and follows label-availability order; it is **not account drawdown percent**.
An otherwise profitable tree can retire at the 10R threshold.

## Redundancy

Only compare identical normalized entry/exit configuration: entry policy, exit
policy/parameters/profile, target R and stop buffer. In the shared observed
lifetime require 50 closed samples per tree, 50 matching `(candle_time, side)`
keys, and intersection/union at least 90%. The retained tree must improve mean
R on matched keys by at least 0.10R, have no greater drawdown in that interval,
and not itself fail the forward R/drawdown thresholds.

Every matched `(side, regime)` group needs at least 10 samples, consistent regime
labels and no lower mean R for the retained tree. Any unmatched group with
positive mean R protects the retiring tree's distinct contribution. Missing
evidence means no redundancy retirement. This measures overlap of resolved
teaching trades, not every possible signal or statistical proof of equivalence.

## Safety and audit

Never retire trees with an open teaching sample, an open Paper position, a
pending Paper order, or the current decision selected for the next Auto step.
Position/order ownership is read under the shared wallet transaction lock.
Unknown or conflicting active ownership disables retirement for that tick.
No position is closed or order cancelled to create room.

Archive genomes, ancestry, history and retirement metadata; archived seed
fingerprints cannot be seeded again. API learning adds `retired_strategies`
without changing `retired_paper`. The Learning page shows generation, reason,
time, forward count, mean R, drawdown R and the retained comparison tree with
overlap evidence. Old archives without metadata remain visible as legacy rows.

Thresholds are fixed for this version; no automatic tuning or restoration.
Historical forward evidence is reused. These experimental replacement rules do
not establish profitability of any tree or the combined portfolio.

Checks:

```sh
python -m pytest backend/tests/test_futures_retirement.py backend/tests/test_futures_learner.py -q
```

Then run the full backend suite, Ruff and frontend production build.
