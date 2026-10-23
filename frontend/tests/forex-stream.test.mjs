import assert from 'node:assert/strict';
import { test } from 'node:test';
import { subscribeForexStream } from '../src/lib/forex-stream.ts';

class Source {
  static CLOSED = 2;
  static instances = [];
  readyState = 1;
  handlers = new Map();
  closed = false;
  constructor(url) {
    this.url = url;
    Source.instances.push(this);
  }
  addEventListener(name, handler) {
    this.handlers.set(name, handler);
  }
  emit(name, body) {
    this.handlers.get(name)?.({ data: JSON.stringify(body) });
  }
  close() {
    this.closed = true;
    this.readyState = Source.CLOSED;
  }
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

test('shared SSE, rapid switch, wrong symbol, cache, errors, cleanup; never REST polling', async () => {
  globalThis.EventSource = Source;
  globalThis.document = { hidden: false, addEventListener() {}, removeEventListener() {} };
  globalThis.fetch = () => {
    throw new Error('REST polling is forbidden');
  };
  const dashboard = [],
    execution = [],
    status = [],
    connecting = [];
  const stopDashboard = subscribeForexStream(
    'XAUUSD',
    'dashboard',
    (value) => dashboard.push(value),
    () => {},
  );
  const stopExecution = subscribeForexStream(
    'XAUUSD',
    'execution',
    (value) => execution.push(value),
    (value, pending) => {
      status.push(value);
      connecting.push(pending);
    },
  );
  assert.equal(connecting.at(-1), true, 'initial subscription is loading, not a connection error');
  await sleep(250);
  assert.equal(Source.instances.length, 1);
  const source = Source.instances[0];
  assert.match(source.url, /parts=dashboard%2Cexecution/);
  source.emit('dashboard', { symbol: 'XAUUSD', data: { test: 1 } });
  assert.equal(status.at(-1), false, 'dashboard must not mark execution as fresh');
  assert.equal(connecting.at(-1), true, 'execution stays loading until its own snapshot arrives');
  source.emit('execution', { symbol: 'EURUSD', data: { test: 2 } });
  assert.equal(execution.length, 0);
  source.emit('execution', { symbol: 'XAUUSD', data: { test: 3 } });
  assert.deepEqual(execution, [{ test: 3 }]);
  assert.equal(status.at(-1), true);
  assert.equal(connecting.at(-1), false);
  source.emit('unavailable', { symbol: 'XAUUSD', part: 'execution' });
  source.emit('heartbeat', {});
  assert.equal(status.at(-1), false, 'heartbeat must not hide part failure');
  assert.equal(connecting.at(-1), false, 'failed snapshot is an error, not loading');
  source.emit('execution', { symbol: 'XAUUSD', data: { test: 4 } });
  assert.equal(status.at(-1), true);
  source.onerror();
  assert.equal(status.at(-1), false);
  assert.equal(connecting.at(-1), false, 'network failure must still surface as an error');
  stopDashboard();
  stopExecution();
  assert.equal(source.closed, true);
  source.emit('execution', { symbol: 'XAUUSD', data: { test: 99 } });
  assert.equal(execution.length, 2, 'old stream may not update an unsubscribed symbol');
  const beforeSwitch = Source.instances.length;
  for (const symbol of ['EURUSD', 'GBPUSD', 'USDJPY']) {
    const stop = subscribeForexStream(
      symbol,
      'execution',
      () => {},
      () => {},
    );
    stop();
  }
  await sleep(250);
  assert.equal(Source.instances.length, beforeSwitch, 'click-through must not open streams');
  const replayed = [];
  const stop = subscribeForexStream(
    'XAUUSD',
    'execution',
    (value) => replayed.push(value),
    () => {},
  );
  assert.deepEqual(replayed, [{ test: 4 }]);
  stop();
});
