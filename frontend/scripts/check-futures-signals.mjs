import assert from 'node:assert/strict';
import { liveSignals } from '../src/lib/futures-signals.ts';
const now = 1800000000 * 1000;
const auto = {
  worker_online: true, learner_error: null, auto_mode: 'paper_learning',
  learner_decision: { time: Math.floor(now / 900000) * 900 - 900, auto_mode: 'paper_learning',
    strategies: [{ strategy: 'tree:1', status: 'signal' }, { strategy: 'tree:2', status: 'no_signal' }] },
};
assert.deepEqual(liveSignals(auto, now).map((row) => row.strategy), ['tree:1']);
assert.equal(liveSignals(auto, now + 900000).length, 0);
assert.equal(liveSignals({ ...auto, worker_online: false }, now).length, 0);
assert.equal(liveSignals({ ...auto, learner_error: 'failed' }, now).length, 0);
assert.equal(liveSignals({ ...auto, auto_mode: 'learner' }, now).length, 0);
assert.equal(liveSignals(null, now).length, 0);
console.log('Futures signals: freshness, errors, worker and mode checks passed.');
