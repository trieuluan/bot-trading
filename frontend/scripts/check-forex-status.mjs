import assert from 'node:assert/strict';
import { botStatus } from '../src/lib/forex-labels.ts';

const healthy = { configured: true, enabled: true, worker_online: true, error: null };
const failed = {
  ...healthy,
  error: 'ExnessError',
  error_msg: 'ExnessError: Market execution unavailable for EURGBP',
  error_at: 1792859735,
  error_symbol: 'EURGBP',
};
assert.equal(botStatus(healthy, false).tone, 'ok');
assert.match(botStatus(failed, false).reason, /broker/);
assert.match(botStatus(failed, false).hint, /EURGBP/);
assert.match(botStatus({ ...failed, error_symbol: null }, false).hint, /chưa xác định cặp/);
assert.match(
  botStatus({ ...failed, error_msg: 'ExnessError: authentication failed' }, false).reason,
  /authentication failed/,
);
assert.equal(botStatus({ ...failed, error: null }, false).tone, 'ok');
assert.equal(botStatus({ ...failed, worker_online: false }, false).tone, 'bad');
assert.equal(botStatus(failed, true).tone, 'idle');
console.log('Forex status checks passed.');
