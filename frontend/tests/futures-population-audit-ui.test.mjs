import assert from 'node:assert/strict';
import test from 'node:test';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { createServer } from 'vite';
import { fileURLToPath } from 'node:url';

test('fmt handles null, undefined, and valid numbers safely', async () => {
  const server = await createServer({
    root: fileURLToPath(new URL('../', import.meta.url)),
    server: { middlewareMode: true, hmr: false, ws: false },
    appType: 'custom',
  });
  try {
    const { fmt, RetirementMetrics } = await server.ssrLoadModule('/src/pages/FuturesLearningPage.tsx');
    assert.equal(fmt(null), '—');
    assert.equal(fmt(undefined), '—');
    assert.equal(fmt(NaN), '—');
    assert.equal(fmt(2.5, 2, 'R'), '2.50R');
    assert.equal(fmt(12.345, 1, '%'), '12.3%');

    // Retirement with null mean_r must not throw TypeError
    const htmlNull = renderToStaticMarkup(
      React.createElement('table', null,
        React.createElement('tbody', null,
          React.createElement('tr', null,
            React.createElement(RetirementMetrics, {
              retirement: {
                at: 1794334550,
                reason: 'sparse_forward_replay',
                samples: 0,
                mean_r: null,
                max_drawdown_r: 0.0,
                retained_strategy: null,
                overlap: null,
              },
            }),
          ),
        ),
      ),
    );
    assert.ok(htmlNull.includes('—'));
    assert.ok(htmlNull.includes('0.00R'));
  } finally {
    await server.close();
  }
});

test('PopulationAuditStatus renders running and idle states safely', async () => {
  const server = await createServer({
    root: fileURLToPath(new URL('../', import.meta.url)),
    server: { middlewareMode: true, hmr: false, ws: false },
    appType: 'custom',
  });
  try {
    const { PopulationAuditStatus, ReplayWorkerStatus } = await server.ssrLoadModule('/src/pages/FuturesLearningPage.tsx');

    const workerHtml = renderToStaticMarkup(
      React.createElement(ReplayWorkerStatus, {
        worker: {
          consumer_enabled: true,
          stale: false,
          phase: 'audit:replay',
          active_job: {
            purpose: 'audit',
            symbol: 'BTC_USDT',
            strategy: 'rule_tree:02952706c012',
            phase: 'replay',
            completed: 5376,
            total: 8640,
            updated_at: 1794334550,
          },
        },
        onView: () => {},
      }),
    );
    assert.ok(workerHtml.includes('Worker replay'));
    assert.ok(workerHtml.includes('BTC/USDT'));
    assert.ok(workerHtml.includes('Audit quần thể · 90 ngày'));
    assert.ok(workerHtml.includes('Xem BTC/USDT'));
    assert.ok(workerHtml.includes('62% bước hiện tại'));
    assert.ok(workerHtml.includes('5.376 / 8.640 nến'));
    const universe = (phase, replay_symbol) => renderToStaticMarkup(React.createElement(ReplayWorkerStatus, {
      worker: { consumer_enabled: true, stale: false, active_job: { purpose: 'draft', symbol: 'DOGE_USDT', replay_symbol,
        universe: true, strategy: 'rule_tree:b82f7e1d', phase, completed: 16608, total: 26640 } },
      onView: () => {},
    }));
    const cross = universe('AVAX_USDT:replay', 'AVAX_USDT');
    assert.ok(cross.includes('AVAX/USDT') && cross.includes('Con nháp thuộc DOGE/USDT'));
    assert.ok(cross.includes('62% cặp hiện tại') && cross.includes('Xem DOGE/USDT'));
    const pending = universe('data', null);
    assert.ok(pending.includes('Đang chuẩn bị') && !pending.includes('<strong class="text-sky-200">DOGE'));
    assert.ok(universe('AVAX_USDT:benchmark:BTC_USDT', 'AVAX_USDT').includes('Tải nến tham chiếu'));
    const runningHtml = renderToStaticMarkup(
      React.createElement(PopulationAuditStatus, {
        job: {
          status: 'running',
          strategy: 'rule_tree:005cfaaa4b93',
          phase: 'funding',
          completed: 4,
          total: 11,
          updated_at: 1794334550,
        },
        worker: { consumer_enabled: true, stale: false, phase: 'audit:funding' },
        queued: 120,
      }),
    );
    assert.ok(runningHtml.includes('Tải funding'));
    assert.ok(runningHtml.includes('#005cfa'));
    assert.ok(runningHtml.includes('120 cây chờ'));
    assert.ok(runningHtml.includes('role="progressbar"'));
    assert.ok(runningHtml.includes('aria-valuenow="36"'));
    assert.ok(runningHtml.includes('bước hiện tại'));
    assert.ok(!runningHtml.includes('11 nến'));

    const renderProgress = (progress, worker = {}) => renderToStaticMarkup(
      React.createElement(PopulationAuditStatus, {
        job: { status: 'running', strategy: 'rule_tree:005cfaaa4b93', phase: 'replay', updated_at: 1794334550, ...progress },
        worker: { consumer_enabled: true, stale: false, ...worker },
        queued: 120,
      }),
    );
    for (const progress of [{}, { completed: 0, total: 0 }, { completed: NaN, total: 10 }, { completed: -1, total: 10 }, { completed: 1, total: Infinity }]) {
      const html = renderProgress(progress);
      assert.ok(html.includes('role="progressbar"'));
      assert.ok(!html.includes('aria-valuenow='));
      assert.ok(html.includes('Chưa có số liệu tiến độ'));
    }
    assert.ok(renderProgress({ completed: 12, total: 10 }).includes('aria-valuenow="100"'));
    assert.ok(renderProgress({ completed: 0, total: 10 }).includes('aria-valuenow="0"'));
    assert.ok(renderProgress({ completed: 4, total: 10 }).includes('10 nến'));
    for (const worker of [{ stale: true }, { consumer_enabled: false }]) {
      assert.ok(!renderProgress({ completed: 4, total: 10 }, worker).includes('role="progressbar"'));
    }

    const idleHtml = renderToStaticMarkup(
      React.createElement(PopulationAuditStatus, {
        job: { status: 'completed', strategy: 'rule_tree:005cfaaa4b93', updated_at: 1794334550 },
        worker: { consumer_enabled: true, stale: false, phase: 'idle' },
        queued: 0,
      }),
    );
    assert.ok(idleHtml.includes('Đã hoàn tất lượt gần nhất'));
    assert.ok(!idleHtml.includes('role="progressbar"'));

    const timeoutHtml = renderToStaticMarkup(
      React.createElement(PopulationAuditStatus, {
        job: {
          status: 'failed',
          strategy: 'rule_tree:02952706c012',
          error: 'Replay exceeded 60 minutes',
          error_kind: 'timeout',
          attempts: 2,
          next_retry_at: 1794336627,
          updated_at: 1794334550,
        },
        worker: { consumer_enabled: true, stale: false, phase: 'idle' },
        queued: 119,
      }),
    );
    assert.ok(timeoutHtml.includes('Vượt thời gian'));
    assert.ok(timeoutHtml.includes('Lần thử 2'));
    assert.ok(timeoutHtml.includes('Thử lại lúc'));
    assert.ok(!timeoutHtml.includes('role="progressbar"'));
  } finally {
    await server.close();
  }
});
