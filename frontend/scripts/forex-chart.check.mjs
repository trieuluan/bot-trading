import assert from 'node:assert/strict';
import { recentChartRange, CHART_RIGHT_SPACE } from '../src/lib/chart-timeframes.ts';
import { forexSignalCount } from '../src/lib/forex-labels.ts';

assert.deepEqual(recentChartRange(500), { from: 379.5, to: 511.5 });
assert.deepEqual(recentChartRange(40), { from: -0.5, to: 51.5 });
assert.equal(recentChartRange(500).to - (500 - 0.5), CHART_RIGHT_SPACE);
assert.deepEqual(recentChartRange(1000), {
  from: recentChartRange(500).from + 500,
  to: recentChartRange(500).to + 500,
});
const execution = { signal_count: 3, signal_available_at: 18000, signal_expires_at: 18120 };
assert.equal(forexSignalCount(execution, 18000000), 3);
assert.equal(forexSignalCount(execution, 18120000), 3);
assert.equal(forexSignalCount(execution, 18121000), 0);
assert.equal(forexSignalCount(execution, 17999000), 0);
assert.equal(forexSignalCount(undefined, 18000000), 0);
assert.equal(forexSignalCount({ signal_count: 56 }, 18000000), 0);
assert.equal(forexSignalCount({ ...execution, signal_count: -1 }, 18000000), 0);
assert.equal(forexSignalCount({ ...execution, signal_count: NaN }, 18000000), 0);
console.log('Forex chart range and signal expiry checks passed.');
