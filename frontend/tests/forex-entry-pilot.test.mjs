import assert from 'node:assert/strict';
import { test } from 'node:test';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

test('ForexEntryPilotPanel always renders entry policy column and labels', async () => {
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
      assert.ok(
        [...html.matchAll(/<span class="[^"]*\bentry-tree-policy\b[^"]*">([^<]*)<\/span>/g)]
          .some((match) => match[1] === expectedLabel),
        `Expected ${expectedLabel} in table cell for ${policy}`,
      );
      if (policy === 'market') {
        assert.ok(html.includes('Market · vào trực tiếp'));
        assert.ok(!html.includes('Chờ tối đa'));
      }
    }
  } finally {
    await server.close();
  }
});

test('ForexEntryPilotPanel groups 4 entries per V4 bundle and flags missing trees', async () => {
  const server = await createServer({
    root: fileURLToPath(new URL('../', import.meta.url)),
    server: { middlewareMode: true, hmr: false, ws: false },
    appType: 'custom',
  });
  try {
    const { ForexEntryPilotPanel } = await server.ssrLoadModule('/src/pages/ForexStrategyPage.tsx');
    const policies = ['market', 'limit_setup_mid', 'stop_trigger_break', 'limit_or_stop'];
    const tree = (bundle, policy) => ({
      id: `hast4_${bundle}_${policy}`, bundle_id: bundle, generation: 0, control_tree_id: `hast3_${bundle}`,
      entry_policy: policy, entry_params: {}, h1_setup: 'channel_breakout', m15_trigger: 'channel',
      exit_policy: 'fixed_structural', samples_count: 2, resolved_count: 1, mean_r: 0.5, status_counts: { resolved: 1, open: 1 },
    });
    const trees = [
      ...policies.map((p) => tree('bundle_00', p)),
      ...policies.slice(0, 3).map((p) => tree('bundle_01', p)),
    ];
    const html = renderToStaticMarkup(React.createElement(ForexEntryPilotPanel, {
      symbol: 'XAUUSD',
      entryPilot: { enabled: true, intents: [], pilot: { status: 'ready', tree_count: 7, bundle_count: 2, generation: 0, trees,
        bundles: { bundle_00: { matched_count: 0, ready: false, mean_r: null, running_setups: 1 } } } },
    }));
    assert.equal(html.match(/class="entry-bundle /g)?.length, 2);
    assert.equal(html.match(/entry-tree-policy-cell/g)?.length, 8);
    assert.ok(html.includes('thiếu cây'));
    assert.ok(html.includes('2 / 2 bộ'));
    assert.ok(html.includes('1 có R'));
    assert.ok(html.includes('1 đang chạy'));
    assert.ok(html.includes('1 setup chờ đủ 4 policy'));
    assert.ok(!html.includes('mẫu đóng'));
  } finally {
    await server.close();
  }
});
