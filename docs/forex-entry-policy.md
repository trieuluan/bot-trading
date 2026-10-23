# Forex virtual entry pilot

V3 keeps its 56 trees, IDs, history and evolution. A separate, locked, atomic
ledger captures 56 V4 counterparts once per account/symbol. It never inherits
fitness. The fixed counterpart cohort does not evolve; the main population can
mutate entry nodes only when the pilot flag is enabled.

`FOREX_ENTRY_PILOT_ENABLED=false` is the default. Existing demo execution flags,
broker-confirmed demo identity and account control remain mandatory. Nothing
enables execution locally. Limit/Stop are virtual quote thresholds, not broker
pending orders or fill-price guarantees. Only broker-confirmed fills are real
execution results.

## Safety and rollback

- Reserve the whole exit plan under the account execution lock, including
  uncertain legs, within 66 account / 6 symbol positions.
- Bid/ask quotes must advance in time and remain within two seconds. A gap,
  disconnect, invalidation, expiry or crossed first quote cancels waiting.
- One durable account/symbol/setup/side key chooses one branch. Failed preflight
  ends that setup; no fallback or second branch is allowed.
- Restart cancels waiting, reconciles submitting/submitted/uncertain states and
  retains reservations until broker outcome is known.
- To roll back, disable the pilot flag, recreate API/feed, cancel waiting and
  reconcile submitted intents. Never downgrade away the audit table or close
  positions merely because the pilot is disabled.

## Offline replay

`python scripts/forex_entry_replay.py input.json output.json`

Input contains `as_of` (Unix seconds), `signals` (all captured signals, not only
resolved Market trades), and `candles` keyed by symbol, timeframe and bid/ask.
Each `signals` item contains immutable `signal`, `condition` and `costs`
snapshots. `signal` includes `htf_structure`, its captured SL/TP and trigger time.
`condition.conversion` must be the historical verified conversion snapshot, not
a rate fetched today. `costs` contains `commission_per_unit` and `slippage_bps`.
Candle objects contain `time`, `open`, `high`, `low`, `close`.

The report evaluates Market, setup-mid Limit, trigger-break Stop and hybrid for
every input signal. It prefers existing bid/ask M1; M15 fallback is explicitly
labelled by `resolution_seconds`. Partial M1 history is not silently filled with
M15. Future bars are ignored. Missing bars are excluded. Ambiguous branch or
entry/exit ordering is reported separately with branch scenarios and available
return bounds. Stop and target remain frozen; OHLC opens are never rewritten.
Spread at an intrabar touch is approximated by the smallest bar's opening
spread and explicitly disclosed. All replay output is offline research, never
forward fitness, broker history, or evidence of execution permission.
