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
