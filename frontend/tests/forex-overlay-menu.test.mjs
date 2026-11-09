import assert from 'node:assert/strict';
import { test, after } from 'node:test';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';

const server = await createServer({
  root: fileURLToPath(new URL('../', import.meta.url)),
  server: { middlewareMode: true, hmr: false, ws: false },
  optimizeDeps: { noDiscovery: true, include: [] },
  appType: 'custom',
});
after(() => server.close());
const { overlayTrees, toggleOverlayGroup } = await server.ssrLoadModule('/src/components/StrategyOverlayMenu.tsx');

test('overlayTrees preserves independent individual tree entries', () => {
  const strategies = {
    hast4_m: { status: 'idle' },
    hast4_s: { status: 'idle' },
  };
  const trees = [
    { id: 'hast4_m', generation: 0, entry_policy: 'market' },
    { id: 'hast4_s', generation: 0, entry_policy: 'stop_trigger_break' },
  ];
  const items = overlayTrees(strategies, trees);
  assert.equal(items.length, 2);
  assert.equal(items[0].id, 'hast4_m');
  assert.deepEqual(items[0].ids, ['hast4_m']);
  assert.equal(items[1].id, 'hast4_s');
  assert.deepEqual(items[1].ids, ['hast4_s']);
});

test('toggleOverlayGroup toggles whole group and respects automatic/locked IDs', () => {
  const ids = ['t1', 't2', 't3'];
  const automatic = ['t1'];
  const selectedAfterOn = toggleOverlayGroup([], ids, automatic);
  assert.deepEqual(selectedAfterOn, ['t2', 't3']);
  const selectedAfterOff = toggleOverlayGroup(['t2', 't3', 'other'], ids, automatic);
  assert.deepEqual(selectedAfterOff, ['other']);
});

test('missing metadata preserves independent trees', () => {
  assert.deepEqual(overlayTrees({}, []), []);
  const strategies = { old1: { status: 'idle' }, old2: { status: 'idle' } };
  assert.equal(overlayTrees(strategies, []).length, 2);
  assert.equal(overlayTrees(strategies, [{ id: 'missing' }]).length, 2);
});

test('partial selection adds remaining policies; clearing preserves context and automatic overlays', () => {
  const selected = ['context', 'market'];
  assert.deepEqual(toggleOverlayGroup(selected, ['market', 'limit', 'stop'], ['stop']), ['context', 'market', 'limit']);
  assert.deepEqual(selected, ['context', 'market']);
  assert.deepEqual(toggleOverlayGroup(['context'], ['stop'], ['stop']), ['context']);
});
