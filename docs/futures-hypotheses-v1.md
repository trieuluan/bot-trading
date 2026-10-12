# Futures hypotheses v1: frozen before first run

Research only: no live observers, orders, UI changes, ML or parameter search.
Reuse cached MEXC BTC_USDT perpetual 15m, fair prices and realized funding.
Previously examined history is exploratory data, not a new independent holdout.

## Fixed rules

- Mean reversion: 4h efficiency ratio (absolute net change / sum absolute changes,
  20 intervals) <= 0.30 defines range. Last closed 1h close outside Bollinger
  mean20 +/- 2 population standard deviations of the preceding 20 1h closes.
  First following 15m closes back across that band with directional body.
  SL beyond extremes of touch and confirmation plus 0.1 ATR14 15m; target prior mean.
- Liquidity sweep: 4h EMA50 direction. Latest 15m wick crosses prior 20 fully
  closed 1h range, closes strictly inside with directional body, aligned with trend.
  SL beyond wick plus 0.1 ATR14, TP 2R. Range excludes candidate 15m even at hour close.
- Funding filter: latest realized settlement strictly before confirmation close,
  no older than its funding cycle. Rate <= -0.0003 for long, >= 0.0003 for short.
  Require 4h EMA50 alignment and 15m close beyond preceding 10-bar extreme.
  SL beyond last 5 prior bars and confirmation plus 0.1 ATR14; TP 2R.
  These are past settled rates, NOT historical predicted funding or upcoming rates.

Only fully closed bars. No intermediate/pending signals, no parameter tuning after results.
Signals enter next 15m open. Existing simulator: 1% risk, 1x notional cap, exchange
lot constraints, 5 bps fees + 2 bps slippage per side, realized funding on fair-price
settlements, stop first for ambiguous candles, fresh 10,000 USDT each 30-day window.
Positions close at window end. This omits order-book depth, dynamic spread, leverage
liquidations, and execution latency. Same 1,600-bar warmup and periods for all candidates.

## Screening (unchanged; not a statistical significance test)

All must pass: >=100 trades, pooled PF >1.3, >=60% positive windows, worst window DD <10%.
Re-run existing MTF as comparator. Save each period, config, actual UTC range and cache hash.
Multiple hypotheses and repeated use of history require a fresh holdout/forward validation
even if one passes. Do not automatically promote any result into Auto Paper or an observer.

Run (cached inputs only, no API calls):

```sh
PYTHONPATH=. python scripts/futures_hypotheses.py \
  --cache /tmp/walkforward-cache.json --output /tmp/futures-hypotheses-v1.json
```
