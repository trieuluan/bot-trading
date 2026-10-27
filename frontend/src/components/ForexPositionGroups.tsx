import type { BrokerPosition } from '../types/exness-execution';
import { closeSetupPath, closeTicketPath, groupStatusInfo, strategyBadge, sumFloating, ticketStatus, type PositionGroup } from '../lib/forex-position-groups';
import { formatForexPrice } from '../lib/format';
import { formatTime } from '../lib/time-format';

const number = (value: number | null | undefined) =>
  value == null || !Number.isFinite(value) ? '—' : value.toLocaleString('vi-VN', { maximumFractionDigits: 4 });
const price = (value: number | null | undefined) => value == null ? '—' : formatForexPrice(value);
const profit = (value: number | null) => value == null || !Number.isFinite(value) ? '—' : `${value > 0 ? '+' : ''}${value.toFixed(2)}`;

export function ForexPositionGroups({ groups, selectedId, onSelect, onDeselect, onCloseTicket, onCloseSetup, closeDisabled, pnlOf }: {
  groups: PositionGroup[];
  selectedId?: string;
  onSelect: (id: string) => void;
  onDeselect?: () => void;
  onCloseTicket: (position: BrokerPosition) => void;
  onCloseSetup: (group: PositionGroup) => void;
  closeDisabled: boolean;
  pnlOf: (position: BrokerPosition) => number | null;
}) {
  if (!groups.length) return <p className="orders-empty-state grid min-h-24 place-content-center gap-2 p-4 text-center text-xs text-text-muted">Không có setup khớp bộ lọc.</p>;
  return <div className="forex-position-groups max-h-[65dvh] space-y-2 overflow-y-auto" aria-label="Vị thế theo setup">
    {groups.map((group) => {
      const state = groupStatusInfo(group);
      const floating = sumFloating(group, pnlOf);
      const selected = group.legs.some((leg) => leg.position_id === selectedId);
      const active = group.legs.find((leg) => leg.status === 'open') ?? group.legs[0];
      return <article key={group.id} className={`forex-position-group ${selected ? 'is-selected' : ''} min-w-0 rounded-lg border border-border-base bg-surface-1 [&.is-selected]:border-bull`}>
        <div className="position-group-head grid gap-3 p-3 sm:grid-cols-3 xl:grid-cols-[minmax(160px,1fr)_repeat(4,minmax(70px,auto))_auto]">
          <div className="position-group-identity min-w-0 space-y-1 [&_small]:block [&_small]:text-xs [&_small]:text-text-muted">
            <span className={`side-badge side-${group.side ?? 'unknown'} inline-flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 text-xs font-medium bg-surface-3 text-text-secondary`}>{group.side?.toUpperCase() ?? '—'}</span>
            <strong>{group.ownership_verified ? 'Setup' : 'Ticket'} #{group.legs[0].position_id}</strong>
            <small>{strategyBadge(group.source, group.strategy)} · {group.ticket_count} ticket</small>
            {group.strategy && <code>{group.strategy}</code>}
            <small>{formatTime(group.open_time, true)}</small>
          </div>
          <div className="position-group-metric flex flex-col gap-1 text-xs [&>small]:text-text-muted [&>strong]:font-mono"><small>LOT MỞ / BAN ĐẦU</small><strong>{number(group.remaining_volume)} / {number(group.initial_volume)}</strong><small>Entry TB {price(group.avg_open_price)}</small></div>
          <div className="position-group-metric flex flex-col gap-1 text-xs [&>small]:text-text-muted [&>strong]:font-mono"><small>ĐÃ CHỐT (USD)</small><strong className={group.realized_pnl == null ? '' : group.realized_pnl >= 0 ? "positive text-bull" : "negative text-bear"}>{profit(group.realized_pnl)}</strong><small>Profit sàn · chưa tổng mọi phí</small></div>
          <div className="position-group-metric flex flex-col gap-1 text-xs [&>small]:text-text-muted [&>strong]:font-mono"><small>THẢ NỔI ƯỚC TÍNH (USD)</small><strong className={floating == null ? '' : floating >= 0 ? "positive text-bull" : "negative text-bear"}>{profit(floating)}</strong><small>{group.status === 'open' ? floating == null ? 'Chờ quote / dữ liệu hợp lệ' : 'Theo lot còn mở · chưa phí/swap' : 'Không còn ticket mở'}</small></div>
          <div className="position-group-state flex flex-wrap items-center gap-2"><span className={`order-status-pill status-${group.status} inline-flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 text-xs font-medium bg-surface-3 text-text-secondary`}>{group.close_requested ? 'Đang yêu cầu đóng' : state.label}</span>{state.sub && <small>{state.sub}</small>}</div>
          <div className="order-actions-group flex flex-wrap items-center gap-2">
            {onDeselect ? (
              <button type="button" className="btn-action inline-flex items-center justify-center gap-1.5 rounded-md border border-border-active bg-surface-2 px-3 py-1.5 text-xs text-text-secondary hover:bg-surface-3" onClick={onDeselect}>Ẩn khỏi chart</button>
            ) : (
              <button type="button" className="btn-action inline-flex items-center justify-center gap-1.5 rounded-md border border-border-active bg-surface-2 px-3 py-1.5 text-xs text-text-secondary hover:bg-surface-3" onClick={() => onSelect(active.position_id)}>Xem chart</button>
            )}
            {closeSetupPath(group) && <button type="button" className="btn-action btn-danger inline-flex items-center justify-center gap-1.5 rounded-md border border-border-active bg-surface-2 px-3 py-1.5 text-xs text-text-secondary hover:bg-surface-3 border-bear/40 text-bear hover:bg-bear/10" disabled={closeDisabled || group.close_requested} onClick={() => onCloseSetup(group)}>Đóng cả setup</button>}
          </div>
        </div>
        <details className="position-group-details border-t border-border-base p-3">
          <summary>Chi tiết {group.ticket_count} ticket · giá khớp, SL/TP & deal sàn</summary>
          <div className="table-wrap min-w-0 max-w-full overflow-x-auto overscroll-x-contain"><table className="position-leg-table w-full border-collapse whitespace-nowrap text-xs"><thead><tr><th>Ticket / Vai trò</th><th>Lot mở / đầu</th><th>Vào / Thoát</th><th>SL / TP</th><th>Đã chốt (USD)</th><th>Trạng thái / Thao tác</th></tr></thead>
            <tbody>{group.legs.map((leg) => <tr key={leg.position_id}>
              <td><button className="btn-action inline-flex items-center justify-center gap-1.5 rounded-md border border-border-active bg-surface-2 px-3 py-1.5 text-xs text-text-secondary hover:bg-surface-3" type="button" onClick={() => onSelect(leg.position_id)}>#{leg.position_id} · {leg.role === 'tp1' ? 'TP1' : leg.role === 'runner' ? 'Runner' : 'Ticket'}</button>
                <details><summary>{leg.deals.length} deal sàn</summary>{leg.deals.length ? leg.deals.map((deal) => <div className="position-deal flex flex-wrap items-center gap-3 py-1 text-xs text-text-secondary" key={deal.deal_id}>
                  <span>#{deal.deal_id} / Order {deal.order_id ?? '—'}</span><span>{deal.type} · {deal.direction ?? '—'}</span><span>{formatTime(deal.event_time, true)}</span>
                  <span>Giá {deal.price ?? '—'} · Lot {deal.volume ?? '—'}</span><span>Profit {deal.profit?.amount ?? '—'} {deal.profit?.currency ?? ''} · Commission {deal.commission ?? '—'}</span>
                </div>) : <small>Chưa có lịch sử deal.</small>}</details>
              </td>
              <td>{number(leg.remaining_volume)} / {number(leg.initial_volume)}</td>
              <td>{price(leg.open_price)}<small>{price(leg.close_price)}</small></td>
              <td>{price(leg.stop)}<small>{price(leg.target)}</small></td>
              <td>{profit(leg.has_closes ? leg.pnl : leg.initial_volume == null ? null : 0)}</td>
              <td><span>{ticketStatus(leg)}</span>{closeTicketPath(leg) && <button className="btn-action btn-danger inline-flex items-center justify-center gap-1.5 rounded-md border border-border-active bg-surface-2 px-3 py-1.5 text-xs text-text-secondary hover:bg-surface-3 border-bear/40 text-bear hover:bg-bear/10" type="button" disabled={closeDisabled || !!leg.close_requested} onClick={() => onCloseTicket(leg)}>Đóng ticket này</button>}</td>
            </tr>)}</tbody>
          </table></div>
        </details>
      </article>;
    })}
  </div>;
}
