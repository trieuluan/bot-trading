import test from 'node:test';
import assert from 'node:assert/strict';
import { positionGroupId, groupStatusInfo, ticketStatus, closeTicketPath, closeSetupPath, estimatedPnl, sumFloating, strategyBadge } from '../src/lib/forex-position-groups.ts';

test('only verified ownership provides setup identity; heuristic lookalikes stay independent', () => {
  const base = { symbol: 'XAUUSD', signal_time: 100, strategy: 'same', side: 'buy', setup_id: 'setup:acc:XAUUSD:100' };
  assert.notEqual(positionGroupId({ ...base, position_id: '1' }), positionGroupId({ ...base, position_id: '2' }));
  assert.equal(positionGroupId({ ...base, position_id: '1', ownership_verified: true }), base.setup_id);
  assert.notEqual(positionGroupId({ ...base, position_id: '1', symbol: 'EURUSD' }), positionGroupId({ ...base, position_id: '1' }));
});

test('TP confirmation, manual, SL, unknown closure and incomplete history remain distinct', () => {
  const leg = { role: 'tp1', status: 'closed', partial_taken: true };
  assert.equal(ticketStatus(leg), 'Đã đóng · chưa rõ lý do');
  assert.equal(ticketStatus({ ...leg, take_profit_filled: true }), 'Đã chốt TP1');
  assert.equal(ticketStatus({ ...leg, close_reason: 'stop_loss' }), 'Đã cắt SL');
  assert.equal(ticketStatus({ ...leg, close_requested: true }), 'Đóng thủ công');
  assert.equal(ticketStatus({ status: 'history_incomplete' }), 'Lịch sử chưa đủ');
  assert.equal(groupStatusInfo({ status: 'open', open_count: 1, ticket_count: 2, tp1_taken: true }).label, 'Đang mở · 1/2 ticket');
  assert.equal(strategyBadge('learning', 'hast4_abc'), 'Bot · HTF V4');
});

test('close actions have separate paths; unverified or closed objects cannot request closure', () => {
  const base = { symbol: 'XAUUSD', signal_time: 100, status: 'open', ownership_verified: true, position_id: '123' };
  assert.equal(closeSetupPath(base), '/trades/100/close');
  assert.equal(closeTicketPath(base), '/trades/100/close?position_id=123');
  assert.equal(closeTicketPath({ ...base, ownership_verified: false }), null);
  assert.equal(closeSetupPath({ ...base, ownership_verified: false }), null);
  assert.equal(closeTicketPath({ ...base, position_id: '123?unsafe=1' }), null);
  assert.equal(closeTicketPath({ ...base, status: 'closed' }), null);
});

test('floating uses remaining volume, missing data stays unknown and never substitutes realized P&L', () => {
  const profile = { contract_size: 100, quote_to_account_gain: 1, quote_to_account_loss: 1 };
  const leg = { status: 'open', side: 'buy', volume: 0.1, initial_volume: 0.1, remaining_volume: 0.02, open_price: 4000, pnl: 50 };
  assert.equal(estimatedPnl(leg, 4010, profile), 20);
  assert.equal(estimatedPnl({ ...leg, remaining_volume: null }, 4010, profile), null);
  assert.equal(estimatedPnl(leg, 4010, {}), null);
  const group = { legs: [leg, { status: 'closed', pnl: 100 }] };
  assert.equal(sumFloating(group, (position) => estimatedPnl(position, 4010, profile)), 20);
  assert.equal(sumFloating({ legs: [leg, leg] }, () => null), null);
});
