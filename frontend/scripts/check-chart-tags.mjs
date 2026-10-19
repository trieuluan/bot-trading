import assert from 'node:assert/strict';
import { groupNearbyTags, placeChartTags } from '../src/lib/chart-tags.ts';
import { drawOrder, stackLabels } from '../src/lib/chart-labels.ts';
import { formatPrice, formatQuote } from '../src/lib/format.ts';

assert.equal(formatPrice(0.99), '0.99');
assert.equal(formatQuote(0.9912), '0.9912');
assert.notEqual(formatQuote(0.9912), formatQuote(0.9945));
assert.equal(formatQuote(81891), '81,891.00');
assert.equal(formatQuote(1.012), '1.012');

const labels = [
  { time: 1, above: true, text: 'Signal' },
  { time: 2, above: true, text: 'Entry' },
  { time: 1, above: true, text: 'Confirmation' },
  { time: 1, above: false, text: 'Exit' },
];
const stacked = stackLabels(labels);
assert.deepEqual(stacked.map((label) => label.slot), [0, 0, 1, 0]);
assert.deepEqual(drawOrder(stacked, 1).map((label) => label.text), ['Entry', 'Signal', 'Confirmation', 'Exit']);
assert.deepEqual(drawOrder(stacked, 2).map((label) => label.text), ['Signal', 'Confirmation', 'Exit', 'Entry']);
assert.deepEqual(drawOrder(stacked, null), stacked);
assert.deepEqual(drawOrder(stacked, 99), stacked);
assert.deepEqual(stacked.map((label) => label.time), [1, 2, 1, 1]);

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
const near = groupNearbyTags([
  { id: 1, kind: 'stop', side: 'short', y: 120 },
  { id: 2, kind: 'stop', side: 'short', y: 124 },
  { id: 3, kind: 'target', side: 'short', y: 122 },
  { id: 4, kind: 'stop', side: 'long', y: 121 },
]);
assert.deepEqual(near.map((group) => group.members.map((tag) => tag.id)), [[1, 2], [4], [3]]);
console.log('Chart tag checks passed: overlaps, bounds, overflow columns, offscreen prices.');
