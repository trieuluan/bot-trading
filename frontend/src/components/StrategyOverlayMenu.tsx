import { useState, useRef, useEffect, useMemo } from 'react';
import { Layers, Search, X } from 'lucide-react';
import type { LearnerAnalysis } from '../types/exness-execution';

export const CONTEXT_STRATEGY_KEY = '__context__';

interface StrategyOverlayMenuProps {
  analysis: LearnerAnalysis | null;
  selected: string[];
  onChange: (selected: string[]) => void;
}

type TabType = 'all' | 'built_in' | 'generated' | 'active';

export function StrategyOverlayMenu({ analysis, selected, onChange }: StrategyOverlayMenuProps) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [tab, setTab] = useState<TabType>('all');
  const wrapperRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (wrapperRef.current && !wrapperRef.current.contains(event.target as Node)) {
        setOpen(false);
      }
    }
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') {
        setOpen(false);
      }
    }
    if (open) {
      document.addEventListener('mousedown', handleClickOutside);
      document.addEventListener('keydown', handleKeyDown);
    }
    return () => {
      document.removeEventListener('mousedown', handleClickOutside);
      document.removeEventListener('keydown', handleKeyDown);
    };
  }, [open]);

  const strategies = analysis?.strategies ?? {};
  const builtInNames = useMemo(
    () => Object.keys(strategies).filter((name) => !name.startsWith('gen_')),
    [strategies],
  );
  const genNames = useMemo(
    () => Object.keys(strategies).filter((name) => name.startsWith('gen_')),
    [strategies],
  );

  const toggle = (name: string) => {
    if (selected.includes(name)) {
      onChange(selected.filter((item) => item !== name));
    } else {
      onChange([...selected, name]);
    }
  };

  const clearAll = () => {
    onChange([]);
  };

  const q = query.trim().toLowerCase();

  const filterList = (list: string[]) => {
    if (!q) return list;
    return list.filter((name) => name.toLowerCase().includes(q));
  };

  const activeCount = selected.length;

  return (
    <div className="strategy-menu-wrapper" ref={wrapperRef}>
      <button
        type="button"
        className={`strategy-menu-trigger ${activeCount > 0 ? 'is-active' : ''}`}
        onClick={() => setOpen((prev) => !prev)}
        aria-expanded={open}
        aria-haspopup="dialog"
        title="Chọn chiến thuật để vẽ phân tích lên chart (mặc định không vẽ)"
      >
        <Layers size={13} className="menu-icon" />
        <span>Chiến thuật</span>
        {activeCount > 0 ? (
          <span className="strategy-badge">{activeCount}</span>
        ) : (
          <span className="strategy-badge-off">Tắt</span>
        )}
      </button>

      {open && (
        <div
          className="strategy-popover-dropdown"
          role="dialog"
          aria-label="Lựa chọn chiến thuật hiển thị trên chart"
        >
          <div className="strategy-popover-header">
            <div className="popover-title-row">
              <span className="popover-title">Lớp chiến thuật trên chart</span>
              {activeCount > 0 && (
                <button
                  type="button"
                  className="popover-clear-btn"
                  onClick={clearAll}
                  title="Tắt tất cả các lớp chiến thuật đang vẽ"
                >
                  Bỏ chọn tất cả ({activeCount})
                </button>
              )}
            </div>

            <div className="strategy-search-bar">
              <Search size={13} className="search-icon" />
              <input
                type="text"
                placeholder="Tìm chiến thuật..."
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                autoFocus
              />
              {query && (
                <button type="button" className="search-clear-btn" onClick={() => setQuery('')}>
                  <X size={12} />
                </button>
              )}
            </div>

            <div className="strategy-filter-tabs">
              <button
                type="button"
                className={tab === 'all' ? 'active' : ''}
                onClick={() => setTab('all')}
              >
                Tất cả ({1 + builtInNames.length + genNames.length})
              </button>
              <button
                type="button"
                className={tab === 'built_in' ? 'active' : ''}
                onClick={() => setTab('built_in')}
              >
                Có sẵn ({builtInNames.length})
              </button>
              <button
                type="button"
                className={tab === 'generated' ? 'active' : ''}
                onClick={() => setTab('generated')}
              >
                Bot sinh ({genNames.length})
              </button>
              <button
                type="button"
                className={tab === 'active' ? 'active' : ''}
                onClick={() => setTab('active')}
              >
                Đang vẽ ({activeCount})
              </button>
            </div>
          </div>

          <div className="strategy-popover-body">
            {/* Context technical levels (Donchian / EMA / Barriers) */}
            {(tab === 'all' ||
              tab === 'built_in' ||
              (tab === 'active' && selected.includes(CONTEXT_STRATEGY_KEY))) &&
              (!q || 'donchian ema20 can h1 h4 tham chieu'.includes(q)) && (
                <div className="strategy-group">
                  <div className="group-heading">Mức kỹ thuật chung</div>
                  <label className="strategy-item-row">
                    <input
                      type="checkbox"
                      checked={selected.includes(CONTEXT_STRATEGY_KEY)}
                      onChange={() => toggle(CONTEXT_STRATEGY_KEY)}
                    />
                    <div className="strategy-item-meta">
                      <div className="strategy-item-name">Donchian20 · EMA20 · Cản H1/H4</div>
                      <div className="strategy-item-sub">Khung tham chiếu kỹ thuật của bot</div>
                    </div>
                  </label>
                </div>
              )}

            {/* Built-in strategies */}
            {(tab === 'all' || tab === 'built_in' || tab === 'active') && (
              <div className="strategy-group">
                <div className="group-heading">
                  Chiến thuật có sẵn ({filterList(builtInNames).length})
                </div>
                {filterList(
                  tab === 'active'
                    ? builtInNames.filter((n) => selected.includes(n))
                    : builtInNames,
                ).map((name) => {
                  const item = strategies[name];
                  const isChecked = selected.includes(name);
                  return (
                    <label
                      key={name}
                      className={`strategy-item-row ${isChecked ? 'is-selected' : ''}`}
                    >
                      <input type="checkbox" checked={isChecked} onChange={() => toggle(name)} />
                      <div className="strategy-item-meta">
                        <div className="strategy-item-title-line">
                          <span className="strategy-item-name">{name}</span>
                          {item?.chart && (
                            <span className="strategy-chart-indicator">
                              {item.chart.levels.length > 0 && `${item.chart.levels.length} mức`}
                              {item.chart.oscillator && 'RSI'}
                              {!item.chart.levels.length && !item.chart.oscillator && (
                                <em title={item.chart.note ?? undefined}>Chưa có vùng</em>
                              )}
                            </span>
                          )}
                          {item && (
                            <span className={`status-pill-sub status-${item.status}`}>
                              {item.status}
                            </span>
                          )}
                        </div>
                        {item?.chart?.note &&
                          !item.chart.levels.length &&
                          !item.chart.oscillator && (
                            <div className="strategy-chart-note">{item.chart.note}</div>
                          )}
                        {item?.reason && (
                          <div className="strategy-item-reason" title={item.reason}>
                            {item.side ? `[${item.side.toUpperCase()}] ` : ''}
                            {item.reason}
                          </div>
                        )}
                      </div>
                    </label>
                  );
                })}
              </div>
            )}

            {/* Generated strategies */}
            {(tab === 'all' || tab === 'generated' || tab === 'active') && (
              <div className="strategy-group">
                <div className="group-heading">
                  Chiến thuật bot sinh ra ({filterList(genNames).length})
                </div>
                {filterList(
                  tab === 'active' ? genNames.filter((n) => selected.includes(n)) : genNames,
                ).map((name) => {
                  const item = strategies[name];
                  const isChecked = selected.includes(name);
                  return (
                    <label
                      key={name}
                      className={`strategy-item-row ${isChecked ? 'is-selected' : ''}`}
                    >
                      <input type="checkbox" checked={isChecked} onChange={() => toggle(name)} />
                      <div className="strategy-item-meta">
                        <div className="strategy-item-title-line">
                          <span className="strategy-item-name code-font">{name}</span>
                          {item?.chart && (
                            <span className="strategy-chart-indicator">
                              {item.chart.levels.length > 0 && `${item.chart.levels.length} mức`}
                              {item.chart.oscillator && 'RSI'}
                              {!item.chart.levels.length && !item.chart.oscillator && (
                                <em title={item.chart.note ?? undefined}>Chưa có vùng</em>
                              )}
                            </span>
                          )}
                          {item && (
                            <span className={`status-pill-sub status-${item.status}`}>
                              {item.status}
                            </span>
                          )}
                        </div>
                        {item?.chart?.note &&
                          !item.chart.levels.length &&
                          !item.chart.oscillator && (
                            <div className="strategy-chart-note">{item.chart.note}</div>
                          )}
                        {item?.reason && (
                          <div className="strategy-item-reason" title={item.reason}>
                            {item.side ? `[${item.side.toUpperCase()}] ` : ''}
                            {item.reason}
                          </div>
                        )}
                      </div>
                    </label>
                  );
                })}
              </div>
            )}
          </div>

          <div className="strategy-popover-footer">
            <span>
              {activeCount === 0
                ? 'Không vẽ chiến thuật nào lên chart (mặc định)'
                : `Đang vẽ ${activeCount} lớp phân tích trên chart`}
            </span>
          </div>
        </div>
      )}
    </div>
  );
}
