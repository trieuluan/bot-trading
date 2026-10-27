import assert from 'node:assert/strict';
import { test, before, after } from 'node:test';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

let server;
let journalModule;
before(async () => {
  server = await createServer({
    root: fileURLToPath(new URL('../', import.meta.url)),
    optimizeDeps: { noDiscovery: true },
    server: { middlewareMode: true, hmr: false, ws: false },
    appType: 'custom',
  });
  journalModule = await server.ssrLoadModule('/src/components/DecisionJournal.tsx');
});
after(async () => server?.close());

const candle = (time, extra = {}) => ({ time, at: time + 905, outcome: 'no_signal', confirmed: [], evaluated: true, ...extra });

test('journal groups only adjacent M15 candles, preserves order and does not mutate input', () => {
  const rows = [candle(900), candle(2700), candle(3600), candle(6300)];
  const original = structuredClone(rows);
  const groups = journalModule.groupRows(rows);
  assert.deepEqual(groups.map((group) => group.count), [1, 2, 1]);
  assert.deepEqual(groups[1].rows.map((row) => row.time), [3600, 2700]);
  assert.deepEqual(rows, original);
  assert.equal(journalModule.groupRows([candle(1800, { selected: 'tree' }), candle(900)]).length, 2);
  assert.equal(journalModule.groupRows([candle(1800), candle(900, { confirmed: [{ strategy: 'tree' }] })]).length, 2);
  assert.equal(journalModule.groupRows([candle(1800), candle(900, { reason: 'different' })]).length, 2);
  assert.deepEqual(journalModule.groupRows([]), []);
});

test('journal explains poor_rr without inventing thresholds or broker causes', () => {
  assert.match(journalModule.reasonText('poor_rr'), /R\/R.*chưa đạt/);
  assert.equal(journalModule.reasonText('unknown_broker_message'), 'unknown_broker_message');
  assert.equal(journalModule.reasonText(null), null);
});

test('journal renders separated metrics, local filter counts, raw reasons and attempt details', () => {
  const journal = [candle(2700, {
    outcome: 'blocked:preflight_failed', reason: 'poor_rr',
    events: [{ at: 3605, outcome: 'blocked:preflight_failed' }],
  }), candle(1800), candle(900)];
  const html = renderToStaticMarkup(React.createElement(journalModule.DecisionJournal, {
    journal,
    funnel: { candles: 18, evaluated: 18, with_signal: 0, selected: 0, orders: 0, outcomes: { no_signal: 17 } },
  }));
  assert.match(html, /grid-cols-2.*lg:grid-cols-4/);
  assert.match(html, /aria-label="Lọc theo kết quả"/);
  assert.match(html, /aria-pressed="true"/);
  assert.match(html, /2 nến liên tiếp/);
  assert.match(html, /poor_rr/);
  assert.match(html, /1 mốc ghi nhận/);
  assert.match(html, /border-rose-400/);
  assert.ok(!html.includes('NaN') && !html.includes('Infinity'));
  assert.equal((html.match(/<details/g) ?? []).length, 2);
});

test('journal handles no data, unknown status and long strategy IDs', () => {
  const render = (props) => renderToStaticMarkup(React.createElement(journalModule.DecisionJournal, props));
  assert.match(render({}), /Chưa có nến nào được ghi nhật ký/);
  const strategy = 'hast4_' + 'long'.repeat(50);
  const html = render({ journal: [candle(900, { outcome: 'unknown_status', selected: strategy, confirmed: [{ strategy, side: 'buy', shadow: true }] })] });
  assert.match(html, /unknown_status/);
  assert.match(html, /Bot chọn/);
  assert.match(html, /Mô phỏng/);
  assert.match(html, /truncate/);
});
