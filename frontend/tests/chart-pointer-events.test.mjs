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
