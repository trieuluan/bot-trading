import assert from 'node:assert/strict';
import { summarizePaperTrades } from '../src/lib/paper-position.ts';

const base = { entry: 100, stop: 90, quantity: 1, entry_fee: 1, exit_fee: 1, funding_cost: 0 };
const trades = [
  { ...base, origin: 'auto', net_pnl: 20, funding_cost: -1 },
  { ...base, origin: 'manual', net_pnl: -10, funding_cost: 2 },
  { ...base, origin: 'auto', net_pnl: null, entry_fee: 99 },
];
const total = summarizePaperTrades(trades);
assert.deepEqual(total, { count: 3, closed: 2, wins: 1, net: 10, fees: 5, meanR: 0.5 });
assert.deepEqual(summarizePaperTrades(trades.filter((trade) => trade.net_pnl > 0)), {
  count: 1,
  closed: 1,
  wins: 1,
  net: 20,
  fees: 1,
  meanR: 2,
});
assert.equal(summarizePaperTrades(trades.filter((trade) => trade.origin === 'manual')).net, -10);
assert.deepEqual(summarizePaperTrades([]), {
  count: 0,
  closed: 0,
  wins: 0,
  net: 0,
  fees: 0,
  meanR: null,
});
assert.equal(summarizePaperTrades([{ ...base, net_pnl: 0, stop: 100 }]).meanR, null);
assert.equal(summarizePaperTrades([trades[2]]).fees, 0);
assert.deepEqual(summarizePaperTrades(trades), total);
console.log('Paper history summary checks passed.');
