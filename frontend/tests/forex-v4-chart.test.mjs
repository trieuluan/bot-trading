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
    assert.ok(markup.includes('Hướng dẫn · trạng thái'));
    assert.ok(markup.includes('Xem trên chart'));
  } finally {
    await server.close();
  }
});

test('styles.css keeps .signal-lifecycle-row uncompressed in flex container', async () => {
  const fs = await import('node:fs/promises');
  const css = await fs.readFile(fileURLToPath(new URL('../src/styles.css', import.meta.url)), 'utf8');
  assert.match(
    css,
    /\.signal-lifecycle-row\s*\{[^}]*flex-shrink:\s*0[^}]*\}/,
    'Expected .signal-lifecycle-row to set flex-shrink: 0 to prevent row height crushing',
  );
});

test('ForexStripLegend renders all entry branch states without default browser title tooltip', async () => {
  const server = await createServer({
    root: fileURLToPath(new URL('../', import.meta.url)),
    server: { middlewareMode: true, hmr: false, ws: false },
    appType: 'custom',
  });
  try {
    const { ForexStripLegend } = await server.ssrLoadModule('/src/components/ForexMarkets.tsx');
    const markup = renderToStaticMarkup(React.createElement(ForexStripLegend));
    assert.ok(markup.includes('Chú giải'));
    assert.ok(markup.includes('Canh giá'));
    assert.ok(markup.includes('Kích / đang gửi'));
    assert.ok(markup.includes('Đã khớp'));
    assert.ok(markup.includes('Đối soát'));
    assert.ok(markup.includes('Hủy / hết hạn'));
    assert.ok(markup.includes('Kết thúc'));
    assert.ok(!markup.includes('title="Giải thích icon"'));
  } finally {
    await server.close();
  }
});
