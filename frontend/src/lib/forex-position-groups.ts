import type { BrokerPosition } from '../types/exness-execution';

export interface PositionGroup {
  id: string;
  symbol: string;
  signal_time: number | null;
  strategy?: string;
  source: string;
  side: 'buy' | 'sell' | null;
  ownership_verified: boolean;
  status: 'open' | 'closed' | 'history_incomplete';
  open_count: number;
  ticket_count: number;
  initial_volume: number | null;
  remaining_volume: number | null;
  avg_open_price: number | null;
  open_time: number | null;
  realized_pnl: number | null;
  history_incomplete: boolean;
  tp1_taken: boolean;
  close_requested: boolean;
  legs: BrokerPosition[];
}

export function positionGroupId(position: BrokerPosition): string {
  return position.ownership_verified && position.setup_id
    ? position.setup_id
    : `ticket:${position.symbol ?? 'unknown'}:${position.position_id}`;
}

export function groupStatusInfo(group: PositionGroup): { label: string; sub: string } {
  if (group.status === 'history_incomplete') return { label: 'Lịch sử chưa đủ', sub: 'Chờ đối soát sàn' };
  if (group.status === 'closed') return { label: 'Đã đóng', sub: group.tp1_taken ? 'Đã chốt TP1' : '' };
  return {
    label: `Đang mở · ${group.open_count}/${group.ticket_count} ticket`,
    sub: group.tp1_taken ? 'Đã chốt TP1 · Runner đang mở' : group.history_incomplete ? 'Lịch sử chưa đủ' : '',
  };
}

export function ticketRole(position: BrokerPosition): string {
  switch (position.role) {
    case 'tp1': return 'TP1';
    case 'runner': return 'Runner';
    case 'partial1': return 'Chốt phần 1';
    case 'partial2': return 'Chốt phần 2';
    default: return 'Chưa rõ vai trò';
  }
}

export function ticketStatus(position: BrokerPosition): string {
  if (position.status === 'open') return position.close_requested ? 'Đang yêu cầu đóng' : 'Đang mở';
  if (position.status !== 'closed') return 'Lịch sử chưa đủ';
  if (position.take_profit_filled) return position.role === 'tp1' ? 'Đã chốt TP1' : 'Đã chốt TP';
  if (position.close_reason === 'stop_loss') return 'Đã cắt SL';
  if (position.close_reason === 'weak_close') return 'Đã đóng · tín hiệu suy yếu';
  if (position.close_reason === 'scale_out') return 'Đã chốt từng phần';
  if (position.close_requested || position.close_reason === 'manual') return 'Đóng thủ công';
  return position.close_reason ? `Đã đóng · ${position.close_reason}` : 'Đã đóng · chưa rõ lý do';
}

export function strategyBadge(source: string, strategy?: string): string {
  if (strategy?.startsWith('hast4_')) return 'Bot · HTF V4';
  if (strategy?.startsWith('hast3_')) return 'Bot · HTF V3';
  if (source === 'learning') return 'Bot · Learner';
  if (source === 'regime_router') return 'Bot · Cũ';
  return 'Thủ công / Khác';
}

export function closeTicketPath(position: BrokerPosition): string | null {
  return position.ownership_verified && Number.isInteger(position.signal_time) &&
    /^\d+$/.test(position.position_id) && position.status === 'open'
    ? `/trades/${position.signal_time}/close?position_id=${position.position_id}` : null;
}

export function closeSetupPath(group: PositionGroup): string | null {
  return group.ownership_verified && Number.isInteger(group.signal_time) && group.status === 'open'
    ? `/trades/${group.signal_time}/close` : null;
}

export function sumFloating(group: PositionGroup, pnlOf: (position: BrokerPosition) => number | null): number | null {
  const values = group.legs.filter((leg) => leg.status === 'open').map(pnlOf);
  return values.length && values.every((value) => value != null && Number.isFinite(value))
    ? values.reduce<number>((total, value) => total + value!, 0) : null;
}

export function estimatedPnl(position: BrokerPosition, price: number, profile?: Record<string, unknown> | null): number | null {
  const volume = 'remaining_volume' in position ? position.remaining_volume : position.volume;
  const contract = Number(profile?.contract_size);
  if (!['buy', 'sell'].includes(position.side ?? '') ||
    ![volume, contract, position.open_price, price].every((value) => typeof value === 'number' && Number.isFinite(value) && value > 0)) return null;
  const move = position.side === 'buy' ? price - position.open_price! : position.open_price! - price;
  const conversion = Number(move >= 0 ? profile?.quote_to_account_gain : profile?.quote_to_account_loss);
  return Number.isFinite(conversion) && conversion > 0 ? move * volume! * contract * conversion : null;
}
