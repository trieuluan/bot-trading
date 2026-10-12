# Higher-timeframe screen v1 (rules fixed before results)

Four candidates only: existing breakout and retest on closed 1h and 4h bars.
No EMA filter, new indicators, parameter tuning, UI, live observers or Auto Paper changes.

- Breakout: close beyond preceding 20-bar high/low; stop opposite extreme of
  preceding 5 bars; target 2R. Long and short.
- Retest: same preceding 20-bar range; wait at most 10 signal bars after breakout
  (10h on 1h / 40h on 4h); require retest and later directional confirming close.
  Stop retest structure; target 2R. Existing implementation unchanged.
- Aggregate contiguous 15m MEXC candles into UTC-aligned, fully closed 1h/4h bars.
  Emit only at signal-bar close. Enter at next 15m open (also next signal-bar open).
  Keep original simulator on 15m, not 1h/4h OHLC: SL/TP, settlement funding and
  equity/DD still evaluated every 15m. Ambiguous SL/TP within 15m uses stop first.
- Same 1,600 15m bars warmup and non-overlapping 30-day windows for all candidates;
  10,000 USDT fresh capital each window; 1% risk, 1x notional cap, contract lot sizing,
  fees 5 bps + slippage 2 bps each side, actual funding/fair prices. Close at window end.
- Primary screen: 36 x 30 days of history if MEXC returns complete data, separate cache.
  If unavailable, report data limitation and run existing 12-month cache as preliminary
  only. Do not silently fill gaps, substitute exchanges or loosen criteria.
- All criteria unchanged: >=100 executed trades, pooled PF >1.3, >=60% positive windows,
  worst-window DD <10%. Too few trades means insufficient evidence, not proof of failure.

Multiple comparisons and partly previously examined history mean this is exploratory.
A passing screen still needs independent out-of-sample/forward evidence. Results do not
identify whether fees, signal quality or market regime caused losses without further tests.
No automatic live promotion. Current contract sizing metadata is assumed across history;
order-book execution, changing spread and historical contract-rule changes are not modelled.

```sh
PYTHONPATH=. python scripts/higher_timeframes.py
# Fallback only if 36-month market/funding history is incomplete:
PYTHONPATH=. python scripts/higher_timeframes.py --months 12 \
  --cache /tmp/walkforward-cache.json --output /tmp/higher-timeframes-12m-v1.json
```
