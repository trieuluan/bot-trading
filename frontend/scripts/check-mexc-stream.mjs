import assert from 'node:assert/strict';
import { parseMexcCandle, withLiveCandle } from '../src/lib/mexc-stream.ts';

const now = 1_792_221_517_000;
const t = Math.floor(now / 900_000) * 900;
const msg = (over = {}, top = {}, sym = 'BTC_USDT') =>
  JSON.stringify({
    symbol: sym,
    channel: 'push.kline',
    ts: now,
    data: {
      symbol: sym,
      interval: 'Min15',
      t,
      o: 100,
      h: 102,
      l: 99,
      c: 101,
      q: 5,
      ...over,
    },
    ...top,
  });

assert.deepEqual(parseMexcCandle(msg(), now), {
  time: t,
  open: 100,
  high: 102,
  low: 99,
  close: 101,
  volume: 5,
});
assert.equal(parseMexcCandle(msg({ interval: 'Min60' }), now), null);
assert.equal(parseMexcCandle(msg({ h: 100.5 }), now), null);
assert.equal(parseMexcCandle(msg({ t: t - 900 }), now), null);
assert.equal(parseMexcCandle(msg({}, { ts: now - 60_000 }), now), null);
assert.equal(parseMexcCandle(msg({}, { ts: now - 5_001 }), now), null);
assert.equal(parseMexcCandle(msg({}, { ts: now + 1_001 }), now), null);
assert.notEqual(parseMexcCandle(msg({}, { ts: now - 4_999 }), now), null);
assert.equal(parseMexcCandle('not json', now), null);

const closed = [{ time: t - 900, open: 1, high: 2, low: 1, close: 2, volume: 1 }];
const live = parseMexcCandle(msg(), now);
assert.equal(withLiveCandle(closed, live).length, 2);
assert.equal(withLiveCandle([...closed, { ...live, close: 100 }], live).at(-1).close, 101);
assert.equal(withLiveCandle([{ ...live, time: t + 900 }], live).length, 1);
console.log('mexc-stream ok');

function mergeBars(history, recent) {
  const bars = new Map();
  for (const bar of [...history, ...recent]) bars.set(bar.time, bar);
  return [...bars.values()].sort((a, b) => a.time - b.time);
}

const h = [{ time: 100, close: 1 }, { time: 200, close: 2 }];
const r = [{ time: 200, close: 2.5 }, { time: 300, close: 3 }];
const m = mergeBars(h, r);
assert.equal(m.length, 3);
assert.equal(m[1].close, 2.5);
assert.equal(m[2].time, 300);
console.log('mergeBars ok');

assert.equal(parseMexcCandle(msg({}, {}, 'NEAR_USDT'), now, 'NEAR_USDT').close, 101);
assert.equal(parseMexcCandle(msg({}, {}, 'NEAR_USDT'), now, 'BTC_USDT'), null);
console.log('symbol filter ok');
