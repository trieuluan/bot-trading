import assert from 'node:assert/strict';
import { test } from 'node:test';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

test('ForexSignalLifecycle drawer marks V4 shadow setups as simulation and renders drawer layout', async () => {
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
        open: true,
        initialTab: 'setups',
        onTabChange: () => {},
        symbol: 'EURUSD',
        positions: [],
        pnlOf: () => null,
        setups,
        selectedSetupId: undefined,
        onSelectPosition: () => {},
        onSelectSetup: (id) => { selected = id; },
        onClose: () => {},
        entryStateLabels: { waiting: 'Đang chờ' },
        hasCandle: () => true,
      }),
    );
    assert.ok(markup.includes('Mô phỏng: Đang chạy'));
    assert.ok(markup.includes('Chi tiết'));
    assert.match(markup, /class="[^"]*forex-inspector[^"]*"/);
    assert.ok(markup.includes('<dialog'));

    const selectedMarkup = renderToStaticMarkup(
      React.createElement(ForexSignalLifecycle, {
        open: true,
        initialTab: 'setups',
        onTabChange: () => {},
        symbol: 'EURUSD',
        positions: [],
        pnlOf: () => null,
        setups,
        selectedSetupId: 'setup:1',
        onSelectPosition: () => {},
        onSelectSetup: () => {},
        onClose: () => {},
        entryStateLabels: { waiting: 'Đang chờ' },
        hasCandle: () => true,
      }),
    );
    assert.ok(selectedMarkup.includes('aria-pressed="true"'));
    assert.ok(selectedMarkup.includes('aria-label="Danh sách vị thế và setup"'));
    assert.ok(selectedMarkup.includes('is-selected'));

    const failed = renderToStaticMarkup(React.createElement(ForexSignalLifecycle, {
      open: true,
      initialTab: 'setups',
      onTabChange: () => {},
      setups: [{ ...setups[0], intents: [{ id: 'i1', strategy: 'hast4_alpha', side: 'long', status: 'cancelled',
        reason: 'preflight_failed', reason_code: 'poor_rr', reason_detail: 'poor_rr: below 1.5R',
        limit_price: 1.1, stop_price: 1.2, expires_at: 2000 }] }],
      symbol: 'EURUSD',
      positions: [],
      pnlOf: () => null,
      selectedSetupId: 'setup:1',
      onSelectPosition: () => {},
      onSelectSetup: () => {},
      onClose: () => {},
      entryStateLabels: { waiting: 'Đang chờ' },
      hasCandle: () => true,
    }));
    assert.ok(failed.includes('Hủy'));
    assert.ok(failed.includes('Mô phỏng: Đang chạy'));
    assert.ok(failed.includes('R/R kém'));

    // Chi tiết setup trong cùng drawer (không dùng dialog lồng nhau)
    const detailMarkup = renderToStaticMarkup(
      React.createElement(ForexSignalLifecycle, {
        open: true,
        initialTab: 'setups',
        onTabChange: () => {},
        symbol: 'EURUSD',
        positions: [],
        pnlOf: () => null,
        setups: [{
          id: 'setup:detail',
          time: 1000,
          side: 'long',
          signals: [{ strategy: 'hast4_alpha', side: 'long', available_at: 1000, status: 'waiting' }],
          intents: [{
            id: 'intent-detail',
            strategy: 'hast4_alpha',
            side: 'long',
            status: 'cancelled',
            reason: 'preflight_failed',
            reason_code: 'sl_too_tight',
            limit_price: 1.085,
            stop_price: 1.087,
            expires_at: 2000,
            signal: { stop: 1.083, target: 1.092 },
          }],
        }],
        selectedSetupId: undefined,
        onSelectPosition: () => {},
        onSelectSetup: () => {},
        onClose: () => {},
        entryStateLabels: { waiting: 'Đang chờ' },
        hasCandle: () => false,
      }),
    );
    assert.ok((detailMarkup.match(/<dialog\b/g) || []).length === 1);
    assert.ok(!detailMarkup.includes('role="dialog"') || (detailMarkup.match(/<dialog\b/g) || []).length === 1);
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
    assert.match(markup, /badge-sample is-signal[^\"]*bg-bull\/10 text-bull/);
    assert.match(markup, /N là số cây còn tín hiệu/);
    assert.match(markup, /không phải số liệu trực tiếp/);
    assert.doesNotMatch(markup, /<\/svg>[14]<\/span>/);
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
