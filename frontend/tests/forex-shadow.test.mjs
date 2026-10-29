import assert from 'node:assert/strict';
import { test } from 'node:test';
import { shadowLevels, shadowPhase, shadowPnlR, shadowPnlText } from '../src/lib/forex-shadow.ts';

test('shadowPhase categorizes shadow trades correctly', () => {
  assert.equal(shadowPhase({ status: 'open' }), 'open');
  assert.equal(shadowPhase({ status: 'waiting' }), 'waiting');
  assert.equal(shadowPhase({ status: 'pending' }), 'waiting');
  assert.equal(shadowPhase({ status: 'resolved' }), 'history');
  assert.equal(shadowPhase({ status: 'expired' }), 'history');
  assert.equal(shadowPhase({ status: 'excluded' }), 'history');
});

test('shadowLevels extracts planned levels when unfilled and executed levels when filled', () => {
  const waitingTrade = {
    entry: null,
    planned_levels: [
      { label: 'STOP ENTRY', price: 1.085 },
      { label: 'SL', price: 1.082 },
      { label: 'TP', price: 1.092 },
    ],
  };
  assert.deepEqual(shadowLevels(waitingTrade), [
    { label: 'STOP ENTRY DỰ KIẾN', price: 1.085 },
    { label: 'SL DỰ KIẾN', price: 1.082 },
    { label: 'TP DỰ KIẾN', price: 1.092 },
  ]);

  const openTrade = {
    status: 'open',
    entry: 1.085,
    legs: [
      { role: 'tp1', open: true, stop: 1.082, target: 1.09 },
      { role: 'runner', open: true, stop: 1.082, target: 1.095 },
    ],
  };
  assert.deepEqual(shadowLevels(openTrade), [
    { label: 'ENTRY', price: 1.085 },
    { label: 'SL tp1', price: 1.082 },
    { label: 'TP tp1', price: 1.09 },
    { label: 'SL runner', price: 1.082 },
    { label: 'TP runner', price: 1.095 },
  ]);
});

test('shadowPnlR calculates net R correctly for resolved and open trades', () => {
  const resolved = { status: 'resolved', net_r: 1.45 };
  assert.equal(shadowPnlR(resolved), 1.45);

  const openTrade = {
    status: 'open',
    side: 'long',
    entry: 1.085,
    valuation: {
      risk: 0.003,
      realized_r: 0,
      slippage_bps: 0,
      commission_per_unit: 0,
      verified: true,
      gain_rate: 1,
      loss_rate: 1,
    },
    legs: [{ role: 'all', open: true, weight: 1 }],
  };
  const r = shadowPnlR(openTrade, { bid: 1.088, ask: 1.0881 });
  assert.ok(Math.abs(r - 1.0) < 1e-9);

  assert.equal(shadowPnlR(openTrade, null), null);
  assert.equal(shadowPnlR({ ...openTrade, valuation: { ...openTrade.valuation, verified: false } }, { bid: 1.088, ask: 1.0881 }), null);
});

test('short marks on ask, accounts for partial closes, conversion and exit costs', () => {
  const trade = {
    status: 'open', side: 'short', entry: 160,
    valuation: { risk: 1, realized_r: .4, slippage_bps: 1, commission_per_unit: .01,
      verified: true, gain_rate: .0059, loss_rate: .006 },
    legs: [{ open: false, weight: .5 }, { open: true, weight: .5 }],
  };
  const expected = .4 + .5 * ((160 - 159 * 1.0001) * .0059 - .01 * .0059) / .006;
  assert.ok(Math.abs(shadowPnlR(trade, { bid: 158, ask: 159 }) - expected) < 1e-9);
  const expectedLoss = .4 + .5 * ((160 - 161 * 1.0001) * .006 - .01 * .0059) / .006;
  assert.ok(Math.abs(shadowPnlR(trade, { bid: 160, ask: 161 }) - expectedLoss) < 1e-9);
  for (const value of [null, NaN, Infinity, -1, 0]) {
    assert.equal(shadowPnlR({ ...trade, valuation: { ...trade.valuation, risk: value } }, { bid: 158, ask: 159 }), null);
  }
});

test('cash requires verified risk amount; waiting never fabricates P&L or an entry', () => {
  const resolved = { status: 'resolved', net_r: 1.5, valuation: { risk_usd: 24 } };
  assert.equal(shadowPnlText(resolved), '+36.00 USD · +1.50R');
  assert.equal(shadowPnlText({ ...resolved, valuation: {} }), '+1.50R');
  assert.equal(shadowPnlText({ status: 'waiting', net_r: 0 }), 'P&L —');
  assert.equal(shadowPnlR({ status: 'resolved', net_r: NaN }), null);
  const partial = { status: 'open', entry: 100, legs: [
    { role: 'tp1', open: false, stop: 90, target: 110 },
    { role: 'runner', open: true, stop: 100, target: 120 },
  ] };
  assert.deepEqual(shadowLevels(partial).map((level) => level.label), ['ENTRY', 'SL runner', 'TP runner']);
  assert.deepEqual(shadowLevels({ planned_levels: [{ label: 'SL', price: NaN }, { label: 'TP', price: -1 }] }), []);
});

test('Shadow drawer isolates symbol, renders simulated prices and selection without broker controls', async () => {
  const { createServer } = await import('vite');
  const { fileURLToPath } = await import('node:url');
  const { default: React } = await import('react');
  const { renderToStaticMarkup } = await import('react-dom/server');
  const server = await createServer({
    root: fileURLToPath(new URL('../', import.meta.url)),
    optimizeDeps: { noDiscovery: true, include: [] },
    server: { middlewareMode: true, hmr: false, ws: false }, appType: 'custom',
  });
  try {
    const { ForexSignalLifecycle } = await server.ssrLoadModule('/src/components/ForexSignalLifecycle.tsx');
    const trade = { id: 'shadow-1', symbol: 'EURUSD', strategy: 'hast4_test', side: 'long',
      status: 'open', available_at: 900, entry: 1.09, entry_time: 1800,
      legs: [{ role: 'runner', open: true, volume: .01, stop: 1.08, target: 1.11 }] };
    const markup = renderToStaticMarkup(React.createElement(ForexSignalLifecycle, {
      open: true, initialTab: 'shadow', onTabChange() {}, symbol: 'EURUSD', positions: [],
      pnlOf: () => null, setups: [], onSelectPosition() {}, onSelectSetup() {}, onClose() {},
      entryStateLabels: {}, hasCandle: () => false, shadowStatus: 'ready',
      selectedShadowId: 'shadow-1', shadowTrades: [trade, { ...trade, id: 'other', symbol: 'USDJPY', strategy: 'wrong-symbol' }],
    }));
    assert.ok(markup.includes('MÔ PHỎNG V4'));
    assert.ok(markup.includes('ENTRY: 1.09000'));
    assert.ok(markup.includes('SL runner: 1.08000'));
    assert.ok(markup.includes('TP runner: 1.11000'));
    assert.ok(markup.includes('Ẩn khỏi chart'));
    assert.ok(markup.includes('Chưa có nến'));
    assert.ok(markup.includes('P&amp;L —'));
    assert.ok(!markup.includes('wrong-symbol'));
    assert.ok(!markup.includes('Đóng cả setup'));
  } finally {
    await server.close();
  }
});
