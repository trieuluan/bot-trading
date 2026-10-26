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
  if (!groups.length) return <p className="orders-empty-state">Không có setup khớp bộ lọc.</p>;
  return <div className="forex-position-groups" aria-label="Vị thế theo setup">
    {groups.map((group) => {
      const state = groupStatusInfo(group);
      const floating = sumFloating(group, pnlOf);
      const selected = group.legs.some((leg) => leg.position_id === selectedId);
      const active = group.legs.find((leg) => leg.status === 'open') ?? group.legs[0];
      return <article key={group.id} className={`forex-position-group ${selected ? 'is-selected' : ''}`}>
        <div className="position-group-head">
          <div className="position-group-identity">
            <span className={`side-badge side-${group.side ?? 'unknown'}`}>{group.side?.toUpperCase() ?? '—'}</span>
            <strong>{group.ownership_verified ? 'Setup' : 'Ticket'} #{group.legs[0].position_id}</strong>
            <small>{strategyBadge(group.source, group.strategy)} · {group.ticket_count} ticket</small>
            {group.strategy && <code>{group.strategy}</code>}
            <small>{formatTime(group.open_time, true)}</small>
          </div>
          <div className="position-group-metric"><small>LOT MỞ / BAN ĐẦU</small><strong>{number(group.remaining_volume)} / {number(group.initial_volume)}</strong><small>Entry TB {price(group.avg_open_price)}</small></div>
          <div className="position-group-metric"><small>ĐÃ CHỐT (USD)</small><strong className={group.realized_pnl == null ? '' : group.realized_pnl >= 0 ? 'positive' : 'negative'}>{profit(group.realized_pnl)}</strong><small>Profit sàn · chưa tổng mọi phí</small></div>
          <div className="position-group-metric"><small>THẢ NỔI ƯỚC TÍNH (USD)</small><strong className={floating == null ? '' : floating >= 0 ? 'positive' : 'negative'}>{profit(floating)}</strong><small>{group.status === 'open' ? floating == null ? 'Chờ quote / dữ liệu hợp lệ' : 'Theo lot còn mở · chưa phí/swap' : 'Không còn ticket mở'}</small></div>
          <div className="position-group-state"><span className={`order-status-pill status-${group.status}`}>{group.close_requested ? 'Đang yêu cầu đóng' : state.label}</span>{state.sub && <small>{state.sub}</small>}</div>
          <div className="order-actions-group">
            {onDeselect ? (
              <button type="button" className="btn-action" onClick={onDeselect}>Ẩn khỏi chart</button>
            ) : (
              <button type="button" className="btn-action" onClick={() => onSelect(active.position_id)}>Xem chart</button>
            )}
            {closeSetupPath(group) && <button type="button" className="btn-action btn-danger" disabled={closeDisabled || group.close_requested} onClick={() => onCloseSetup(group)}>Đóng cả setup</button>}
          </div>
        </div>
        <details className="position-group-details">
          <summary>Chi tiết {group.ticket_count} ticket · giá khớp, SL/TP & deal sàn</summary>
          <div className="table-wrap"><table className="position-leg-table"><thead><tr><th>Ticket / Vai trò</th><th>Lot mở / đầu</th><th>Vào / Thoát</th><th>SL / TP</th><th>Đã chốt (USD)</th><th>Trạng thái / Thao tác</th></tr></thead>
            <tbody>{group.legs.map((leg) => <tr key={leg.position_id}>
              <td><button className="btn-action" type="button" onClick={() => onSelect(leg.position_id)}>#{leg.position_id} · {leg.role === 'tp1' ? 'TP1' : leg.role === 'runner' ? 'Runner' : 'Ticket'}</button>
                <details><summary>{leg.deals.length} deal sàn</summary>{leg.deals.length ? leg.deals.map((deal) => <div className="position-deal" key={deal.deal_id}>
                  <span>#{deal.deal_id} / Order {deal.order_id ?? '—'}</span><span>{deal.type} · {deal.direction ?? '—'}</span><span>{formatTime(deal.event_time, true)}</span>
                  <span>Giá {deal.price ?? '—'} · Lot {deal.volume ?? '—'}</span><span>Profit {deal.profit?.amount ?? '—'} {deal.profit?.currency ?? ''} · Commission {deal.commission ?? '—'}</span>
                </div>) : <small>Chưa có lịch sử deal.</small>}</details>
              </td>
              <td>{number(leg.remaining_volume)} / {number(leg.initial_volume)}</td>
              <td>{price(leg.open_price)}<small>{price(leg.close_price)}</small></td>
              <td>{price(leg.stop)}<small>{price(leg.target)}</small></td>
              <td>{profit(leg.has_closes ? leg.pnl : leg.initial_volume == null ? null : 0)}</td>
              <td><span>{ticketStatus(leg)}</span>{closeTicketPath(leg) && <button className="btn-action btn-danger" type="button" disabled={closeDisabled || !!leg.close_requested} onClick={() => onCloseTicket(leg)}>Đóng ticket này</button>}</td>
            </tr>)}</tbody>
          </table></div>
        </details>
      </article>;
    })}
  </div>;
}
