import assert from 'node:assert/strict';
import { test } from 'node:test';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

test('ForexEntryPilotPanel renders independent entry policy cards and labels', async () => {
  const server = await createServer({
    root: fileURLToPath(new URL('../', import.meta.url)),
    server: { middlewareMode: true, hmr: false, ws: false },
    appType: 'custom',
  });
  try {
    const { ForexEntryPilotPanel } = await server.ssrLoadModule('/src/pages/ForexStrategyPage.tsx');
    const policies = ['market', 'limit_setup_mid', 'stop_trigger_break', 'limit_or_stop'];
    for (const policy of policies) {
      const html = renderToStaticMarkup(
        React.createElement(ForexEntryPilotPanel, {
          symbol: 'XAUUSD',
          entryPilot: {
            enabled: true,
            intents: [],
            pilot: {
              status: 'ready',
              tree_count: 1,
              trees: [
                {
                  id: `hast4_${policy}`,
                  control_tree_id: 'hast3_test',
                  entry_policy: policy,
                  entry_params:
                    policy === 'market'
                      ? {}
                      : policy === 'limit_setup_mid'
                        ? { limit_ratio: 0.5, wait_bars: 4 }
                        : policy === 'stop_trigger_break'
                          ? { stop_buffer_atr: 0.05, wait_bars: 4 }
                          : { limit_ratio: 0.5, stop_buffer_atr: 0.05, wait_bars: 4 },
                  h1_setup: 'channel_breakout',
                  m15_trigger: 'channel',
                  exit_policy: 'fixed_structural',
                  samples_count: 0,
                  resolved_count: 0,
                  mean_r: null,
                },
              ],
            },
          },
        }),
      );
      const expectedLabel = {
        market: 'Market',
        limit_setup_mid: 'Limit ảo',
        stop_trigger_break: 'Stop ảo',
        limit_or_stop: 'Hybrid · Limit/Stop ảo',
      }[policy];
      assert.ok(html.includes(expectedLabel), `Expected ${expectedLabel} for ${policy}`);
      if (policy === 'market') {
        assert.ok(html.includes('Market · vào trực tiếp'));
        assert.ok(!html.includes('Chờ tối đa'));
      }
    }
  } finally {
    await server.close();
  }
});

test('ForexEntryPilotPanel displays individual tree metrics and progress', async () => {
  const server = await createServer({
    root: fileURLToPath(new URL('../', import.meta.url)),
    server: { middlewareMode: true, hmr: false, ws: false },
    appType: 'custom',
  });
  try {
    const { ForexEntryPilotPanel } = await server.ssrLoadModule('/src/pages/ForexStrategyPage.tsx');
    const trees = [
      {
        id: 'hast4_tree_01', generation: 0, control_tree_id: 'hast3_base',
        entry_policy: 'stop_trigger_break', entry_params: { wait_bars: 4, stop_buffer_atr: 0.05 },
        h1_setup: 'channel_breakout', m15_trigger: 'channel', exit_policy: 'fixed_structural',
        samples_count: 12, resolved_count: 10, mean_r: 0.75, eligible: true, ready: true,
        status_counts: { resolved: 10, open: 2 }, parents: [],
      },
      {
        id: 'hast4_tree_02', generation: 1, control_tree_id: 'hast3_base',
        entry_policy: 'market', entry_params: {},
        h1_setup: 'nr7', m15_trigger: 'setup_reclaim', exit_policy: 'ladder_h1',
        samples_count: 5, resolved_count: 3, mean_r: -0.2, eligible: false, ready: false,
        status_counts: { resolved: 3, expired_0r: 2 }, parents: ['hast4_tree_01'],
      },
    ];
    const html = renderToStaticMarkup(React.createElement(ForexEntryPilotPanel, {
      symbol: 'XAUUSD',
      entryPilot: {
        enabled: true, intents: [],
        pilot: { status: 'ready', tree_count: 2, generation: 1, population_model: 'independent_v1', min_outcomes: 10, trees },
      },
    }));
    assert.ok(html.includes('title="hast4_tree_01"'));
    assert.ok(html.includes('title="hast4_tree_02"'));
    assert.ok(html.includes('2 / 2 cây · entry tiến hóa độc lập'));
    assert.ok(html.includes('aria-label="Ma trận cấu trúc H1 theo cách vào lệnh"'));
    assert.ok(html.includes('#ree_01 · G0</span>'));
    assert.ok(html.includes('#ree_02 · G1</span>'));
    assert.ok(html.includes('+0.75R'));
    assert.ok(html.includes('10/10'));
    assert.ok(html.includes('2 mở/chờ'));
    assert.ok(html.includes('Có thể sinh con'));
    assert.ok(html.includes('Đang gom mẫu'));
    assert.ok(html.includes('aria-label="Lọc cách vào lệnh"'));
    assert.ok(html.includes('aria-label="Shadow đang chạy"'));
    assert.ok(html.includes('aria-label="Thế hệ cây"'));
    assert.ok(!html.includes('aria-label="Trạng thái cây"'), 'lifecycle filter only via quick chips');
    assert.ok(html.includes('M15: Lấy lại mốc setup H1'));
    assert.ok(html.includes('Chốt từng nấc +1R/+2R'));
    assert.ok(html.includes('aria-label="Sắp xếp cây"'));
    assert.ok(html.includes('Đủ mẫu trước · Mean R'));
    assert.ok(html.includes('Mean R cao nhất'));
    assert.ok(!html.includes('Xoá lọc'));
    assert.ok(!html.includes('4/4'));
    assert.ok(!html.includes('bộ'));
  } finally {
    await server.close();
  }
});

test('ForexEntryPilotPanel renders new root setups with Vietnamese labels', async () => {
  const server = await createServer({
    root: fileURLToPath(new URL('../', import.meta.url)),
    server: { middlewareMode: true, hmr: false, ws: false },
    appType: 'custom',
  });
  try {
    const { ForexEntryPilotPanel } = await server.ssrLoadModule('/src/pages/ForexStrategyPage.tsx');
    const setups = [
      { id: 't_nr7', setup: 'nr7', expected: 'Biên độ hẹp 7 nến NR7' },
      { id: 't_asia', setup: 'asia_break', expected: 'Phá biên phiên Á UTC' },
      { id: 't_london', setup: 'london_orb_h1', expected: 'Phá biên mở phiên London H1' },
    ];
    const trees = setups.map((s) => ({
      id: s.id, generation: 0, control_tree_id: 'c_' + s.id,
      entry_policy: 'market', entry_params: {},
      h1_setup: s.setup, m15_trigger: 'setup_reclaim', exit_policy: 'fixed_structural',
      samples_count: 0, resolved_count: 0, mean_r: null,
    }));
    const html = renderToStaticMarkup(React.createElement(ForexEntryPilotPanel, {
      symbol: 'EURUSD',
      entryPilot: {
        enabled: true, intents: [],
        pilot: { status: 'ready', tree_count: 3, generation: 0, population_model: 'independent_v1', min_outcomes: 10, trees },
      },
    }));
    for (const s of setups) {
      assert.ok(html.includes(s.expected), `Expected label ${s.expected} for ${s.setup}`);
    }
    assert.ok(html.includes('Đang chờ mẫu shadow đầu tiên.'));
    assert.ok(html.includes('Chờ tín hiệu'));
    assert.ok(!html.includes('0 đang chạy'));
  } finally {
    await server.close();
  }
});
