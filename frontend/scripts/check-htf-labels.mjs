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
