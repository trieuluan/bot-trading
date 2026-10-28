import assert from 'node:assert/strict';
import { test } from 'node:test';
import { executionLegs, executionSummary, entrySlippage, executionTimeline } from '../src/lib/forex-execution-view.ts';

const intent = { side: 'short', winner: 'market', status: 'submitted', available_at: 100, created_at: 105, triggered_at: 112.7,
  trigger_quote: { bid: 175.48, ask: 175.49 }, broker_fills: [{ position_id: '1', price: 175.484 }, { position_id: '2', price: 175.483 }] };
const positions = [
  { position_id: '1', role: 'tp1', volume: 0.31, status: 'closed', pnl: 5 },
  { position_id: '2', role: 'runner', volume: 0.32, status: 'open', pnl: null },
];
test('legs, summary, slippage, timeline', () => {
  const legs = executionLegs(intent, positions, () => -2);
  assert.deepEqual(legs.map((l) => [l.role, l.pnl]), [['TP1', 5], ['Runner', -2]]);
  const sum = executionSummary(legs);
  assert.equal(sum.pnl, 3);
  assert.equal(sum.open, 1);
  assert.ok(Math.abs(sum.lots - 0.63) < 1e-9);
  assert.ok(entrySlippage(intent, sum.averagePrice) < 0); // bán thấp hơn bid? bid 175.48 => 175.4835-175.48>0 nhưng short đảo dấu
  const steps = executionTimeline(intent, legs);
  assert.equal(steps[2].gap, 8);
  assert.equal(steps.find((x) => x.label === 'Broker khớp').time, null);
});
