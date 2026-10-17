import assert from 'node:assert/strict';
import { estimatedPaperPnl } from '../src/lib/paper-position.ts';
import { placeChartTags } from '../src/lib/chart-tags.ts';

const trade = {
  entry: 100,
  quantity: 2,
  side: 'long',
  entry_fee: 0.1,
  funding_cost: 0.2,
  exit_time: null,
};
const near = (actual, expected) => assert.ok(Math.abs(actual - expected) < 1e-9);
near(estimatedPaperPnl(trade, 110, 5), 19.59);
near(estimatedPaperPnl(trade, 90, 5), -20.39);
near(estimatedPaperPnl({ ...trade, side: 'short' }, 90, 5), 19.61);
near(estimatedPaperPnl({ ...trade, side: 'short' }, 110, 5), -20.41);
near(estimatedPaperPnl({ ...trade, funding_cost: -0.2 }, 100, 5), 0);
near(estimatedPaperPnl({ ...trade, side: 'short' }, 90, 5, false), 20);
near(estimatedPaperPnl({ ...trade, side: 'short' }, 110, 5, false), -20);
for (const price of [null, NaN, Infinity, -1, 0]) {
  assert.equal(estimatedPaperPnl(trade, price, 5), null);
}
assert.equal(estimatedPaperPnl({ ...trade, exit_time: 123 }, 110, 5), null);
assert.equal(estimatedPaperPnl(trade, 110, -5), null);
const tags = placeChartTags([{ y: 180 }, { y: 180 }, { y: 181 }], 400);
assert.equal(tags.length, 3);
assert.ok(tags[1].displayY - tags[0].displayY >= 30);
assert.ok(tags[2].displayY - tags[1].displayY >= 30);
console.log('Paper position PnL and chart tags: OK');
