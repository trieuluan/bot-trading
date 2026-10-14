import assert from 'node:assert/strict';
import { placeChartTags } from '../src/lib/chart-tags.ts';

for (const count of [1, 5, 7, 22]) {
  for (const priceY of [0, 100, 399]) {
    const input = Array.from({ length: count }, (_, id) => ({ id, y: priceY }));
    const placed = placeChartTags(input, 400);
    assert.equal(placed.length, count);
    assert.equal(new Set(placed.map((tag) => tag.id)).size, count);
    for (const [index, tag] of placed.entries()) {
      assert.equal(tag.y, priceY);
      assert.ok(tag.displayY >= 16 && tag.displayY <= 384);
      const previous = placed[index - 1];
      if (previous?.column === tag.column) assert.ok(tag.displayY - previous.displayY >= 30);
    }
    assert.deepEqual(
      input.map((tag) => tag.y),
      Array(count).fill(priceY),
    );
  }
}
assert.deepEqual(placeChartTags([{ y: -1 }, { y: 401 }, { y: NaN }], 400), []);
assert.deepEqual(placeChartTags([{ y: 10 }], 0), []);
assert.deepEqual(
  placeChartTags([{ y: 100 }, { y: 200 }], 400).map((tag) => tag.displayY),
  [100, 200],
);
console.log('Chart tag checks passed: overlaps, bounds, overflow columns, offscreen prices.');
