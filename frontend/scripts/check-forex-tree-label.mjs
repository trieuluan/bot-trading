import assert from 'node:assert/strict';
import { treeLabel, shortTreeId } from '../src/lib/forex-tree-label.ts';

const label = {
  title: 'Mua · Quét thanh khoản + RSI',
  policy: 'Chốt 1 phần · BE H1',
  params: 'lookback 30',
  generation: 1,
  short: 'ab12',
  full: 'Mua · Quét thanh khoản + RSI · Chốt 1 phần · BE H1 · lookback 30 · G1 #b12c',
};
assert.equal(treeLabel({ label }, 'hast2_ab12cdef0000').title, label.title);
assert.equal(treeLabel({ generation: 2 }, 'hast2_ab12cdef0000').title, 'Cây #ab12');
assert.equal(treeLabel(undefined, 'hast2_ab12cdef0000').generation, 0);
assert.equal(shortTreeId('hast2_ab12cdef0000'), 'ab12');
console.log('Forex tree label checks passed.');
