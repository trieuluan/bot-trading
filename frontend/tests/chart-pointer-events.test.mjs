import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import { placeChartTags, clusterEntryTags } from '../src/lib/chart-tags.ts';

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

test('PaperChart separates hover preview from persistent native position details', async () => {
  const source = await readFile(new URL('../src/components/PaperChart.tsx', import.meta.url), 'utf8');
  const tip = source.match(/className="position-tip [^"]*"/)?.[0];
  for (const utility of ['pointer-events-auto', 'fixed', 'flex-col', 'overflow-hidden', 'bg-surface-2', 'sm:w-[400px]', 'w-full', '[&:popover-open]:flex']) {
    assert.ok(tip?.includes(utility), `Tooltip needs ${utility}`);
  }
  assert.match(source, /position-tip-list min-h-0 overflow-y-auto overscroll-contain/);
  assert.match(source, /grid-cols-\[minmax\(0,1fr\)_minmax\(0,2fr\)\]/);
  assert.match(source, /event.key === 'Escape'\) setHover\(null\)/);
  assert.match(source, /position-quick-tip pointer-events-none/);
  assert.match(source, /popover="auto"/);
  assert.match(source, /popoverTarget=\{popoverId\}/);
  assert.match(source, /popoverTargetAction="hide"/);
  assert.match(source, /<details/);
  assert.match(source, /<summary/);
  assert.match(source, /onSelectPosition\(p.id\)/);
  assert.match(source, /hidePopover\(\)/);
  assert.ok(!source.includes('onSelectPosition?.(open.id)'));
  assert.match(source, /onMouseLeave=\{\(\) => setHover\(null\)\}/);
});

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

test('placeChartTags prevents overlapping tags when entry prices are close', () => {
  const tags = [
    { id: '13750498', y: 120 },
    { id: '13750522', y: 125 },
    { id: '13778382', y: 220 },
    { id: '13778398', y: 224 },
  ];
  const placed = placeChartTags(tags, 500, 48);
  assert.equal(placed.length, 4);
  for (let index = 1; index < placed.length; index++) {
    assert.ok(
      placed[index].displayY - placed[index - 1].displayY >= 48,
      `Tags ${placed[index - 1].id} and ${placed[index].id} must have 48px spacing`,
    );
  }
});

test('Forex tag layout stays bounded in dense columns and preserves price anchors', () => {
  for (const height of [100, 390, 540]) {
    for (const priceY of [0, 10, height / 2, height]) {
      const tags = Array.from({ length: 40 }, (_, index) => ({ id: String(index), y: priceY }));
      const placed = placeChartTags(tags, height, 48);
      assert.equal(placed.length, tags.length);
      for (const [index, tag] of placed.entries()) {
        assert.equal(tag.y, priceY);
        assert.ok(tag.displayY >= 16 && tag.displayY <= height - 16);
        const previous = placed[index - 1];
        if (previous?.column === tag.column) assert.ok(tag.displayY - previous.displayY >= 48);
      }
    }
  }
  assert.deepEqual(placeChartTags([{ y: NaN }, { y: -1 }, { y: 501 }], 500, 48), []);
});

test('clusterEntryTags groups nearby entry tags into a single cluster item', () => {
  const tags = [
    { id: '13750498', kind: 'entry', y: 120 },
    { id: '13750522', kind: 'entry', y: 125 }, // within 48px -> clustered
    { id: '13778382', kind: 'entry', y: 220 }, // far -> separate
    { id: '13750498', kind: 'stop', y: 80 },   // non-entry -> never clustered
  ];
  const placed = clusterEntryTags(tags, 500, 48);
  assert.equal(placed.length, 3);
  const cluster = placed.find((t) => t.id === '13750498' && t.kind === 'entry');
  assert.ok(cluster);
  assert.equal(cluster.members.length, 2);
  assert.deepEqual(cluster.members.map((m) => m.id), ['13750498', '13750522']);

  const singleStop = placed.find((t) => t.kind === 'stop');
  assert.equal(singleStop?.members.length, 1);
});

test('ChartPositionCluster UI component contract matches DOM accessibility requirements', async () => {
  const source = await readFile(new URL('../src/components/ChartPositionCluster.tsx', import.meta.url), 'utf8');
  assert.match(source, /aria-expanded=\{open\}/);
  assert.match(source, /popover="auto"/);
  assert.match(source, /role="dialog"/);
  assert.match(source, /onDetails/);
});

test('AutoJournal tooltip never intercepts hover and stays anchored above or below the candle', async () => {
  const source = await readFile(new URL('../src/components/AutoJournal.tsx', import.meta.url), 'utf8');
  const tooltip = source.match(/className=\{`ap-portal-tooltip[^`]*`\}/)?.[0];
  assert.ok(tooltip?.includes('pointer-events-none'));
  assert.ok(tooltip?.includes('-translate-x-1/2'));
  assert.ok(tooltip?.includes("tip.below ? 'translate-y-0' : '-translate-y-full'"));
  assert.ok(tooltip?.includes('w-[min(280px,calc(100vw-20px))]'));
  assert.match(source, /onMouseLeave=\{hideTip\}/);
  assert.match(source, /event.key === 'Escape'\) hideTip\(\)/);
});
