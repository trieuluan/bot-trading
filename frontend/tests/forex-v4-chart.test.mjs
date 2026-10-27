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
    assert.match(markup, /<article class="[^\"]*\bshrink-0\b/);
    assert.match(markup, /class="signal-lifecycle-list [^\"]*max-h-\[250px\][^\"]*overflow-y-auto/);
    assert.ok(!markup.includes('Chi tiết thực thi'));
    const selectedMarkup = renderToStaticMarkup(
      React.createElement(ForexSignalLifecycle, {
        setups, symbol: 'EURUSD', selectedId: 'setup:1', onSelect: () => {},
      }),
    );
    assert.equal((selectedMarkup.match(/aria-pressed="true"/g) ?? []).length, 2);
    assert.ok(selectedMarkup.includes('Chi tiết thực thi'));
    assert.ok(selectedMarkup.includes('aria-label="Vòng đời tín hiệu EURUSD"'));
  } finally {
    await server.close();
  }
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
    assert.ok(!markup.includes('<span>Chú giải</span>'));
    assert.ok(markup.includes('aria-label="Chú giải biểu tượng"'));
    assert.match(markup, /popover="auto" role="dialog" aria-label="Chú giải dải cặp tiền"/);
    assert.ok(markup.includes('aria-label="Đóng chú giải"'));
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
