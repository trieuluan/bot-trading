import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

for (const component of ['MtfObservationChart', 'PaperChart']) {
  test(`${component} leaves decorative overlays transparent to pointer input`, async () => {
    const source = await readFile(new URL(`../src/components/${component}.tsx`, import.meta.url), 'utf8');
    const layer = source.match(/className="position-tag-layer[^"]*"/)?.[0];
    assert.ok(layer?.includes('pointer-events-none'));
    assert.ok(!layer.includes('[&>*]:pointer-events-auto'));
    assert.match(source, /className="position-tag-leaders pointer-events-none /);
    assert.match(source, /position-tag [^\n]*pointer-events-auto/);
  });
}

test('Forex chart keeps indicator readout in setup details, not over the canvas', async () => {
  const chart = await readFile(new URL('../src/components/MtfObservationChart.tsx', import.meta.url), 'utf8');
  const drawer = await readFile(new URL('../src/components/ForexSignalLifecycle.tsx', import.meta.url), 'utf8');
  const page = await readFile(new URL('../src/pages/ForexStrategyPage.tsx', import.meta.url), 'utf8');
  assert.ok(!chart.includes('forex-overlay-readout'));
  assert.ok(!chart.includes('Chỉ báo & mức giá'));
  assert.match(chart, /className="chart-ohlc pointer-events-none /);
  assert.match(drawer, /aria-label="Chỉ báo và mức giá setup"/);
  assert.match(drawer, /Snapshot của tín hiệu đang xem/);
  assert.match(drawer, /không phải snapshot lịch sử/);
  assert.match(page, /strategies=\{analysis\?\.strategies\}/);
});
