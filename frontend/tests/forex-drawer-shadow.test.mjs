import assert from 'node:assert/strict';
import test from 'node:test';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { createServer } from 'vite';
import { fileURLToPath } from 'node:url';

test('ForexSignalLifecycle shadow tab reflects count and auto-defaults correctly', async () => {
  const server = await createServer({
    root: fileURLToPath(new URL('../', import.meta.url)),
    server: { middlewareMode: true, hmr: false, ws: false },
    appType: 'custom',
  });
  try {
    const { ForexSignalLifecycle } = await server.ssrLoadModule('/src/components/ForexSignalLifecycle.tsx');
    const shadowTrades = [
      { id: 'st1', symbol: 'GBPJPY', strategy: 'hast4_s1', side: 'short', status: 'excluded', available_at: 1000 },
      { id: 'st2', symbol: 'GBPJPY', strategy: 'hast4_s2', side: 'short', status: 'excluded', available_at: 1000 },
      { id: 'st3', symbol: 'GBPJPY', strategy: 'hast4_s3', side: 'short', status: 'excluded', available_at: 1000 },
      { id: 'st4', symbol: 'GBPJPY', strategy: 'hast4_s4', side: 'short', status: 'excluded', available_at: 1000 },
    ];
    const html = renderToStaticMarkup(
      React.createElement(ForexSignalLifecycle, {
        open: true,
        initialTab: 'shadow',
        onTabChange: () => {},
        symbol: 'GBPJPY',
        positions: [],
        setups: [],
        onSelectPosition: () => {},
        onSelectSetup: () => {},
        onClose: () => {},
        pnlOf: () => null,
        entryStateLabels: {},
        hasCandle: () => true,
        shadowTrades,
        shadowStatus: 'ready',
      }),
    );
    // Tab header reflects 4 total when open=0
    assert.ok(html.includes('Shadow (4)'));
    // Sub-tab counts
    assert.ok(html.includes('Đang chạy <span class="font-mono text-[10px] opacity-75">(0)</span>'));
    assert.ok(html.includes('Chờ khớp <span class="font-mono text-[10px] opacity-75">(0)</span>'));
    assert.ok(html.includes('Lịch sử <span class="font-mono text-[10px] opacity-75">(4)</span>'));
  } finally {
    await server.close();
  }
});
