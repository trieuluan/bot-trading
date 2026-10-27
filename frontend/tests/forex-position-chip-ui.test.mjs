import assert from 'node:assert/strict';
import { test, before, after } from 'node:test';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

let server;
before(async () => {
  server = await createServer({
    root: fileURLToPath(new URL('../', import.meta.url)),
    optimizeDeps: { noDiscovery: true },
    plugins: [{
      name: 'stub-chart-canvas',
      enforce: 'pre',
      transform(_source, id) {
        if (id.endsWith('/lib/router.ts')) return `
          export function useRoute() { return [{}, () => {}]; }
          export function setRoute() {}
        `;
        if (id.endsWith('/components/MtfObservationChart.tsx')) return `
          export let chartProps;
          export function MtfObservationChart(props) { chartProps = props; return null; }
        `;
      },
    }],
    server: { middlewareMode: true, hmr: false, ws: false },
    appType: 'custom',
  });
});
after(async () => {
  await server?.close();
});

test('broker position chips distinguish closed from open positions and show descriptive role label', async () => {
  const { ForexStrategyPage } = await server.ssrLoadModule('/src/pages/ForexStrategyPage.tsx');
  const legs = [
    { position_id: '13594393', symbol: 'EURGBP', side: 'buy', role: 'tp1', volume: 0.05, remaining_volume: 0, status: 'closed', deals: [], pnl: 4.86, take_profit_filled: true },
    { position_id: '13701707', symbol: 'EURGBP', side: 'buy', role: 'partial1', volume: 0.05, remaining_volume: 0, status: 'closed', deals: [], pnl: 1.12, close_reason: 'weak_close' },
    { position_id: '13701712', symbol: 'EURGBP', side: 'buy', role: 'runner', volume: 0.05, remaining_volume: 0.05, status: 'open', deals: [], pnl: null },
  ];
  const group = { id: 'setup:demo:EURGBP:100', status: 'open', legs };
  const props = {
    dashboard: {
      workspace: { symbol: 'EURGBP' }, market: { worker_online: true },
      observation: { status: 'waiting', snapshot: null },
    },
    executions: legs, positionGroups: [group], executionOnly: true,
    selectedPosition: undefined, disconnected: false, includeReplay: false,
  };
  const markup = renderToStaticMarkup(React.createElement(ForexStrategyPage, props));
  assert.ok(markup.includes('TP1'));
  assert.ok(markup.includes('Runner'));
  assert.ok(markup.includes('Chốt phần 1'));
  assert.ok(markup.includes('Đã chốt TP1'));
  assert.ok(markup.includes('Đã đóng · tín hiệu suy yếu'));
});
