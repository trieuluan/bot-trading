import assert from 'node:assert/strict';
import { HTF_LABELS, profileOf, strategyName } from '../src/lib/futures-strategy-info.ts';

const ruleFor = (feat) => ({
  op: 'all',
  children: [
    { feature: 'trend', frame: 'H4' },
    { feature: feat, frame: 'H1' },
    { feature: 'volume', frame: 'M15' },
  ],
});

const cases = [
  ['breakout', 'htf_breakout', 'Breakout HTF'],
  ['compression', 'htf_squeeze', 'Squeeze HTF'],
  ['wick_sweep', 'htf_sweep', 'Quét thanh khoản HTF'],
  ['ema_touch', 'htf_pullback', 'Pullback EMA HTF'],
  ['ema_cross_n', 'htf_ema_cross', 'EMA cross HTF'],
  ['wedge', 'htf_wedge', 'Nêm HTF'],
  ['swing_zone', 'htf_swing', 'Vùng swing HTF'],
];

for (const [feat, expectedKey, expectedLabel] of cases) {
  const genome = { family: 'rule_tree', lookback: 20, target_r: 2, stop_buffer: 0, rule: ruleFor(feat) };
  assert.equal(profileOf(genome), expectedKey);
  assert.equal(HTF_LABELS[expectedKey], expectedLabel);
  assert.ok(strategyName(genome).startsWith(expectedLabel));
}

// Retest with sequence child
const retestGenome = {
  family: 'rule_tree', lookback: 20, target_r: 2, stop_buffer: 0,
  rule: {
    op: 'all',
    children: [
      { feature: 'trend', frame: 'H4' },
      { op: 'then', within: 3, children: [{ feature: 'breakout', frame: 'H1' }, { feature: 'retest', frame: 'H1' }] },
      { feature: 'engulfing', frame: 'M15' },
    ],
  },
};
assert.equal(profileOf(retestGenome), 'htf_retest');
assert.ok(strategyName(retestGenome).startsWith('Retest HTF'));

console.log('HTF labels check passed successfully');

import { projectOverlayPoints, recentChartRange } from '../src/lib/chart-timeframes.ts';
assert.deepEqual(recentChartRange(200), { from: 79.5, to: 211.5 });
assert.deepEqual(recentChartRange(40), { from: -0.5, to: 51.5 });
{
  const merged = projectOverlayPoints([{ time: 1000, value: 50 }, { time: 1900, value: 60 }], 3600);
  assert.deepEqual(merged, [{ time: 0, value: 60 }]);
  const split = projectOverlayPoints([{ time: 1000, value: 50 }, { time: 4000, value: 70 }], 3600);
  assert.equal(split.length, 2);
  for (const step of [900, 3600, 14400, 86400]) {
    assert.deepEqual(projectOverlayPoints([
      { time: step + 600, value: 70 },
      { time: step + 300, value: 50 },
      { time: 0, value: 10 },
      { time: Number.NaN, value: 20 },
      { time: step * 2, value: Number.NaN },
    ], step), [{ time: 0, value: 10 }, { time: step, value: 70 }]);
  }
  console.log('overlay projection check passed');
}
