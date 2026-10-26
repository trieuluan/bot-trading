import assert from 'node:assert/strict';
import { test } from 'node:test';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

test('hiding selected setup only clears selection, never closes broker tickets', async () => {
  const server = await createServer({
    root: fileURLToPath(new URL('../', import.meta.url)),
    server: { middlewareMode: true, hmr: false, ws: false },
    appType: 'custom',
  });
  try {
    const { ForexPositionGroups } = await server.ssrLoadModule('/src/components/ForexPositionGroups.tsx');
    let selectedId = '123';
    let closes = 0;
    const props = {
      groups: [{
        id: 'setup:demo:USDJPY:100', symbol: 'USDJPY', signal_time: 100,
        side: 'sell', ownership_verified: true, source: 'learning', status: 'open',
        open_count: 1, ticket_count: 1, legs: [{ position_id: '123', status: 'open', deals: [] }],
      }],
      selectedId,
      onSelect: (identifier) => { selectedId = identifier; },
      onDeselect: () => { selectedId = undefined; },
      onCloseTicket: () => { closes += 1; },
      onCloseSetup: () => { closes += 1; },
      closeDisabled: false,
      pnlOf: () => null,
    };
    const buttons = [];
    function visit(element) {
      if (!React.isValidElement(element)) return;
      if (element.type === 'button') buttons.push(element);
      React.Children.forEach(element.props.children, visit);
    }
    visit(ForexPositionGroups(props));
    const hide = buttons.find((button) => button.props.children === 'Ẩn khỏi chart');
    assert.ok(hide);
    hide.props.onClick();
    assert.equal(selectedId, undefined);
    assert.equal(closes, 0);
    const list = renderToStaticMarkup(React.createElement(ForexPositionGroups, { ...props, onDeselect: undefined }));
    assert.ok(list.includes('Xem chart'));
    assert.ok(!list.includes('Ẩn khỏi chart'));
  } finally {
    await server.close();
  }
});
