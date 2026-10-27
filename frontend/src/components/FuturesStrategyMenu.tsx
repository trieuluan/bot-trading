import { useId, useRef, useState } from 'react';
import { Layers, Search, X } from 'lucide-react';
import type { FuturesStrategyObservation } from '../types/paper';
import { strategyColor } from './PaperChart';
import { chartStrategyText, searchText } from '../lib/futures-strategy-info';

export function FuturesStrategyMenu({
  rows,
  holding = [],
  selected,
  onChange,
}: {
  rows: FuturesStrategyObservation[];
  holding?: string[];
  selected: string[];
  onChange: (names: string[]) => void;
}) {
  const id = useId();
  const trigger = useRef<HTMLButtonElement>(null);
  const [query, setQuery] = useState('');
  const [filter, setFilter] = useState('all');
  const [position, setPosition] = useState({ top: 0, left: 0 });
  // Pinned order: holding a position > fresh signal > rest (stable within group).
  const rank = (row: FuturesStrategyObservation) =>
    holding.includes(row.strategy) ? 0 : row.status === 'signal' ? 1 : 2;
  const visible = rows
    .filter(
      (row) =>
        (filter !== 'holding' || holding.includes(row.strategy)) &&
        (filter !== 'selected' || selected.includes(row.strategy)) &&
        (filter !== 'signal' || row.status === 'signal') &&
        chartStrategyText(row).search.includes(searchText(query.trim())),
    )
    .sort((a, b) => rank(a) - rank(b));
  return (
    <div className="strategy-menu-wrapper relative inline-flex min-w-0">
      <button
        ref={trigger}
        type="button"
        className={`strategy-menu-trigger ${selected.length ? 'is-active' : ''} inline-flex items-center gap-2 rounded border border-border-base px-3 py-1.5 text-xs text-text-secondary hover:bg-surface-2`}
        popoverTarget={id}
        aria-haspopup="dialog"
        onClick={() => {
          const rect = trigger.current!.getBoundingClientRect();
          setPosition({
            top: Math.max(8, Math.min(rect.bottom + 8, window.innerHeight - 200)),
            left: Math.max(8, Math.min(rect.right - 400, window.innerWidth - 408)),
          });
        }}
      >
        <Layers size={13} /> Chiến thuật <span className="strategy-badge inline-flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 text-xs font-medium bg-surface-3 text-text-secondary">{selected.length}</span>
      </button>
      <div
        id={id}
        popover="auto"
        role="dialog"
        aria-label="Lớp chiến thuật Futures trên chart"
        className="strategy-popover-dropdown futures-strategy-menu absolute left-0 top-full z-40 mt-2 flex max-h-[70dvh] w-[min(440px,calc(100vw-32px))] flex-col overflow-hidden rounded-lg border border-border-active bg-surface-2 shadow-lg fixed z-50"
        style={position}
      >
        <div className="strategy-popover-header shrink-0 space-y-2 border-b border-border-base p-3">
          <div className="popover-title-row flex items-center justify-between gap-2 text-xs">
            <strong>Lớp chiến thuật trên chart</strong>
            <button
              type="button"
              className="search-clear-btn text-xs text-text-muted hover:text-text-primary"
              popoverTarget={id}
              popoverTargetAction="hide"
              aria-label="Đóng chọn chiến thuật"
            >
              <X size={16} />
            </button>
          </div>
          <div className="strategy-search-bar flex items-center gap-2 [&>input]:min-w-0 [&>input]:flex-1">
            <Search size={14} />
            <input
              autoFocus
              aria-label="Tìm chiến thuật"
              placeholder="Tìm tên, mã cây hoặc điều kiện…"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />
          </div>
          <div className="strategy-filter-tabs flex flex-wrap items-center gap-1 [&>button]:rounded [&>button]:px-2 [&>button]:py-1 [&>button]:text-xs [&>button]:text-text-secondary [&>button:hover]:bg-surface-3 [&>button[aria-pressed=true]]:bg-surface-3 [&>button[aria-pressed=true]]:text-bull border-b border-border-subtle py-1" role="group" aria-label="Lọc chiến thuật">
            {[
              ['all', `Tất cả (${rows.length})`],
              [
                'holding',
                `Có lệnh (${rows.filter((row) => holding.includes(row.strategy)).length})`,
              ],
              ['signal', `Tín hiệu (${rows.filter((row) => row.status === 'signal').length})`],
              ['selected', `Đang vẽ (${selected.length})`],
            ].map(([key, label]) => (
              <button
                key={key}
                type="button"
                className={filter === key ? "active bg-bull/10 text-bull" : ''}
                aria-pressed={filter === key}
                onClick={() => setFilter(key)}
              >
                {label}
              </button>
            ))}
          </div>
        </div>
        <div className="strategy-popover-body min-h-0 overflow-y-auto overscroll-y-contain">
          {visible.map((row) => {
            const checked = selected.includes(row.strategy);
            const code = row.strategy.split(':')[1];
            const text = chartStrategyText(row);
            return (
              <label
                key={row.strategy}
                className={`strategy-item-row ${checked ? 'is-selected' : ''} ${holding.includes(row.strategy) ? 'is-holding' : ''} flex min-w-0 items-start gap-2 border-b border-border-subtle p-3 text-xs hover:bg-surface-3 [&.is-selected]:bg-bull/5 [&_input]:mt-1`}
              >
                <input
                  type="checkbox"
                  checked={checked}
                  onChange={() =>
                    onChange(
                      checked
                        ? selected.filter((name) => name !== row.strategy)
                        : [...selected, row.strategy],
                    )
                  }
                />
                <div className="strategy-item-meta min-w-0 flex-1 space-y-1">
                  <div className="strategy-item-title-line flex flex-wrap items-center gap-2">
                    <span className="strategy-item-name text-xs font-semibold text-text-primary">
                      <span style={{ color: strategyColor(row.strategy) }}>■</span> {text.title}
                    </span>
                    <span className="status-pill-sub inline-flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 text-xs font-medium bg-surface-3 text-text-secondary">
                      {holding.includes(row.strategy)
                        ? 'Đang có lệnh'
                        : row.status === 'signal'
                          ? 'Có tín hiệu'
                          : row.status === 'warming_up'
                            ? 'Khởi tạo'
                            : 'Đang chờ'}
                    </span>
                  </div>
                  <small className="strategy-item-sub text-xs leading-relaxed text-text-muted">
                    {code ? `#${code} · ` : ''}
                    {row.side?.toUpperCase() ?? 'Chưa có hướng'}
                  </small>
                  <div className="strategy-item-reason text-xs leading-relaxed text-text-muted">
                    {row.setup?.reason ?? row.note ?? text.description}
                  </div>
                </div>
              </label>
            );
          })}
          {!visible.length && <p className="strategy-menu-empty grid min-h-24 place-content-center gap-2 p-4 text-center text-xs text-text-muted">Không có chiến thuật phù hợp.</p>}
        </div>
        <div className="strategy-popover-footer shrink-0 border-t border-border-base p-3 text-xs text-text-muted">
          <span>Chỉ đổi lớp vẽ, không đổi bot.</span>
          <button
            type="button"
            className="popover-clear-btn text-xs text-text-muted hover:text-text-primary"
            disabled={!selected.length}
            onClick={() => onChange([])}
          >
            Bỏ chọn tất cả
          </button>
        </div>
      </div>
    </div>
  );
}
