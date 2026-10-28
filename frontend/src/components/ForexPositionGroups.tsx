import type { BrokerPosition } from '../types/exness-execution';
import { closeSetupPath, closeTicketPath, groupStatusInfo, strategyBadge, sumFloating, ticketRole, ticketStatus, type PositionGroup } from '../lib/forex-position-groups';
import { formatForexPrice } from '../lib/format';
import { formatTime } from '../lib/time-format';
import { Eye, Tag, XCircle } from 'lucide-react';

const number = (value: number | null | undefined) =>
  value == null || !Number.isFinite(value) ? '—' : value.toLocaleString('vi-VN', { maximumFractionDigits: 4 });
const price = (value: number | null | undefined) => value == null ? '—' : formatForexPrice(value);
const profit = (value: number | null) => value == null || !Number.isFinite(value) ? '—' : `${value > 0 ? '+' : ''}${value.toFixed(2)}`;

export function ForexPositionGroups({ groups, selectedId, onSelect, onDeselect, onCloseTicket, onCloseSetup, closeDisabled, pnlOf }: {
  groups: PositionGroup[];
  selectedId?: string;
  /** scope: 'setup' = chart hiện cả setup; 'ticket' = chỉ ticket được bấm. */
  onSelect: (id: string, scope?: 'ticket' | 'setup') => void;
  onDeselect?: () => void;
  onCloseTicket: (position: BrokerPosition) => void;
  onCloseSetup: (group: PositionGroup) => void;
  closeDisabled: boolean;
  pnlOf: (position: BrokerPosition) => number | null;
}) {
  if (!groups.length) {
    return (
      <div className="orders-empty-state flex min-h-36 flex-col items-center justify-center gap-2 rounded-xl border border-dashed border-border-base bg-surface-2/20 p-6 text-center text-xs text-text-muted">
        <Tag size={20} className="text-text-muted/60" aria-hidden="true" />
        <p className="font-medium text-text-secondary">Không có setup khớp bộ lọc</p>
        <p className="max-w-md text-[11px] leading-relaxed text-text-muted">
          Thử đổi bộ lọc sang &quot;Tất cả&quot; hoặc chờ bot phát hiện tín hiệu đạt chuẩn trên các nến M15/H1.
        </p>
      </div>
    );
  }

  return (
    <div className="forex-position-groups max-h-[65dvh] space-y-2.5 overflow-y-auto" aria-label="Vị thế theo setup">
      {groups.map((group) => {
        const state = groupStatusInfo(group);
        const floating = sumFloating(group, pnlOf);
        const selected = group.legs.some((leg) => leg.position_id === selectedId);
        const active = group.legs.find((leg) => leg.status === 'open') ?? group.legs[0];
        const isBuy = group.side === 'buy';

        return (
          <article
            key={group.id}
            className={`forex-position-group min-w-0 rounded-xl border bg-surface-1 transition-all ${
              selected
                ? 'is-selected border-bull/50 ring-1 ring-bull/30 shadow-md shadow-bull/5'
                : 'border-border-base hover:border-border-active'
            }`}
          >
            <div className="position-group-head grid gap-3 p-3.5 sm:grid-cols-2 lg:grid-cols-[minmax(180px,1.2fr)_repeat(3,minmax(110px,1fr))_minmax(130px,1fr)_auto]">
              <div className="position-group-identity min-w-0 space-y-1.5">
                <div className="flex flex-wrap items-center gap-2">
                  <span
                    className={`inline-flex items-center gap-1 rounded-md px-2 py-0.5 font-mono text-[11px] font-bold uppercase tracking-wider ${
                      isBuy
                        ? 'border border-bull/30 bg-bull/10 text-bull'
                        : 'border border-bear/30 bg-bear/10 text-bear'
                    }`}
                  >
                    {group.side?.toUpperCase() ?? '—'}
                  </span>
                  <strong className="font-mono text-sm font-semibold text-text-primary">
                    {group.ownership_verified ? 'Setup' : 'Ticket'} #{group.legs[0].position_id}
                  </strong>
                  <span className="rounded bg-surface-3 px-1.5 py-0.5 text-[10px] text-text-muted">
                    {group.ticket_count} ticket
                  </span>
                </div>
                <div className="flex flex-wrap items-center gap-2 text-xs text-text-muted">
                  <span className="font-medium text-text-secondary">
                    {strategyBadge(group.source, group.strategy)}
                  </span>
                  <span>·</span>
                  <span className="font-mono tabular-nums">{formatTime(group.open_time, true)}</span>
                </div>
                {group.strategy && (
                  <code className="block truncate font-mono text-[11px] text-text-muted" title={group.strategy}>
                    {group.strategy}
                  </code>
                )}
              </div>

              <div className="position-group-metric flex flex-col justify-center rounded-lg bg-surface-2/40 px-2.5 py-1.5 text-xs">
                <small className="text-[10px] uppercase tracking-wide text-text-muted">Lot mở / Đầu</small>
                <strong className="mt-0.5 font-mono text-sm font-semibold text-text-primary tabular-nums">
                  {number(group.remaining_volume)} / {number(group.initial_volume)}
                </strong>
                <small className="mt-0.5 font-mono text-[11px] text-text-muted tabular-nums">
                  Entry TB: {price(group.avg_open_price)}
                </small>
              </div>

              <div className="position-group-metric flex flex-col justify-center rounded-lg bg-surface-2/40 px-2.5 py-1.5 text-xs">
                <small className="text-[10px] uppercase tracking-wide text-text-muted">Đã chốt (USD)</small>
                <strong
                  className={`mt-0.5 font-mono text-sm font-semibold tabular-nums ${
                    group.realized_pnl == null
                      ? 'text-text-muted'
                      : group.realized_pnl >= 0
                        ? 'positive text-bull'
                        : 'negative text-bear'
                  }`}
                >
                  {profit(group.realized_pnl)}
                </strong>
                <small className="mt-0.5 text-[10px] text-text-muted">Profit sàn · chưa swap</small>
              </div>

              <div className="position-group-metric flex flex-col justify-center rounded-lg bg-surface-2/40 px-2.5 py-1.5 text-xs">
                <small className="text-[10px] uppercase tracking-wide text-text-muted">Thả nổi ước tính</small>
                <strong
                  className={`mt-0.5 font-mono text-sm font-semibold tabular-nums ${
                    floating == null
                      ? 'text-text-muted'
                      : floating >= 0
                        ? 'positive text-bull'
                        : 'negative text-bear'
                  }`}
                >
                  {profit(floating)}
                </strong>
                <small className="mt-0.5 truncate text-[10px] text-text-muted">
                  {group.status === 'open' ? (floating == null ? 'Chờ quote nến' : 'Theo lot còn mở') : 'Đã đóng hết'}
                </small>
              </div>

              <div className="position-group-state flex flex-col justify-center gap-1 text-xs">
                <span
                  className={`order-status-pill status-${group.status} inline-flex w-fit items-center gap-1.5 rounded-full px-2 py-0.5 text-[11px] font-medium ${
                    group.status === 'open'
                      ? 'border border-bull/25 bg-bull/10 text-bull'
                      : group.status === 'closed'
                        ? 'border border-border-subtle bg-surface-3 text-text-muted'
                        : 'border border-amber-400/25 bg-amber-400/10 text-amber-200'
                  }`}
                >
                  <span
                    className={`size-1.5 rounded-full ${
                      group.status === 'open'
                        ? 'bg-bull'
                        : group.status === 'closed'
                          ? 'bg-text-muted'
                          : 'bg-amber-400'
                    }`}
                  />
                  {group.close_requested ? 'Đang yêu cầu đóng' : state.label}
                </span>
                {state.sub && <small className="text-[11px] text-text-muted">{state.sub}</small>}
              </div>

              <div className="order-actions-group flex flex-wrap items-center justify-end gap-2 sm:col-span-2 lg:col-span-1">
                {onDeselect ? (
                  <button
                    type="button"
                    className="btn-action inline-flex min-h-8 items-center gap-1.5 rounded-lg border border-border-base bg-surface-2 px-3 text-xs font-medium text-text-secondary transition-colors hover:border-border-active hover:bg-surface-3"
                    onClick={onDeselect}
                  >
                    Ẩn khỏi chart
                  </button>
                ) : (
                  <button
                    type="button"
                    className="btn-action inline-flex min-h-8 items-center gap-1.5 rounded-lg border border-border-base bg-surface-2 px-3 text-xs font-medium text-text-secondary transition-colors hover:border-border-active hover:bg-surface-3"
                    onClick={() => onSelect(active.position_id, 'setup')}
                  >
                    Xem chart
                  </button>
                )}
                {closeSetupPath(group) && (
                  <button
                    type="button"
                    className="btn-action btn-danger inline-flex min-h-8 items-center gap-1.5 rounded-lg border border-bear/30 bg-bear/10 px-3 text-xs font-medium text-bear transition-colors hover:bg-bear/20 disabled:cursor-not-allowed disabled:opacity-40"
                    disabled={closeDisabled || group.close_requested}
                    onClick={() => onCloseSetup(group)}
                  >
                    <XCircle size={13} aria-hidden="true" />
                    Đóng cả setup
                  </button>
                )}
              </div>
            </div>

            <details className="position-group-details group border-t border-border-subtle bg-surface-1/50 px-3.5 py-2.5">
              <summary className="flex min-h-8 cursor-pointer list-none items-center gap-2 text-xs text-text-secondary hover:text-text-primary [&::-webkit-details-marker]:hidden">
                <span
                  aria-hidden="true"
                  className="inline-block text-text-muted transition-transform group-open:rotate-90"
                >
                  ›
                </span>
                <span className="font-medium">
                  Chi tiết {group.ticket_count} ticket · giá khớp, SL/TP &amp; đối soát deal
                </span>
              </summary>
              <div className="table-wrap mt-2 min-w-0 max-w-full overflow-x-auto rounded-lg border border-border-subtle bg-surface-2/30">
                <table className="position-leg-table w-full border-collapse whitespace-nowrap text-left text-xs">
                  <thead className="border-b border-border-subtle bg-surface-3/60 font-mono text-[11px] text-text-muted">
                    <tr>
                      <th className="px-3 py-2">Ticket / Vai trò</th>
                      <th className="px-3 py-2">Lot mở / Đầu</th>
                      <th className="px-3 py-2">Vào / Thoát</th>
                      <th className="px-3 py-2">SL / TP</th>
                      <th className="px-3 py-2">Đã chốt (USD)</th>
                      <th className="px-3 py-2 text-right">Trạng thái / Thao tác</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-border-subtle font-mono text-[11px]">
                    {group.legs.map((leg) => {
                      const isTp1 = leg.role === 'tp1';
                      return (
                        <tr key={leg.position_id} className="hover:bg-surface-3/30">
                          <td className="px-3 py-2.5">
                            <div className="flex flex-col gap-1">
                              <div className="flex items-center gap-1.5">
                                <button
                                  type="button"
                                  className="btn-action rounded border border-border-base bg-surface-2 px-1.5 py-0.5 font-semibold text-text-primary hover:border-border-active"
                                  onClick={() => onSelect(leg.position_id, 'ticket')}
                                >
                                  #{leg.position_id}
                                </button>
                                <span
                                  className={`rounded px-1.5 py-0.5 text-[10px] font-bold uppercase ${
                                    isTp1
                                      ? 'border border-sky-400/25 bg-sky-400/10 text-sky-300'
                                      : 'border border-purple-400/25 bg-purple-400/10 text-purple-300'
                                  }`}
                                >
                                  {ticketRole(leg)}
                                </span>
                              </div>
                              <details className="mt-1 text-[11px]">
                                <summary className="cursor-pointer text-text-muted hover:text-text-secondary">
                                  {leg.deals.length} deal sàn
                                </summary>
                                {leg.deals.length > 0 ? (
                                  <div className="mt-1 space-y-1 rounded border border-border-subtle bg-surface-1 p-2 font-mono text-[10px] text-text-secondary">
                                    {leg.deals.map((deal) => (
                                      <div
                                        key={deal.deal_id}
                                        className="position-deal flex flex-wrap items-center gap-2 border-b border-border-subtle/40 pb-1 last:border-0 last:pb-0"
                                      >
                                        <span className="text-text-muted">#{deal.deal_id}</span>
                                        <span className="font-semibold uppercase">{deal.type}</span>
                                        <span>{formatTime(deal.event_time, true)}</span>
                                        <span>Giá: {deal.price ?? '—'}</span>
                                        <span>Lot: {deal.volume ?? '—'}</span>
                                        <span className="text-bull">
                                          P&amp;L: {deal.profit?.amount ?? '0.00'}
                                        </span>
                                      </div>
                                    ))}
                                  </div>
                                ) : (
                                  <small className="text-text-muted">Chưa có lịch sử deal.</small>
                                )}
                              </details>
                            </div>
                          </td>
                          <td className="px-3 py-2.5 tabular-nums">
                            {number(leg.remaining_volume)} / {number(leg.initial_volume)}
                          </td>
                          <td className="px-3 py-2.5 tabular-nums">
                            <div>{price(leg.open_price)}</div>
                            {leg.close_price && (
                              <small className="text-text-muted">{price(leg.close_price)}</small>
                            )}
                          </td>
                          <td className="px-3 py-2.5 tabular-nums">
                            <div>SL: {price(leg.stop)}</div>
                            <small className="text-text-muted">TP: {price(leg.target)}</small>
                            {(leg.role === 'partial1' || leg.role === 'partial2') && (
                              <small className="block font-sans text-amber-300/80" title="Không đặt TP riêng trên sàn. Bot tự đóng khi đạt điều kiện chốt phần (weak_close / scale_out); TP sàn chỉ là trần dự phòng.">Bot tự chốt sớm · TP sàn dự phòng</small>
                            )}
                          </td>
                          <td className="px-3 py-2.5 tabular-nums font-semibold">
                            <span
                              className={
                                leg.pnl == null
                                  ? ''
                                  : leg.pnl >= 0
                                    ? 'positive text-bull'
                                    : 'negative text-bear'
                              }
                            >
                              {profit(
                                leg.has_closes
                                  ? leg.pnl
                                  : leg.initial_volume == null
                                    ? null
                                    : 0,
                              )}
                            </span>
                          </td>
                          <td className="px-3 py-2.5 text-right">
                            <div className="flex items-center justify-end gap-2">
                              <span className="text-text-secondary">{ticketStatus(leg)}</span>
                              {closeTicketPath(leg) && (
                                <button
                                  type="button"
                                  className="btn-action btn-danger rounded border border-bear/30 bg-bear/10 px-2 py-1 text-[11px] font-medium text-bear hover:bg-bear/20 disabled:cursor-not-allowed disabled:opacity-40"
                                  disabled={closeDisabled || !!leg.close_requested}
                                  onClick={() => onCloseTicket(leg)}
                                >
                                  Đóng ticket này
                                </button>
                              )}
                            </div>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </details>
          </article>
        );
      })}
    </div>
  );
}
