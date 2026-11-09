import assert from 'node:assert/strict';
import test from 'node:test';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { createServer } from 'vite';
import { fileURLToPath } from 'node:url';

test('ForexEntryPilotPanel matrix buttons expose dialog semantics', async () => {
  const server = await createServer({
    root: fileURLToPath(new URL('../', import.meta.url)),
    server: { middlewareMode: true, hmr: false, ws: false },
    appType: 'custom',
  });
  try {
    const { ForexEntryPilotPanel } = await server.ssrLoadModule('/src/pages/ForexStrategyPage.tsx');
    const html = renderToStaticMarkup(
      React.createElement(ForexEntryPilotPanel, {
        symbol: 'GBPJPY',
        entryPilot: {
          enabled: true,
          intents: [],
          pilot: {
            status: 'ready',
            tree_count: 1,
            trees: [{
              id: 'hast4_pop_01',
              control_tree_id: 'ctrl_1',
              entry_policy: 'market',
              entry_params: {},
              h1_setup: 'channel_breakout',
              m15_trigger: 'channel',
              exit_policy: 'fixed_structural',
              samples_count: 0,
              resolved_count: 0,
              mean_r: null,
            }],
          },
        },
      }),
    );
    assert.ok(html.includes('aria-haspopup="dialog"'));
    assert.ok(html.includes('aria-expanded="false"'));
  } finally {
    await server.close();
  }
});
