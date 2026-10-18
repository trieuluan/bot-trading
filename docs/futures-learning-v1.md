# Futures learner v1

MEXC BTC/USDT perpetual, closed 15m bars only. Exness data, policies and orders are
not shared. Run migration `0016`, restart API and `bot.worker`, then open
**MEXC Futures > Nghien cuu > Bot hoc**. Selecting the MEXC data source starts the
independent shadow loop, even while Auto Paper is off. Missing data blocks learning.

## Strategy pool

Eight seed families: breakout, MTF retest, mean reversion, liquidity sweep,
funding filter, retest, squeeze breakout and trend pullback. The first five
reuse the existing rules; the three additions are experimental, not proven edges.
One initial mutation per seed is attempted; boundary/duplicate mutations may
produce fewer than sixteen distinct candidates. Immutable genome IDs include
bounded lookback, stop buffer, target R, range efficiency and funding threshold.

Contextual UCB chooses among signals by direction and 15m efficiency regime
(range/uptrend/downtrend). Only resolved labels available at that decision time
affect scores. Each genome has at most one open teaching sample. Entries use
the next bar's open, 5 bps fees and 2 bps slippage per side; stops win ambiguous
bars. Existing positions pay settled funding at the fair-price open; new entries
do not pay that opening settlement. Teaching labels time out after 48h and are
per-unit R, not exchange fills or a portfolio return.

Every six hours, parents with at least twenty resolved samples may produce one
child by crossover and mutation. Each child has a new future birth boundary,
parent IDs and generation number. Parents never change. Population stops at
32; v1 does not discard evidence or replace old genomes to keep searching forever.
This is bounded rule evolution, not unrestricted strategy/code invention.

## Forward gate

Teaching scores cannot promote a candidate. Starting strictly after its frozen
birth boundary, evaluate complete, non-overlapping thirty-day windows with the
existing futures simulator: 1,600 warmup bars, fresh 10,000 USDT per window,
1% SL risk, 1x notional cap, contract lot constraints, fees, slippage and realized
funding on historical fair prices. Window-end positions close. This validation
does not use the teaching-label 48h exit; it independently evaluates the actual
SL/TP rule. Previously examined pre-birth backtests cannot count toward the gate.

All checks must pass: at least 100 trades, pooled PF strictly above 1.3,
at least 60% positive windows and worst window mark-to-market DD strictly below
10%. No-loss PF is shown separately rather than inventing a finite ratio.
Data gaps or mismatched fair candles block validation, not silently pass it.

Passing only makes a candidate eligible. The user explicitly approves it in
the table. Later failed validation removes approval. Auto Paper defaults to its
legacy breakout selector; change to **Auto: bot hoc** only while Auto Paper is
stopped, then enable Auto Paper separately. Learner mode never falls back to
legacy breakout when no qualified approved signal exists. Existing paper-wallet
risk, freshness, daily limits, single-position checks and stop monitoring remain.
Promoting a rule does not enable trading or submit an order.

## Immediate paper exploration

Select **Auto: hoc thu Paper** (`auto_mode: paper_learning`) while Auto Paper is
stopped, then enable Auto Paper separately. This opt-in selector may use unapproved
candidates without changing their forward qualification or approval. It ranks all
current signals by contextual UCB, exploring unseen strategy/direction/regime
contexts first. Equal scores use a reproducible candle-seeded choice rather than
always choosing the first registered strategy. Scores still use resolved shadow
labels, not a claim that paper-wallet PnL trains the selector directly.

No valid signal means no entry; there is no legacy fallback. Fresh closed-bar
decisions, quotes, funding, one-position cap, sizing, SL/TP and daily limits still
apply. Every opened decision records the strategy, selector mode, regime and
selection reason, with an explicit unverified-experiment label. Paper exploration
does not promote rules, route exchange orders or bypass the approved-only mode.

GET `/api/workspaces/mexc_futures/learning` reports state. POST `/config` takes
`enabled` and `auto_mode` (`legacy`, `learner` or `paper_learning`). POST `/promotion` takes a known
`strategy` ID and `promoted` boolean; qualification is rechecked server-side.
State is stored transactionally in `futures_learning_state` under the existing
paper-wallet lock, with a persisted candle cursor for retries/restarts.

## Limits

Public MEXC reads only; no live exchange execution or API keys. Real spreads,
depth, latency and liquidations are not modeled by the validation simulator.
Individual candidate gates do not validate the adaptive selector as a profitable
combined portfolio. Multiple comparisons can still overfit; continue forward
paper observation. The JSON teaching ledger grows over time; archive or move
samples into indexed rows before operating indefinitely. Missing long-backlog
history fails closed rather than inventing entry fills.

Check: `/tmp/v/bin/pytest backend/tests/test_futures_learner.py` (or the project's
Python environment). Production does not require that temporary environment path.

## Experimental exit-policy learning

Every family keeps its original fixed-level seed. Only `squeeze` also gets a `trail`
experimental seed: in a 340-day BTC replay `trail` was marginally ahead of `fixed` on
`squeeze` validation (thin sample, train still negative) and equal or worse elsewhere.
`breakeven`/`structure` remain valid for stored genomes but are no longer seeded or mutated. Optional `exit_policy` participates in the genome hash, crossover and mutation;
old genomes and their scores retain their identities. New candidates start with no
validation windows or promotion. The policies are:

- `fixed`: original SL/TP, unchanged.
- `breakeven`: when an M15 close reaches 1R, tighten SL to cover entry fee, estimated
  exit fee, settled funding and adverse exit slippage. R is always the initial fill
  to initial SL distance, never recalculated from a moved stop.
- `trail`: same breakeven; from a 1.5R M15 close, tighten against the last three
  closed M15 lows/highs plus a 0.1 ATR14 buffer. Never widen SL.
- `structure`: same breakeven; at signal time cap TP before the nearest closed
  H1/H4 Donchian20 or confirmed swing barrier, with 0.1 M15 ATR14 buffer. Never
  extend the strategy's original TP. Require both frames to have 20 closed bars;
  reject the signal if the capped reward is below 1.5R at signal price.
- `adaptive`: initial SL uses the five-bar M15 swing plus 0.25 ATR14, with a
  minimum distance of one ATR14, then adds the genome's `stop_buffer`. Initial
  TP is capped at `max(2, target_r)` times that final risk. Before entry, cap it
  at the nearest prior M15/H1 range extreme or confirmed swing, buffered by
  0.1 M15 ATR14. Reject a barrier below 1.5R instead of ignoring it; use the R
  cap when no barrier exists or the barrier lies beyond it. H1 aggregation drops
  incomplete hours; management retains 100 M15 bars to cover 20 closed hours.
  While open, only weak closed candles can pull TP closer, never farther, and
  only if the new target is still ahead of price and at least 1.5 initial R.
  Strong candles keep their target. Reaching 1R alone does not take partial
  profit: a weak close is also required. Partial exits are rounded to contract
  steps and recorded once; the remaining position keeps breakeven/trailing SL.

Mutation toggles `exit_policy` between `fixed` and `trail` for `squeeze` only.
Stored variants outside that policy remain visible as paused, preserving their
genomes and evidence. They cannot supply new signals, Auto Paper entries, parents,
promotions or new replay/validation windows. Open paper trades and teaching samples
still finish with their snapshotted exit rules. CAP=32 applies to active variants;
archived variants do not consume active slots. Previously scored genomes cannot be
reborn after eviction with a new birth date and their old performance attached.
Forward teaching, historical replay and qualification backtests share the same stop
manager. Check the prior stop/target first, then move SL at candle close for the NEXT
bar; never use the same bar's high/low to retroactively trigger a new stop.

Auto Paper snapshots the selected policy and initial risk into the new trade's
monitoring JSON. Restarts preserve those values. Existing and manual trades remain
fixed; no live exchange orders are enabled. The chart receives the updated SL via
its existing position refresh. Learning cards identify the exit policy and explain
its thresholds. Fees are simulation settings, not a verified exchange-account rate.
Breakeven is an estimate: gaps, spread and future funding can still produce a loss.
These policies are experimental; no improvement in real-market returns is claimed.

## Ví dùng chung đa cặp (Unified Cross-Symbol Paper Wallet)
- Số dư tiền mặt thực tế tập trung tại `paper_wallet` id = 1 (Master Cash Ledger).
- Các dòng id = 2..10 lưu trữ `quote`, `contract`, `funding_cursor` đặc thù của từng cặp.
- Ký quỹ dùng (`used_margin`) và lợi nhuận chưa chốt (`unrealized_pnl`): tổng hợp trên toàn bộ vị thế mở của 10 cặp theo giá mark thời gian thực của từng cặp.
- Sức mua khả dụng: `available = max(0, master.balance - total_used_margin)`.
- Giới hạn lỗ ngày `daily_loss_percent` được kiểm soát theo biến động equity của ví chung.
