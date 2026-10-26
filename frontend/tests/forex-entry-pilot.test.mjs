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
      assert.ok(html.includes('<th>Cách vào lệnh</th>'));
      const expectedLabel = {
        market: 'Market',
        limit_setup_mid: 'Limit ảo',
        stop_trigger_break: 'Stop ảo',
        limit_or_stop: 'Hybrid · Limit/Stop ảo',
      }[policy];
      assert.ok(
        html.includes(`<span class="entry-tree-policy">${expectedLabel}</span>`),
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
