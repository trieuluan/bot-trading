import assert from 'node:assert/strict';
import { estimatedPaperPnl, tradePartialProgress } from '../frontend/src/lib/paper-position.ts';

const leg = { quantity: 3, gross: 14, entry_fee: 0.5, exit_fee: 1 };
const trade = {
  quantity: 7, entry: 100, entry_fee: 0.7, funding_cost: 0.2,
  side: 'long', exit_time: null,
  monitoring: { original_quantity: 10, partial_exits: [leg] },
};
assert.equal(tradePartialProgress({ ...trade, monitoring: null }), null);
assert.equal(tradePartialProgress({ ...trade, monitoring: { partial_exits: [] } }), null);
const progress = tradePartialProgress(trade);
assert.equal(progress.closedPercent, 30);
assert.equal(progress.remainingPercent, 70);
assert.equal(progress.realizedNet, 12.5);
assert.equal(estimatedPaperPnl(trade, 101, 0), 6.1);
assert.equal(estimatedPaperPnl(trade, null, 0), null);
assert.equal(tradePartialProgress(trade).realizedNet, 12.5);
const twice = { ...trade, quantity: 5, monitoring: {
  partial_exits: [leg, { quantity: 2, gross: -3, entry_fee: 0.2, exit_fee: 0.3 }],
} };
assert.equal(tradePartialProgress(twice).originalQuantity, 10);
assert.equal(tradePartialProgress(twice).closedPercent, 50);
assert.equal(tradePartialProgress(twice).realizedNet, 9);
assert.equal(tradePartialProgress({ ...trade, quantity: 2, monitoring: {
  original_quantity: 3, partial_exits: [{ ...leg, quantity: 1 }],
} }).closedPercent, 33.3);
assert.equal(tradePartialProgress({ ...trade, monitoring: {
  partial_exits: [{ ...leg, quantity: NaN }],
} }), null);
console.log('Paper partial checks passed');
