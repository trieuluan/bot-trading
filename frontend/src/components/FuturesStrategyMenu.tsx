import { useId, useRef, useState } from 'react';
import { Layers, Search, X } from 'lucide-react';
import type { FuturesStrategyObservation } from '../types/paper';
import { strategyColor } from './PaperChart';

export function FuturesStrategyMenu({
  rows,
  selected,
  onChange,
}: {
  rows: FuturesStrategyObservation[];
  selected: string[];
  onChange: (names: string[]) => void;
}) {
  const id = useId();
  const trigger = useRef<HTMLButtonElement>(null);
  const [query, setQuery] = useState('');
  const [filter, setFilter] = useState('all');
  const [position, setPosition] = useState({ top: 0, left: 0 });
  const visible = rows.filter(
    (row) =>
      (filter !== 'selected' || selected.includes(row.strategy)) &&
      (filter !== 'signal' || row.status === 'signal') &&
      row.strategy.replace(/_/g, ' ').toLowerCase().includes(query.trim().toLowerCase()),
  );
  return (
    <div className="strategy-menu-wrapper">
      <button
        ref={trigger}
        type="button"
        className={`strategy-menu-trigger ${selected.length ? 'is-active' : ''}`}
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
        <Layers size={13} /> Chiến thuật <span className="strategy-badge">{selected.length}</span>
      </button>
      <div
        id={id}
        popover="auto"
        role="dialog"
        aria-label="Lớp chiến thuật Futures trên chart"
        className="strategy-popover-dropdown futures-strategy-menu"
        style={position}
      >
        <div className="strategy-popover-header">
          <div className="popover-title-row">
            <strong>Lớp chiến thuật trên chart</strong>
            <button
              type="button"
              className="search-clear-btn"
              popoverTarget={id}
              popoverTargetAction="hide"
              aria-label="Đóng chọn chiến thuật"
            >
              <X size={16} />
            </button>
          </div>
          <div className="strategy-search-bar">
            <Search size={14} />
            <input
              autoFocus
              aria-label="Tìm chiến thuật"
              placeholder="Tìm tên hoặc mã biến thể…"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />
          </div>
          <div className="strategy-filter-tabs" role="group" aria-label="Lọc chiến thuật">
            {[
              ['all', `Tất cả (${rows.length})`],
              ['signal', `Tín hiệu (${rows.filter((row) => row.status === 'signal').length})`],
              ['selected', `Đang vẽ (${selected.length})`],
            ].map(([key, label]) => (
              <button
                key={key}
                type="button"
                className={filter === key ? 'active' : ''}
                aria-pressed={filter === key}
                onClick={() => setFilter(key)}
              >
                {label}
              </button>
            ))}
          </div>
        </div>
        <div className="strategy-popover-body">
          {visible.map((row) => {
            const checked = selected.includes(row.strategy);
            const [family, code] = row.strategy.split(':');
            return (
              <label
                key={row.strategy}
                className={`strategy-item-row ${checked ? 'is-selected' : ''}`}
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
                <div className="strategy-item-meta">
                  <div className="strategy-item-title-line">
                    <span className="strategy-item-name" title={row.strategy}>
                      <span style={{ color: strategyColor(row.strategy) }}>■</span>{' '}
                      {family.replace(/_/g, ' ')}
                    </span>
                    <span className="status-pill-sub">
                      {row.status === 'signal'
                        ? 'Có tín hiệu'
                        : row.status === 'warming_up'
                          ? 'Khởi tạo'
                          : 'Đang chờ'}
                    </span>
                  </div>
                  <small className="strategy-item-sub">
                    {code ? `#${code} · ` : ''}
                    {row.side?.toUpperCase() ?? 'Chưa có hướng'}
                  </small>
                  <div className="strategy-item-reason" title={row.setup?.reason ?? row.note}>
                    {row.setup?.reason ?? row.note}
                  </div>
                </div>
              </label>
            );
          })}
          {!visible.length && <p className="strategy-menu-empty">Không có chiến thuật phù hợp.</p>}
        </div>
        <div className="strategy-popover-footer">
          <span>Chỉ đổi lớp vẽ, không đổi bot.</span>
          <button
            type="button"
            className="popover-clear-btn"
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
