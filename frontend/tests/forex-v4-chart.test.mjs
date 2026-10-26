import assert from 'node:assert/strict';
import { test } from 'node:test';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

test('ForexSignalLifecycle marks V4 shadow setups as simulation and renders guide', async () => {
  const server = await createServer({
    root: fileURLToPath(new URL('../', import.meta.url)),
    server: { middlewareMode: true, hmr: false, ws: false },
    appType: 'custom',
  });
  try {
    const { ForexSignalLifecycle } = await server.ssrLoadModule('/src/components/ForexSignalLifecycle.tsx');
    const setups = [
      {
        id: 'setup:1',
        time: 1000,
        side: 'long',
        intents: [],
        signals: [{ strategy: 'hast4_alpha', side: 'long', available_at: 1000, status: 'waiting' }],
      },
    ];
    let selected = null;
    const markup = renderToStaticMarkup(
      React.createElement(ForexSignalLifecycle, {
        setups,
        symbol: 'EURUSD',
        selectedId: null,
        onSelect: (id) => { selected = id; },
      }),
    );
    assert.ok(markup.includes('Mô phỏng'));
    assert.ok(markup.includes('1 mô phỏng'));
    assert.ok(markup.includes('V4 đang canh / chạy mô phỏng'));
    assert.ok(markup.includes('Xem trên chart'));
  } finally {
    await server.close();
  }
});
