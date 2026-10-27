import assert from 'node:assert/strict';
import fs from 'node:fs';
import { test } from 'node:test';
import { fileURLToPath } from 'node:url';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { createServer } from 'vite';

const root = fileURLToPath(new URL('../', import.meta.url));

test('Drawer is a native right-pinned full-height dialog', async () => {
  const server = await createServer({ root, server: { middlewareMode: true, hmr: false, ws: false }, appType: 'custom' });
  try {
    const { Drawer } = await server.ssrLoadModule('/src/components/Drawer.tsx');
    const html = renderToStaticMarkup(
      React.createElement(Drawer, { label: 'Cách đánh', onClose() {} }, React.createElement('i', { id: 'inner' })),
    );
    assert.ok(html.startsWith('<dialog'));
    assert.match(html, /aria-label="Cách đánh"/);
    for (const cls of ['right-0', 'h-dvh', 'w-[min(560px,100vw)]', 'overflow-y-auto']) assert.ok(html.includes(cls), cls);
    assert.ok(html.includes('id="inner"'));
  } finally {
    await server.close();
  }
});

test('root scroll locks while a modal dialog is open', () => {
  const css = fs.readFileSync(new URL('../src/tailwind.css', import.meta.url), 'utf8');
  assert.match(css, /html:has\(dialog:modal\[open\]\)\s*\{\s*overflow:\s*hidden/);
});
