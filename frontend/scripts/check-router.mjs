import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';

const history = ['?workspace=exness_forex&page=Bot&tab=learning&symbol=XAUUSD'];
let cursor = 0;
const window = {
  location: { pathname: '/', search: history[0], hash: '' },
  history: {
    pushState(_, __, url) {
      history.splice(++cursor, history.length, url.slice(1));
      window.location.search = history[cursor];
    },
    replaceState(_, __, url) {
      history[cursor] = url.slice(1);
      window.location.search = history[cursor];
    },
  },
  dispatchEvent() {},
};
const source = readFileSync(new URL('../src/lib/router.ts', import.meta.url), 'utf8');
const { outputText } = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
});
const exports = {};
vm.runInNewContext(outputText, { exports, require: () => ({}), window, URLSearchParams, Event });
const { parseRoute, setRoute } = exports;
assert.equal(parseRoute('').workspace, 'mexc_futures');
assert.equal(parseRoute('?workspace=exness_forex').symbol, 'XAUUSD');
assert.equal(parseRoute('?workspace=exness_forex&page=Bot&tab=orders&symbol=EURUSD').tab, 'orders');
assert.equal(parseRoute('?workspace=exness_forex&symbol=UNKNOWN').symbol, 'UNKNOWN');
setRoute({ symbol: 'EURUSD' });
setRoute({ symbol: 'GBPJPY' });
assert.equal(parseRoute().symbol, 'GBPJPY');
assert.equal(parseRoute().tab, 'learning');
window.location.search = history[--cursor];
assert.equal(parseRoute().symbol, 'EURUSD');
window.location.search = history[++cursor];
assert.equal(parseRoute().symbol, 'GBPJPY');
setRoute({ page: 'Tổng quan' });
assert.equal(parseRoute().symbol, 'GBPJPY');
assert.equal(parseRoute().tab, undefined);
assert.equal(parseRoute(window.location.search).symbol, 'GBPJPY');
console.log('Router: symbol, refresh, Back/Forward, page and tab checks passed.');
