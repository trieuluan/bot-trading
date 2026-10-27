import { useEffect, useId, useRef, useState } from 'react';
import type { BrokerPosition } from '../types/exness-execution';
import { ticketRole } from '../lib/forex-position-groups';

export function ChartPositionCluster({
  rows,
  selectedId,
  top,
  left,
  pnlOf,
  onSelect,
  onDetails,
}: {
  rows: BrokerPosition[];
  selectedId?: string;
  top: number;
  left: number;
  pnlOf?: (position: BrokerPosition) => number | null;
  onSelect?: (id: string) => void;
  onDetails?: (id: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const popoverId = useId();
  const popover = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    popover.current?.hidePopover();
  }, [top, left]);

  useEffect(() => {
    const close = () => popover.current?.hidePopover();
    const onWheel = (event: WheelEvent) => {
      if (!popover.current?.contains(event.target as Node)) close();
    };
    window.addEventListener('resize', close);
    window.addEventListener('wheel', onWheel, { passive: true });
    return () => {
      window.removeEventListener('resize', close);
      window.removeEventListener('wheel', onWheel);
    };
  }, []);

  const close = () => {
    popover.current?.hidePopover();
    trigger.current?.focus({ preventScroll: true });
  };

  const hasSelected = rows.some((row) => row.position_id === selectedId);
  const buyCount = rows.filter((r) => r.side === 'buy').length;
  const sellCount = rows.filter((r) => r.side === 'sell').length;
  const sideLabel =
    buyCount > 0 && sellCount > 0
      ? `BUY ${buyCount} / SELL ${sellCount}`
      : buyCount > 0
        ? `BUY ${buyCount}`
        : sellCount > 0 ? `SELL ${sellCount}` : 'Chưa rõ chiều';

  const totalPnl = pnlOf
    ? rows.reduce<number | null>((acc, row) => {
        const val = pnlOf(row);
        return val == null || acc == null ? null : acc + val;
      }, 0)
    : null;

  const tone = totalPnl == null ? 'flat' : totalPnl >= 0 ? 'win' : 'loss';

  return (
    <div
      className={`position-tag ${tone} ${hasSelected ? 'is-selected border-sky-400 ring-1 ring-sky-400/30' : 'border-border-active'} pointer-events-auto absolute flex h-11 max-w-[calc(100%-24px)] items-center gap-2 rounded-lg border bg-surface-2 px-2 py-1 text-xs`}
      style={{ top, left }}
    >
      <button
        ref={trigger}
        type="button"
        className="position-tag-select flex min-w-0 items-center gap-2 text-left focus-visible:outline-2 focus-visible:outline-sky-400"
        popoverTarget={popoverId}
        aria-expanded={open}
        aria-label={`Nhóm ${rows.length} vị thế`}
        onClick={() => {
          const panel = popover.current;
          const anchor = trigger.current?.getBoundingClientRect();
          if (!panel || !anchor) return;
          const below = window.innerHeight - anchor.bottom - 12;
          const above = anchor.top - 12;
          const height = Math.min(360, Math.max(below, above));
          panel.style.left = `${Math.max(12, Math.min(anchor.left, window.innerWidth - 372))}px`;
          panel.style.top = `${below >= Math.min(240, height) ? anchor.bottom + 6 : Math.max(12, anchor.top - height - 6)}px`;
          panel.style.maxHeight = `${Math.max(80, below >= Math.min(240, height) ? Math.min(360, below - 6) : height)}px`;
        }}
      >
        <span className="tag-vol flex flex-col text-xs [&>small]:text-text-muted">
          <span>
            {rows.length} vị thế · {sideLabel}
          </span>
          <small className="font-mono">{totalPnl == null ? 'P&L chưa có dữ liệu' : `≈ ${totalPnl >= 0 ? '+' : '−'}${Math.abs(totalPnl).toFixed(2)} USD`}</small>
        </span>
        <span aria-hidden="true" className="text-text-muted">{open ? '▴' : '▾'}</span>
      </button>

        <div
          ref={popover}
          id={popoverId}
          popover="auto"
          onToggle={(event) => setOpen(event.newState === 'open')}
          className="position-cluster-popover fixed inset-auto m-0 w-[360px] max-w-[calc(100vw-24px)] overflow-y-auto overscroll-contain rounded-xl border border-border-active bg-surface-1 p-2 text-xs text-text-base shadow-2xl motion-safe:animate-in motion-safe:fade-in motion-safe:zoom-in-95 motion-safe:duration-150"
          role="dialog"
          aria-label="Danh sách vị thế trong nhóm"
        >
          <div className="flex items-center justify-between border-b border-border-base px-2 py-1 text-2xs font-semibold text-text-muted">
            <span>{rows.length} VỊ THẾ GẦN NHAU</span>
            <button
              type="button"
              className="text-text-muted hover:text-text-base"
              onClick={close}
              aria-label="Đóng popover"
            >
                              ×
            </button>
          </div>
          {rows.map((row) => {
            const isRowSelected = row.position_id === selectedId;
            const rowPnl = pnlOf?.(row) ?? null;
            return (
              <div
                key={row.position_id}
                className={`flex items-center justify-between gap-2 rounded px-2 py-1.5 transition hover:bg-surface-2 ${isRowSelected ? 'bg-sky-500/15 text-sky-200' : ''}`}
              >
                <button
                  type="button"
                  className="flex flex-1 flex-col text-left"
                  onClick={() => {
                    close();
                    onSelect?.(row.position_id);
                  }}
                  aria-pressed={isRowSelected}
                  aria-label={`Chọn vị thế #${row.position_id}`}
                >
                  <span className="font-mono font-medium">
                    {row.side === 'buy' ? 'BUY' : row.side === 'sell' ? 'SELL' : '—'} #{row.position_id}
                    {isRowSelected && ' ✓'}
                  </span>
                  <span className="text-2xs text-text-muted">
                    {ticketRole(row)} · {row.remaining_volume ?? row.volume ?? '—'} lot
                    <span className="block font-mono">Entry {row.open_price ?? '—'}</span>
                  </span>
                </button>
                <div className="flex items-center gap-2">
                  <strong
                    className={`font-mono text-2xs ${rowPnl == null ? 'text-text-muted' : rowPnl >= 0 ? 'text-bull' : 'text-bear'}`}
                  >
                    {rowPnl == null ? '—' : `${rowPnl >= 0 ? '+' : '−'}${Math.abs(rowPnl).toFixed(2)} USD`}
                  </strong>
                  {onDetails && (
                    <button
                      type="button"
                      className="rounded border border-border-base px-1.5 py-0.5 text-2xs text-text-muted hover:border-border-active hover:text-text-base"
                      onClick={() => {
                        close();
                        onDetails(row.position_id);
                      }}
                      aria-label={`Xem chi tiết #${row.position_id}`}
                    >
                      Chi tiết
                    </button>
                  )}
                </div>
              </div>
            );
          })}
          <p className="mt-1 border-t border-border-base px-2 pt-2 text-text-muted whitespace-normal">P&L ước tính, chưa trừ phí/swap. Chọn chỉ hiển thị trên chart, không đóng hoặc hủy lệnh.</p>
        </div>
    </div>
  );
}
