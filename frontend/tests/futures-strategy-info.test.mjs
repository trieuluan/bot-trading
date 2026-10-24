import assert from 'node:assert/strict';
import { test } from 'node:test';
import { howToPlay, riskParts, stepKind } from '../src/lib/futures-strategy-info.ts';

test('howToPlay maps all frames and separates entry, sl, tp, manage', () => {
  const g = {
    family: 'rule_tree',
    lookback: 20,
    target_r: 2,
    stop_buffer: 0.2,
    exit_policy: 'adaptive',
    entry_policy: 'body50',
    rule: {
      op: 'all',
      children: [
        {
          op: 'all',
          children: [
            { feature: 'trend', frame: 'H4' },
            { feature: 'market_alignment', frame: 'H4' },
          ],
        },
        { feature: 'breakout', frame: 'H1' },
        { feature: 'engulfing', frame: 'M15' },
      ],
    },
  };
  const steps = howToPlay(g, 'stop');
  const labels = steps.map((s) => s.label);
  assert.deepEqual(labels, [
    'Tín hiệu H4',
    'Tín hiệu H1',
    'Tín hiệu M15',
    'Điểm vào',
    'Huỷ lệnh chờ',
    'Cắt lỗ SL',
    'Chốt lời TP',
    'Quản lý lệnh',
  ]);
  assert.equal(stepKind('Cắt lỗ SL'), 'sl');
  assert.equal(stepKind('Chốt lời TP'), 'tp');
  assert.equal(stepKind('Điểm vào'), 'entry');
  assert.equal(stepKind('Huỷ lệnh chờ'), 'entry');
  assert.equal(stepKind('Tín hiệu H4'), '');
  assert.match(steps.find((s) => s.label === 'Điểm vào').text, /50% thân nến/);
  assert.match(steps.find((s) => s.label === 'Cắt lỗ SL').text, /Swing 5 nến H1/);
});

test('riskParts extracts distinct SL, TP and management texts', () => {
  const parts = riskParts({ lookback: 20, target_r: 2.5, stop_buffer: 0.1, exit_policy: 'fixed' });
  assert.match(parts.sl, /Swing 5 nến H1/);
  assert.match(parts.tp, /Trước cản H1\/H4/);
  assert.match(parts.manage, /Giữ nguyên SL\/TP/);
});
