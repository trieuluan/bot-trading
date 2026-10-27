import { useState, useRef, useEffect, useMemo } from 'react';
import { Layers, Search, X, Sparkles } from 'lucide-react';
import { compactTreeTitle, treeLabel } from '../lib/forex-tree-label';
import type { ExnessExecution, LearnerAnalysis } from '../types/exness-execution';

export const CONTEXT_STRATEGY_KEY = '__context__';
export const TECHNICAL_OVERLAYS = [
  {
    id: CONTEXT_STRATEGY_KEY,
    title: 'Tham khảo chung · EMA20 khung chart & Cản H1/H4',
    sub: 'Không thuộc cây; chỉ báo riêng tự hiện khi chọn chiến thuật bên dưới',
  },
] as const;

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
    <div className="strategy-menu-wrapper relative inline-flex min-w-0" ref={wrapperRef}>
      <button
        type="button"
        className={`strategy-menu-trigger ${activeCount > 0 ? 'is-active' : ''} inline-flex items-center gap-2 rounded border border-border-base px-3 py-1.5 text-xs text-text-secondary hover:bg-surface-2`}
        onClick={() => setOpen((prev) => !prev)}
        aria-expanded={open}
        aria-haspopup="dialog"
        title="Chọn cây V3 để vẽ vùng setup H1, mức vô hiệu và SL lên chart (cây đang giữ lệnh tự vẽ)"
      >
        <Layers size={13} className="menu-icon" />
        <span>Chiến thuật</span>
        {activeCount > 0 ? (
          <span className="strategy-badge inline-flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 text-xs font-medium bg-surface-3 text-text-secondary">{activeCount}</span>
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
          className="strategy-popover-dropdown absolute left-0 top-full z-40 mt-2 flex max-h-[70dvh] w-[min(440px,calc(100vw-32px))] flex-col overflow-hidden rounded-lg border border-border-active bg-surface-2 shadow-lg"
          role="dialog"
          aria-label="Lựa chọn cây V3 hiển thị trên chart"
        >
          <div className="strategy-popover-header shrink-0 space-y-2 border-b border-border-base p-3">
            <div className="popover-title-row flex items-center justify-between gap-2 text-xs">
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
                  <button type="button" className="popover-clear-btn text-xs text-text-muted hover:text-text-primary" onClick={() => onChange([])}>
                    Bỏ chọn ({activeCount})
                  </button>
                )}
              </div>
            </div>

            <div className="strategy-search-bar flex items-center gap-2 [&>input]:min-w-0 [&>input]:flex-1">
              <Search size={13} className="search-icon" />
              <input
                type="text"
                placeholder="Tìm theo setup H1, trigger M15, exit…"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                autoFocus
              />
              {query && (
                <button type="button" className="search-clear-btn text-xs text-text-muted hover:text-text-primary" onClick={() => setQuery('')}>
                  <X size={12} />
                </button>
              )}
            </div>

            <div className="strategy-filter-tabs flex flex-wrap items-center gap-1 [&>button]:rounded [&>button]:px-2 [&>button]:py-1 [&>button]:text-xs [&>button]:text-text-secondary [&>button:hover]:bg-surface-3 [&>button[aria-pressed=true]]:bg-surface-3 [&>button[aria-pressed=true]]:text-bull border-b border-border-subtle py-1">
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
                  className={tab === key ? "active bg-bull/10 text-bull" : ''}
                  onClick={() => setTab(key as TabType)}
                >
                  {label}
                </button>
              ))}
            </div>
          </div>

          <div className="strategy-popover-body min-h-0 overflow-y-auto overscroll-y-contain">
            {tab !== 'live' && tab !== 'holding' && tab !== 'confirmed' && (
              <div className="strategy-group">
                <div className="group-heading border-b border-border-base px-3 py-2 text-xs font-semibold text-text-muted">Tham khảo thêm (tuỳ chọn)</div>
                {TECHNICAL_OVERLAYS.filter(
                  (item) => !q || `${item.title} ${item.sub}`.toLowerCase().includes(q),
                ).map((item) => (
                  <label key={item.id} className="strategy-item-row flex min-w-0 items-start gap-2 border-b border-border-subtle p-3 text-xs hover:bg-surface-3 [&.is-selected]:bg-bull/5 [&_input]:mt-1">
                    <input
                      type="checkbox"
                      checked={selected.includes(item.id)}
                      onChange={() => toggle(item.id)}
                    />
                    <div className="strategy-item-meta min-w-0 flex-1 space-y-1">
                      <div className="strategy-item-name text-xs font-semibold text-text-primary">{item.title}</div>
                      <div className="strategy-item-sub text-xs leading-relaxed text-text-muted">{item.sub}</div>
                    </div>
                  </label>
                ))}
              </div>
            )}

            {/* Mục ghim lên đầu: Cây đang giữ lệnh hoặc có tín hiệu kích hoạt */}
            {pinnedNames.filter(visible).length > 0 && (
              <div className="strategy-group strategy-group-pinned">
                <div className="group-heading pinned-heading border-b border-border-base px-3 py-2 text-xs font-semibold text-text-muted text-bull">
                  Ưu tiên: Đang giữ lệnh & Có tín hiệu ({pinnedNames.filter(visible).length})
                </div>
                {pinnedNames.filter(visible).map((name) => renderRow(name))}
              </div>
            )}

            {/* Các cây còn lại */}
            {remainingNames.filter(visible).length > 0 && (
              <div className="strategy-group">
                <div className="group-heading border-b border-border-base px-3 py-2 text-xs font-semibold text-text-muted">
                  Các cây khác ({remainingNames.filter(visible).length})
                </div>
                {remainingNames.filter(visible).map((name) => renderRow(name))}
              </div>
            )}
          </div>

          {tip && strategies[tip.name] !== undefined && (
            <div className="strategy-tooltip fixed z-[80] max-h-[70dvh] max-w-[min(360px,calc(100vw-24px))] overflow-auto rounded-lg border border-border-active bg-surface-2 p-3 text-xs text-text-secondary shadow-lg" style={{ top: tip.top, left: tip.left }}>
              <div className="evo-tooltip-title mb-2 font-semibold text-text-primary">
                {treeLabel(strategies[tip.name], tip.name).full}
              </div>
              {strategies[tip.name]?.description && (
                <div className="evo-tooltip-body text-xs leading-relaxed text-text-secondary">{strategies[tip.name]?.description}</div>
              )}
              <div className="evo-tooltip-code mt-2 break-all font-mono text-xs text-text-muted">{tip.name}</div>
            </div>
          )}
          <div className="strategy-popover-footer shrink-0 border-t border-border-base p-3 text-xs text-text-muted">
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
        className={`strategy-item-row ${selected.includes(name) || isHolding ? 'is-selected' : ''} flex min-w-0 items-start gap-2 border-b border-border-subtle p-3 text-xs hover:bg-surface-3 [&.is-selected]:bg-bull/5 [&_input]:mt-1`}
        onMouseEnter={(e) => showTip(name, e.currentTarget)}
        onMouseLeave={() => setTip(null)}
      >
        <input
          type="checkbox"
          checked={selected.includes(name) || isHolding}
          disabled={isHolding}
          onChange={() => toggle(name)}
        />
        <div className="strategy-item-meta min-w-0 flex-1 space-y-1">
          <div className="strategy-item-title-line flex flex-wrap items-center gap-2">
            <span className="strategy-item-name text-xs font-semibold text-text-primary">{compactTreeTitle(label.title)}</span>
            {isHolding && <span className="status-pill-sub status-holding inline-flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 text-xs font-medium bg-surface-3 text-text-secondary inline-flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 text-xs font-medium bg-surface-3 text-text-secondary">ĐANG GIỮ LỆNH</span>}
            {isConfirmed && <span className="status-pill-sub status-confirmed inline-flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 text-xs font-medium bg-surface-3 text-text-secondary inline-flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 text-xs font-medium bg-surface-3 text-text-secondary">TÍN HIỆU M15</span>}
            {zone && !isHolding && !isConfirmed && (
              <span className="strategy-chart-indicator inline-flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 text-xs font-medium bg-surface-3">Vùng H1 {zone.side.toUpperCase()}</span>
            )}
          </div>
          <div className="strategy-item-reason text-xs leading-relaxed text-text-muted">
            {label.policy} · G{label.generation} · #{label.short}
            {label.params && <span className="strategy-label-params text-xs leading-relaxed text-text-muted">{label.params}</span>}
          </div>
        </div>
      </label>
    );
  }
}
