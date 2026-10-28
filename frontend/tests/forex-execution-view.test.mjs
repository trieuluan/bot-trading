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

import { brokerFillsDiagnosis } from '../src/lib/forex-execution-view.ts';
test('brokerFillsDiagnosis: chỉ TP1 khớp, không suy diễn runner', () => {
  const one = { side: 'long', broker_fills: [{ position_id: '1', price: 1 }] };
  const legs = executionLegs(one, [{ position_id: '1', role: 'tp1', volume: 0.05, status: 'closed', pnl: 3 }], () => 0);
  const d = brokerFillsDiagnosis(one, legs);
  assert.equal(d.isPartialFill, true);
  assert.match(d.text, /Chỉ khớp TP1/);
  assert.equal(brokerFillsDiagnosis({ side: 'long' }, []).text, 'Chưa có lệnh broker khớp');
});

import { shadowStatusText } from '../src/lib/forex-execution-view.ts';
test('shadowStatusText: broker đóng, shadow chưa cập nhật', () => {
  const labels = { open: 'Shadow đang mở', resolved: 'Đã kết thúc' };
  assert.match(shadowStatusText('open', true, labels), /Đang đối chiếu/);
  assert.equal(shadowStatusText('open', false, labels), 'Shadow đang mở');
  assert.equal(shadowStatusText('resolved', true, labels), 'Đã kết thúc');
});
