import { useState, useRef, useEffect, useMemo } from 'react';
import { Layers, Search, X, Sparkles } from 'lucide-react';
import { compactTreeTitle, treeLabel } from '../lib/forex-tree-label';
import type { ExnessExecution, LearnerAnalysis } from '../types/exness-execution';

export const CONTEXT_STRATEGY_KEY = '__context__';

type Tree2 = NonNullable<
  NonNullable<ExnessExecution['learning']>['htf_ast3_shadow']
>['trees'][number];

interface StrategyOverlayMenuProps {
  analysis: LearnerAnalysis | null;
  selected: string[];
  onChange: (selected: string[]) => void;
  holding?: string[];
  trees?: Tree2[];
}

type TabType = 'all' | 'holding' | 'confirmed' | 'live' | 'active';

export function StrategyOverlayMenu({
  analysis,
  selected,
  onChange,
  holding = [],
  trees = [],
}: StrategyOverlayMenuProps) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [tab, setTab] = useState<TabType>('all');
  const [tip, setTip] = useState<{ name: string; top: number; left: number } | null>(null);
  const showTip = (name: string, el: HTMLElement) => {
    const r = el.getBoundingClientRect();
    setTip({
      name,
      top: r.bottom + 6,
      left: Math.max(8, Math.min(r.left, window.innerWidth - 300)),
    });
  };
  const wrapperRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (wrapperRef.current && !wrapperRef.current.contains(event.target as Node)) setOpen(false);
    }
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') setOpen(false);
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
  const allNames = Object.keys(strategies);

  // Thứ tự ưu tiên: 0 = Đang giữ vị thế broker, 1 = Đã kích hoạt trigger M15, 2 = Đang có setup H1 còn hạn, 3 = Còn lại
  const rank = (name: string) => {
    if (holding.includes(name)) return 0;
    const item = strategies[name];
    if (item?.status === 'confirmed') return 1;
    if (item?.zone || item?.captured_zone) return 2;
    return 3;
  };

  const holdingCount = allNames.filter((n) => holding.includes(n)).length;
  const confirmedCount = allNames.filter((n) => strategies[n]?.status === 'confirmed').length;
  const liveCount = allNames.filter(
    (n) => strategies[n]?.zone || strategies[n]?.captured_zone,
  ).length;

  const toggle = (name: string) =>
    onChange(
      selected.includes(name) ? selected.filter((item) => item !== name) : [...selected, name],
    );

  const bestTree = useMemo(() => {
    const withSamples = trees.filter((t) => (t.resolved ?? 0) >= 1 && t.mean_r != null);
    if (!withSamples.length) return null;
    return [...withSamples].sort((a, b) => (b.mean_r ?? 0) - (a.mean_r ?? 0))[0];
  }, [trees]);

  const selectBest = () => {
    if (!bestTree) return;
    if (!selected.includes(bestTree.id)) {
      onChange([...selected, bestTree.id]);
    }
  };

  const q = query.trim().toLowerCase();
  const visible = (name: string) => {
    const item = strategies[name];
    if (tab === 'holding' && !holding.includes(name)) return false;
    if (tab === 'confirmed' && item?.status !== 'confirmed') return false;
    if (tab === 'live' && !item?.zone && !item?.captured_zone) return false;
    if (tab === 'active' && !selected.includes(name)) return false;
    if (!q) return true;
    return `${name} ${treeLabel(item, name).full} ${item?.description ?? ''}`
      .toLowerCase()
      .includes(q);
  };

  const sortedNames = useMemo(
    () => [...allNames].sort((a, b) => rank(a) - rank(b) || a.localeCompare(b)),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [strategies, holding],
  );

  const pinnedNames = sortedNames.filter((name) => rank(name) <= 1);
  const remainingNames = sortedNames.filter((name) => rank(name) > 1);

  const activeCount = selected.length;
  const hasLiveAlert = holdingCount > 0 || confirmedCount > 0;

  return (
    <div className="strategy-menu-wrapper" ref={wrapperRef}>
      <button
        type="button"
        className={`strategy-menu-trigger ${activeCount > 0 ? 'is-active' : ''}`}
        onClick={() => setOpen((prev) => !prev)}
        aria-expanded={open}
        aria-haspopup="dialog"
        title="Chọn cây V3 để vẽ vùng setup H1, mức vô hiệu và SL lên chart (cây đang giữ lệnh tự vẽ)"
      >
        <Layers size={13} className="menu-icon" />
        <span>Chiến thuật</span>
        {activeCount > 0 ? (
          <span className="strategy-badge">{activeCount}</span>
        ) : (
          <span className="strategy-badge-off">Tắt</span>
        )}
        {hasLiveAlert && (
          <span
            className="menu-live-dot"
            title={`${holdingCount} cây giữ lệnh · ${confirmedCount} cây có tín hiệu`}
          />
        )}
      </button>

      {open && (
        <div
          className="strategy-popover-dropdown"
          role="dialog"
          aria-label="Lựa chọn cây V3 hiển thị trên chart"
        >
          <div className="strategy-popover-header">
            <div className="popover-title-row">
              <span className="popover-title">Lớp chiến thuật trên chart</span>
              <div className="popover-actions">
                {bestTree && (
                  <button
                    type="button"
                    className="popover-btn-best"
                    onClick={selectBest}
                    title={`Chọn cây có R cao nhất: ${treeLabel(strategies[bestTree.id], bestTree.id).title} (+${bestTree.mean_r?.toFixed(2)}R)`}
                  >
                    <Sparkles size={11} /> Cây R tốt nhất
                  </button>
                )}
                {activeCount > 0 && (
                  <button type="button" className="popover-clear-btn" onClick={() => onChange([])}>
                    Bỏ chọn ({activeCount})
                  </button>
                )}
              </div>
            </div>

            <div className="strategy-search-bar">
              <Search size={13} className="search-icon" />
              <input
                type="text"
                placeholder="Tìm theo setup H1, trigger M15, exit…"
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
              {(
                [
                  ['all', `Tất cả (${allNames.length})`],
                  ...(holdingCount > 0
                    ? ([['holding', `Giữ lệnh (${holdingCount})`]] as const)
                    : []),
                  ...(confirmedCount > 0
                    ? ([['confirmed', `Có tín hiệu (${confirmedCount})`]] as const)
                    : []),
                  ['live', `Có setup (${liveCount})`],
                  ['active', `Đang vẽ (${activeCount})`],
                ] as const
              ).map(([key, label]) => (
                <button
                  type="button"
                  key={key}
                  className={tab === key ? 'active' : ''}
                  onClick={() => setTab(key as TabType)}
                >
                  {label}
                </button>
              ))}
            </div>
          </div>

          <div className="strategy-popover-body">
            {tab !== 'live' &&
              tab !== 'holding' &&
              tab !== 'confirmed' &&
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
                      <div className="strategy-item-name">EMA20 M15 · Cản H1/H4</div>
                      <div className="strategy-item-sub">Cản tham chiếu cho TP cấu trúc</div>
                    </div>
                  </label>
                </div>
              )}

            {/* Mục ghim lên đầu: Cây đang giữ lệnh hoặc có tín hiệu kích hoạt */}
            {pinnedNames.filter(visible).length > 0 && (
              <div className="strategy-group strategy-group-pinned">
                <div className="group-heading pinned-heading">
                  Ưu tiên: Đang giữ lệnh & Có tín hiệu ({pinnedNames.filter(visible).length})
                </div>
                {pinnedNames.filter(visible).map((name) => renderRow(name))}
              </div>
            )}

            {/* Các cây còn lại */}
            {remainingNames.filter(visible).length > 0 && (
              <div className="strategy-group">
                <div className="group-heading">
                  Các cây khác ({remainingNames.filter(visible).length})
                </div>
                {remainingNames.filter(visible).map((name) => renderRow(name))}
              </div>
            )}
          </div>

          {tip && strategies[tip.name] !== undefined && (
            <div className="strategy-tooltip" style={{ top: tip.top, left: tip.left }}>
              <div className="evo-tooltip-title">
                {treeLabel(strategies[tip.name], tip.name).full}
              </div>
              {strategies[tip.name]?.description && (
                <div className="evo-tooltip-body">{strategies[tip.name]?.description}</div>
              )}
              <div className="evo-tooltip-code">{tip.name}</div>
            </div>
          )}
          <div className="strategy-popover-footer">
            <span>
              {activeCount === 0
                ? holdingCount > 0
                  ? `Tự vẽ ${holdingCount} cây đang giữ lệnh · click để xem thêm cây khác`
                  : 'Không vẽ lớp nào lên chart (mặc định)'
                : `Đang vẽ ${activeCount} cây: vùng setup H1, mức vô hiệu, SL`}
            </span>
          </div>
        </div>
      )}
    </div>
  );

  function renderRow(name: string) {
    const item = strategies[name];
    const label = treeLabel(item, name);
    const isHolding = holding.includes(name);
    const isConfirmed = item?.status === 'confirmed';
    const zone = item?.captured_zone ?? item?.zone;
    return (
      <label
        key={name}
        className={`strategy-item-row ${selected.includes(name) || isHolding ? 'is-selected' : ''}`}
        onMouseEnter={(e) => showTip(name, e.currentTarget)}
        onMouseLeave={() => setTip(null)}
      >
        <input
          type="checkbox"
          checked={selected.includes(name) || isHolding}
          disabled={isHolding}
          onChange={() => toggle(name)}
        />
        <div className="strategy-item-meta">
          <div className="strategy-item-title-line">
            <span className="strategy-item-name">{compactTreeTitle(label.title)}</span>
            {isHolding && <span className="status-pill-sub status-holding">ĐANG GIỮ LỆNH</span>}
            {isConfirmed && <span className="status-pill-sub status-confirmed">TÍN HIỆU M15</span>}
            {zone && !isHolding && !isConfirmed && (
              <span className="strategy-chart-indicator">Vùng H1 {zone.side.toUpperCase()}</span>
            )}
          </div>
          <div className="strategy-item-reason">
            {label.policy} · G{label.generation} · #{label.short}
            {label.params && <span className="strategy-label-params">{label.params}</span>}
          </div>
        </div>
      </label>
    );
  }
}
