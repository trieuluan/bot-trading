import assert from 'node:assert/strict';
import { aggregateCandles } from '../frontend/src/lib/chart-timeframes.ts';

const bars = Array.from({ length: 192 }, (_, index) => ({
  time: index * 900, open: 100 + index, high: 102 + index,
  low: 99 + index, close: 101 + index, volume: index + 1,
}));
assert.equal(aggregateCandles(bars, 900), bars);
for (const seconds of [3600, 14400, 86400]) {
  const result = aggregateCandles(bars, seconds, 172800);
  const count = seconds / 900;
  assert.equal(result.length, 192 / count);
  assert.deepEqual(result[0], {
    time: 0, open: 100, high: 101 + count, low: 99,
    close: 100 + count, volume: count * (count + 1) / 2,
  });
  assert.equal(aggregateCandles(bars.slice(1), seconds, 172800).length, result.length - 1);
  assert.equal(aggregateCandles(bars.filter((_, index) => index !== 1), seconds, 172800).length,
    result.length - 1);
}
assert.deepEqual(aggregateCandles(bars.slice(0, 3), 3600, 7200), []);
assert.equal(aggregateCandles(bars.slice(0, 3), 3600, 1800)[0].close, 103);
const live = [...bars.slice(0, 3), { ...bars[3], high: 200, close: 190 }];
assert.equal(aggregateCandles(live, 3600, 3000)[0].high, 200);
assert.equal(aggregateCandles(live, 3600, 3000)[0].close, 190);
assert.deepEqual(aggregateCandles([], 86400), []);
assert.throws(() => aggregateCandles(bars, 60));
console.log('Chart timeframe checks passed');
