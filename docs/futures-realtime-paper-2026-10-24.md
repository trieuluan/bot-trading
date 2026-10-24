# Futures Paper realtime quotes

- Worker subscribes to MEXC `sub.ticker` for each supported symbol using the
  existing `websockets` dependency. Published ticker cadence is approximately
  one second, not every trade or every order-book change.
- Each valid ticker updates the wallet and checks existing SL/TP immediately
  under the shared wallet lock. Long exits use bid, short exits use ask, with
  existing fees/slippage. No exchange orders are submitted.
- The fast path does not call candle-history reconciliation. Historical stops,
  trailing/exit-policy updates, and REST polling remain independent worker jobs.
- Invalid/crossed quotes, wrong symbols, timestamps older than five seconds or
  more than one second ahead are rejected. Freshness is checked again after
  obtaining the wallet lock. Older REST replies cannot replace newer WS quotes.
- JSON heartbeat runs every five seconds. Ten seconds without valid ticker
  data reconnects with bounded exponential backoff. A per-symbol watchdog checks
  every five seconds but skips the REST ticker while WS is healthy. Disconnects
  reset health immediately; five seconds without fresh advancing quotes also
  enables fallback on the next watchdog pass. Recovery needs three committed,
  advancing WS quotes spanning at least two seconds before REST ticker polling
  stops again. Reconnect alone, pongs, invalid, duplicate or uncommitted ticks
  do not establish health. Health resets on process restart.
- Contract bootstrap, funding metadata and candle history still use REST when
  needed, even with healthy WS. Disabling REST ticker polling does not disable
  those endpoints. REST failures do not invalidate a fresh WS quote.
- Frontend candles still use direct MEXC `push.kline`. The chart badge shows
  estimated timestamp-to-browser delay and quote age; negative delay is labeled
  clock skew. Five-second-old candle data is no longer labeled LIVE.
- A separate SL/TP badge shows the backend quote source and age. Wallet/exit
  display still travels through existing SSE snapshots (two-second selected
  cache and two-second emission loop), so display is not instantaneous.
- H4/H1/M15 strategy signals and pending-entry fills still use closed candles.
  This change does not turn pending limits into an intrabar matching engine.
- Paper remains a sampled-price simulation. Short excursions between ticker
  messages can still be missed; historical candle recovery is not exact fills.

Protocol reference: https://mexcdevelop.github.io/apidocs/contract_v1_en/#ticker

Checks:

```sh
python -m pytest backend/tests/test_mexc_stream.py backend/tests/test_paper.py backend/tests/test_worker.py -q
node frontend/scripts/check-mexc-stream.mjs
npm --prefix frontend run build
```
