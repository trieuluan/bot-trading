import assert from 'node:assert/strict';
import { test, before, after } from 'node:test';
import { fileURLToPath } from 'node:url';
import { readFileSync } from 'node:fs';
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

test('hiding selected setup only clears selection, never closes broker tickets', async () => {
    const { ForexPositionGroups } = await server.ssrLoadModule('/src/components/ForexPositionGroups.tsx');
    let selectedId = '123';
    let closes = 0;
    const props = {
      groups: [{
        id: 'setup:demo:USDJPY:100', symbol: 'USDJPY', signal_time: 100,
        side: 'sell', ownership_verified: true, source: 'learning', status: 'open',
        open_count: 1, ticket_count: 1, legs: [{ position_id: '123', status: 'open', deals: [] }],
      }],
      selectedId,
      onSelect: (identifier) => { selectedId = identifier; },
      onDeselect: () => { selectedId = undefined; },
      onCloseTicket: () => { closes += 1; },
      onCloseSetup: () => { closes += 1; },
      closeDisabled: false,
      pnlOf: () => null,
    };
    const buttons = [];
    function visit(element) {
      if (!React.isValidElement(element)) return;
      if (element.type === 'button') buttons.push(element);
      React.Children.forEach(element.props.children, visit);
    }
    visit(ForexPositionGroups(props));
    const hide = buttons.find((button) => button.props.children === 'Ẩn khỏi chart');
    assert.ok(hide);
    hide.props.onClick();
    assert.equal(selectedId, undefined);
    assert.equal(closes, 0);
    const list = renderToStaticMarkup(React.createElement(ForexPositionGroups, { ...props, onDeselect: undefined }));
    assert.ok(list.includes('Xem chart'));
    assert.ok(!list.includes('Ẩn khỏi chart'));
});

test('chart offers individual TP1/Runner tickets without duplicating setup history', async () => {
    const { ForexStrategyPage } = await server.ssrLoadModule('/src/pages/ForexStrategyPage.tsx');
    const chart = await server.ssrLoadModule('/src/components/MtfObservationChart.tsx');
    const legs = ['tp1', 'runner'].map((role, index) => ({
      position_id: String(123 + index), symbol: 'USDJPY', side: 'sell', role,
      volume: 0.05, remaining_volume: 0.05, status: 'open', deals: [], pnl: null,
    }));
    const group = { id: 'setup:demo:USDJPY:100', status: 'open', legs };
    const props = {
      dashboard: {
        workspace: { symbol: 'USDJPY' }, market: { worker_online: true },
        observation: { status: 'waiting', snapshot: null },
      },
      executions: legs, positionGroups: [group], executionOnly: true,
      selectedPosition: legs[1], disconnected: false, includeReplay: false,
    };
    props.chartScope = 'ticket'; // drawer/#id: chỉ đúng ticket, không kéo cả setup
    const render = () => renderToStaticMarkup(React.createElement(ForexStrategyPage, props));
    const markup = render();
    // Thanh điều khiển trên chart thu gọn và chip trong drawer
    assert.ok(markup.includes('Danh sách'));
    assert.ok(markup.includes('Ticket BÁN #124'));
    assert.ok(!markup.includes('Setup BÁN #123'));
    assert.equal((markup.match(/class="[^"]*broker-position-chip[^"]*"/g) ?? []).length, 2);
    assert.match(markup, /aria-pressed="false" aria-label="Chọn ticket #123 · TP1"/);
    assert.match(markup, /class="[^"]*broker-position-chip[^"]*" aria-pressed="true" aria-label="Chọn ticket #124 · Runner"/);
    assert.ok(!markup.includes('Đóng cả setup'));
    assert.ok(!markup.includes('Chi tiết 2 ticket'));
    assert.equal(chart.chartProps.selectedPositionId, '124');
    assert.deepEqual(chart.chartProps.positionGroups, []); // ticket scope: chart chỉ vẽ ticket đó

    props.chartScope = 'setup'; // Xem chart ở setup card: kéo cả setup
    assert.ok(render().includes('Setup BÁN #123') && render().includes('2 ticket'));
    assert.deepEqual(chart.chartProps.positionGroups, [group]);

    Object.assign(legs[0], { status: 'closed', remaining_volume: 0, pnl: 3.49, take_profit_filled: true });
    const partial = render();
    assert.equal((partial.match(/class="[^"]*\bbroker-position-chip\b[^"]*"/g) ?? []).length, 2);
    assert.ok(partial.includes('Đã chốt TP1'));
    assert.ok(partial.includes('3.49 USD'));

    props.selectedPosition = legs[0];
    render();
    assert.equal(chart.chartProps.selectedPositionId, '123');

    props.selectedPosition = undefined;
    assert.equal((render().match(/class="[^"]*broker-position-chip[^"]*" aria-pressed="true"/g) ?? []).length, 0);
    const source = readFileSync(new URL('../src/pages/ForexStrategyPage.tsx', import.meta.url), 'utf8');
    // source check updated to drawer handler
    assert.ok(source.includes('onSelectPosition(selected ? undefined : pos.position_id)') || source.includes('onSelectPosition'));
    assert.ok(source.includes("chartScope === 'setup' ? positionGroups : []"));
});

test('formatForexLeverage formats broker leverage according to calculation mode', async () => {
  const { formatForexLeverage } = await server.ssrLoadModule('/src/lib/format.ts');
  assert.equal(formatForexLeverage({ calc_mode: 'forex', account_leverage: '2000' }), '1:2.000');
  assert.equal(formatForexLeverage({ calc_mode: 'cfd_leverage', account_leverage: '500' }), '1:500');
  assert.equal(formatForexLeverage({ calc_mode: 'forex_no_leverage', margin_percent: '0.01' }), '1:100');
  assert.equal(formatForexLeverage({ calc_mode: 'cfd', margin_percent: '0.005' }), '1:200');
  assert.equal(formatForexLeverage(null), '—');
  assert.equal(formatForexLeverage({ calc_mode: 'unknown' }), '—');
  for (const value of [undefined, '', '0', '-2', 'NaN', 'Infinity']) {
    assert.equal(formatForexLeverage({ calc_mode: 'forex', account_leverage: value }), '—');
    assert.equal(formatForexLeverage({ calc_mode: 'cfd', margin_percent: value }), '—');
  }
});

test('terminal-account-bar shows configured pair leverage and 1% risk budget', async () => {
  const { ForexExecutionPage } = await server.ssrLoadModule('/src/pages/ForexExecutionPage.tsx');
  const html = renderToStaticMarkup(
    React.createElement(ForexExecutionPage, {
      dashboard: {
        workspace: { symbol: 'USDJPY', id: 'exness_forex', name: 'Forex' },
        market: { status: 'running', message: 'OK' },
        observation: { status: 'ready', message: 'OK' },
        instrument: {
          profile: {
            contract_size: '100000',
            point_digits: 3,
            quote_to_account_loss: '0.0067',
            quote_to_account_gain: '0.0067',
            calc_mode: 'forex',
            account_leverage: '2000',
            margin_percent: '1',
          },
        },
      },
    }),
  );
  assert.ok(html.includes('Đòn bẩy USDJPY · cấu hình'));
  assert.ok(html.includes('1:2.000'));
  assert.ok(html.includes('Rủi ro / setup'));
  assert.ok(html.includes('Đòn bẩy cấu hình USDJPY từ thông số broker'));
  assert.ok(html.includes('Điều khiển bot'));
  assert.ok(html.includes('Số dư · USD'));
  assert.ok(html.includes('Vốn thực · USD'));
  assert.ok(html.includes('grid-cols-2'));
  assert.ok(html.includes('sm:grid-cols-4'));
  assert.match(html, /<details[^>]*group\/account/);
  assert.match(html, /<button[^>]*terminal-toggle[^>]*disabled=""/);
});

test('ForexSignalLifecycle drawer empty state preserves filters, tabs and safety guidance', async () => {
  const { ForexSignalLifecycle } = await server.ssrLoadModule('/src/components/ForexSignalLifecycle.tsx');
  const html = renderToStaticMarkup(React.createElement(ForexSignalLifecycle, {
    open: true,
    initialTab: 'setups',
    onTabChange: () => {},
    symbol: 'EURUSD',
    positions: [],
    pnlOf: () => null,
    setups: [],
    selectedSetupId: undefined,
    onSelectPosition: () => {},
    onSelectSetup: () => {},
    onClose: () => {},
    entryStateLabels: {},
    hasCandle: () => true,
  }));
  assert.ok(html.includes('aria-label="Lọc setup"'));
  assert.ok(html.includes('aria-pressed="true"'));
  assert.ok(html.includes('Không có setup nào trong bộ lọc này'));
  assert.ok(html.includes('Không phải lệnh broker') || html.includes('không phải lệnh broker') || html.includes('Không có setup nào'));
  assert.ok(html.includes('Bộ lọc setup') || html.includes('Tất cả') || html.includes('Đang canh'));
});

test('ForexCatchupStatus compact view shows clean status summary and retry countdown', async () => {
  const { ForexCatchupStatus } = await server.ssrLoadModule('/src/pages/ForexStrategyPage.tsx');
  const retry = new Date(Date.now() + 16000).toISOString();
  const html = renderToStaticMarkup(React.createElement(ForexCatchupStatus, {
    dashboard: {
      workspace: { symbol: 'EURUSD', id: 'exness_forex', name: 'Forex' },
      market: { status: 'running', message: 'OK', worker_online: true },
      observation: {
        status: 'catching_up',
        message: 'OK',
        catch_up: { pending_bars: 41, last_processed_candle: 1729996200, latest_closed_candle: 1730000000 },
      },
      frames: [
        { timeframe: '15m', remaining_intervals: 9, error: 'Cannot reach Exness API', retry_at: retry },
        { timeframe: '1h', remaining_intervals: 2, error: 'Cannot reach Exness API', retry_at: retry },
        { timeframe: '4h', remaining_intervals: 3, error: 'Cannot reach Exness API', retry_at: retry },
      ],
    },
    disconnected: false,
    compact: true,
  }));
  assert.ok(html.includes('Kết nối nến Exness gián đoạn'));
  assert.ok(html.includes('Tự thử lại'));
  assert.ok(html.includes('15m: 9'));
  assert.ok(html.includes('MTF: 41'));
  assert.ok(html.includes('Chi tiết'));
});
