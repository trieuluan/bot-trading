import assert from 'node:assert/strict';
import { recentChartRange, CHART_RIGHT_SPACE } from '../src/lib/chart-timeframes.ts';
import { forexSignalCount, marketSessionDisplay } from '../src/lib/forex-labels.ts';

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

const openView = marketSessionDisplay(
  {
    status: 'open',
    reason: 'scheduled_open',
    as_of: 1000,
    observed_at: 990,
    next_open_at: 2000,
    next_close_at: 1600,
    source: 'exness_trade_sessions',
  },
  1000 * 1000,
);
assert.equal(openView.status, 'open');
assert.equal(openView.label, 'Đang mở');
assert.equal(openView.tone, 'ok');
assert.match(openView.transition, /Nghỉ dự kiến/);
assert.match(openView.tooltip, /Theo lịch Exness · giờ thực tế có thể thay đổi/);

const breakView = marketSessionDisplay(
  {
    status: 'closed',
    reason: 'session_break',
    as_of: 1000,
    observed_at: 990,
    next_open_at: 1600,
    next_close_at: 2000,
    source: 'exness_trade_sessions',
  },
  1000 * 1000,
);
assert.equal(breakView.status, 'closed');
assert.equal(breakView.label, 'Nghỉ phiên');
assert.equal(breakView.tone, 'idle');
assert.match(breakView.transition, /Mở dự kiến/);

const weekendView = marketSessionDisplay(
  {
    status: 'closed',
    reason: 'weekend',
    as_of: 1000,
    observed_at: 990,
    next_open_at: 5000,
    next_close_at: 8000,
    source: 'exness_trade_sessions',
  },
  1000 * 1000,
);
assert.equal(weekendView.label, 'Đóng cuối tuần');
assert.equal(weekendView.tone, 'idle');

const staleView = marketSessionDisplay(
  {
    status: 'open',
    reason: 'scheduled_open',
    as_of: 1000,
    observed_at: 1000,
    next_open_at: 6000,
    next_close_at: 5000,
    source: 'exness_trade_sessions',
  },
  4600 * 1000,
);
assert.equal(staleView.status, 'unknown');
assert.equal(staleView.label, 'Chưa xác minh');
assert.equal(staleView.tone, 'idle');
console.log('Forex chart range and signal expiry checks passed.');
