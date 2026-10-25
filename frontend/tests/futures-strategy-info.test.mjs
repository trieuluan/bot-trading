import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
  hasHardTarget,
  howToPlay,
  riskParts,
  stepKind,
  targetCapText,
} from '../src/lib/futures-strategy-info.ts';

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

test('hasHardTarget distinguishes runner/scale_out_ladder from hard TP policies', () => {
  assert.equal(hasHardTarget({ exit_policy: 'runner' }), false);
  assert.equal(hasHardTarget({ exit_policy: 'scale_out_ladder' }), false);
  assert.equal(hasHardTarget({ exit_policy: 'fixed' }), true);
  assert.equal(hasHardTarget({ exit_policy: 'adaptive' }), true);
  assert.equal(hasHardTarget({ exit_policy: 'trail' }), true);

  const runnerParts = riskParts({ exit_policy: 'runner', target_r: 2, stop_buffer: 0 });
  assert.match(runnerParts.tp, /Không dùng TP cứng/);

  const fixedParts = riskParts({ exit_policy: 'fixed', target_r: 3.5, stop_buffer: 0.1 });
  assert.match(fixedParts.tp, /tối đa 3.5R/);
});

test('card and panel distinguish legacy gene from effective cap without rewriting genome', () => {
  for (const policy of [
    undefined,
    'fixed',
    'adaptive',
    'trail',
    'atr_chandelier',
    'time_stop',
    'signal_invalidation',
    'volatility_stop',
    'funding_exit',
    'breakeven',
    'structure',
  ]) {
    for (const target of [1.5, 2, 3.5, 5]) {
      const genome = { family: 'rule_tree', exit_policy: policy, target_r: target, stop_buffer: 0 };
      const original = structuredClone(genome);
      const cap = targetCapText(genome);
      assert.equal(hasHardTarget(genome), true);
      assert.equal(cap, target < 2 ? 'Gene 1.5R · trần hiệu lực 2R' : `Trần TP: ${target}R`);
      const text = howToPlay(genome, '').find((step) => step.label === 'Chốt lời TP').text;
      assert.ok(text.includes(cap));
      assert.ok(text.includes(`tối đa ${Math.max(2, target)}R`));
      assert.match(text, /cản gần hơn 1.5R/);
      assert.deepEqual(genome, original);
    }
  }
});

test('no-target policies separate reference target from their own exit thresholds', () => {
  const runner = {
    family: 'rule_tree',
    exit_policy: 'runner',
    target_r: 2,
    stop_buffer: 0,
    entry_policy: 'body50',
    exit_params: { partial_r: 2.5, partial_fraction: 0.4, atr_multiple: 4 },
  };
  const ladder = {
    ...runner,
    exit_policy: 'scale_out_ladder',
    exit_params: { step_r: 1.5, partial_fraction: 0.2 },
  };
  for (const genome of [runner, ladder]) {
    assert.equal(targetCapText(genome), 'Không dùng TP cứng');
    const parts = riskParts(genome);
    assert.match(parts.tp, /Target tham chiếu 2R/);
    assert.match(parts.tp, /không kích hoạt đóng lệnh/);
    assert.doesNotMatch(parts.tp, /Trần TP:/);
    assert.match(
      howToPlay(genome, '').find((step) => step.label === 'Huỷ lệnh chờ').text,
      /target tham chiếu chạm/,
    );
  }
  assert.match(riskParts(runner).manage, /2.5R: chốt 40%/);
  assert.match(riskParts(runner).manage, /4 ATR14 H1/);
  assert.match(riskParts(ladder).manage, /1.5R và 3R: mỗi bậc chốt 20%/);
  assert.match(riskParts(ladder).manage, /đóng hết tại 4.5R/);
});

test('howToPlay renders H4 consensus slot with EMA50 mandatory and 1-of-2 confirmations', () => {
  const g = {
    family: 'rule_tree',
    lookback: 20,
    target_r: 2,
    stop_buffer: 0.2,
    exit_policy: 'adaptive',
    entry_policy: 'market',
    rule: {
      op: 'all',
      children: [
        {
          op: 'all',
          children: [
            { feature: 'trend', frame: 'H4' },
            { feature: 'swing_structure', frame: 'H4' },
            { feature: 'market_alignment', frame: 'H4' },
          ],
        },
        { feature: 'breakout', frame: 'H1' },
        { feature: 'volume', frame: 'M15' },
      ],
    },
  };
  const steps = howToPlay(g, 'stop');
  const h4 = steps.find((s) => s.label === 'Tín hiệu H4');
  assert.ok(h4);
  assert.equal(h4.text, 'Xu hướng EMA50 bắt buộc + ít nhất 1 trong 2 xác nhận (2/3):');
  assert.deepEqual(h4.items, [
    'Hai đỉnh và hai đáy cùng hướng, pivot xác nhận 2 nến',
    'BTC cùng chiều và ít nhất 5/10 coin đồng thuận H4',
  ]);
});
