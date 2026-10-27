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
const { overlayBundles, toggleOverlayGroup } = await server.ssrLoadModule('/src/components/StrategyOverlayMenu.tsx');

test('overlayBundles groups 4 entry variants under 1 bundle', () => {
  const strategies = {
    hast4_m: { status: 'idle' },
    hast4_l: { status: 'idle' },
    hast4_s: { status: 'idle' },
    hast4_h: { status: 'idle' },
  };
  const trees = [
    { id: 'hast4_m', bundle_id: 'bundle_01', generation: 0, entry_policy: 'market' },
    { id: 'hast4_l', bundle_id: 'bundle_01', generation: 0, entry_policy: 'limit_setup_mid' },
    { id: 'hast4_s', bundle_id: 'bundle_01', generation: 0, entry_policy: 'stop_trigger_break' },
    { id: 'hast4_h', bundle_id: 'bundle_01', generation: 0, entry_policy: 'limit_or_stop' },
  ];
  const bundles = overlayBundles(strategies, trees);
  assert.equal(bundles.length, 1);
  assert.equal(bundles[0].id, 'bundle_01');
  assert.deepEqual(bundles[0].ids, ['hast4_m', 'hast4_l', 'hast4_s', 'hast4_h']);
});

test('toggleOverlayGroup toggles whole group and respects automatic/locked IDs', () => {
  const ids = ['t1', 't2', 't3'];
  const automatic = ['t1'];
  const selectedAfterOn = toggleOverlayGroup([], ids, automatic);
  assert.deepEqual(selectedAfterOn, ['t2', 't3']);
  const selectedAfterOff = toggleOverlayGroup(['t2', 't3', 'other'], ids, automatic);
  assert.deepEqual(selectedAfterOff, ['other']);
});

test('missing metadata never merges unrelated trees or invents entry variants', () => {
  assert.deepEqual(overlayBundles({}, []), []);
  const strategies = { old1: { status: 'idle' }, old2: { status: 'idle' } };
  assert.equal(overlayBundles(strategies, []).length, 2);
  assert.equal(overlayBundles(strategies, [{ id: 'missing', bundle_id: 'other' }]).length, 2);
});

test('partial selection adds remaining policies; clearing preserves context and automatic overlays', () => {
  const selected = ['context', 'market'];
  assert.deepEqual(toggleOverlayGroup(selected, ['market', 'limit', 'stop'], ['stop']), ['context', 'market', 'limit']);
  assert.deepEqual(selected, ['context', 'market']);
  assert.deepEqual(toggleOverlayGroup(['context'], ['stop'], ['stop']), ['context']);
});
