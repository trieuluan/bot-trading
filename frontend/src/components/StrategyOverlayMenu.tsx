import { useEffect, useId, useRef, useState } from 'react';
import { Layers, Search, X } from 'lucide-react';
import { compactTreeTitle, treeLabel } from '../lib/forex-tree-label';
import type { LearnerAnalysis } from '../types/exness-execution';
import type { ForexEntryPilot } from '../types/forex-entry';

export const CONTEXT_STRATEGY_KEY = '__context__';
export const TECHNICAL_OVERLAYS = [{
  id: CONTEXT_STRATEGY_KEY,
  title: 'Tham khảo chung · EMA20 khung chart & Cản H1/H4',
  sub: 'Chỉ báo bổ sung, không phải setup hoặc lệnh broker',
}] as const;

type EntryTree = NonNullable<ForexEntryPilot['pilot']['trees']>[number];
type Strategies = LearnerAnalysis['strategies'];
type Tab = 'all' | 'holding' | 'live' | 'active';
const policies = [
  ['market', 'Market'], ['limit_setup_mid', 'Limit'],
  ['stop_trigger_break', 'Stop'], ['limit_or_stop', 'Hybrid'],
] as const;

export function overlayBundles(strategies: Strategies, trees: EntryTree[]) {
  const metadata = new Map(trees.map((tree) => [tree.id, tree]));
  const groups = new Map<string, { id: string; ids: string[]; generation: number; title: string; exit: string; params: string }>();
  for (const id of Object.keys(strategies)) {
    const tree = metadata.get(id);
    const key = tree?.bundle_id || id;
    const label = treeLabel(strategies[id], id);
    if (!groups.has(key)) groups.set(key, {
      id: key, ids: [], generation: tree?.generation ?? label.generation,
      title: compactTreeTitle(label.title), exit: label.policy, params: label.params,
    });
    groups.get(key)!.ids.push(id);
  }
  return [...groups.values()];
}

export function toggleOverlayGroup(selected: string[], ids: string[], automatic: string[]) {
  const editable = ids.filter((id) => !automatic.includes(id));
  return editable.every((id) => selected.includes(id))
    ? selected.filter((id) => !editable.includes(id))
    : [...new Set([...selected, ...editable])];
}

interface StrategyOverlayMenuProps {
  analysis: LearnerAnalysis | null;
  selected: string[];
  onChange: (selected: string[]) => void;
  holding?: string[];
  setupStrategy?: string;
  trees?: EntryTree[];
}

export function StrategyOverlayMenu({ analysis, selected, onChange, holding = [], setupStrategy, trees = [] }: StrategyOverlayMenuProps) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [tab, setTab] = useState<Tab>('all');
  const wrapper = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const panelId = useId();
  useEffect(() => {
    if (!open) return;
    const outside = (event: MouseEvent) => {
      if (!wrapper.current?.contains(event.target as Node)) setOpen(false);
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.stopPropagation();
        setOpen(false);
        trigger.current?.focus();
      }
    };
    document.addEventListener('mousedown', outside);
    document.addEventListener('keydown', escape);
    return () => {
      document.removeEventListener('mousedown', outside);
      document.removeEventListener('keydown', escape);
    };
  }, [open]);

  const strategies = analysis?.strategies ?? {};
  const metadata = new Map(trees.map((tree) => [tree.id, tree]));
  const automatic = [...new Set([...holding, ...(setupStrategy ? [setupStrategy] : [])])];
  const drawn = new Set([...selected, ...automatic]);
  const bundles = overlayBundles(strategies, trees);
  const hasSetup = (id: string) => Boolean(strategies[id]?.zone || strategies[id]?.captured_zone || strategies[id]?.status === 'confirmed');
  const matchesTab = (ids: string[], value: Tab) => value === 'all' || ids.some((id) =>
    value === 'holding' ? holding.includes(id) : value === 'live' ? hasSetup(id) : drawn.has(id));
  const search = query.trim().toLocaleLowerCase('vi');
  const visible = bundles.filter((bundle) => matchesTab(bundle.ids, tab) &&
    `${bundle.title} ${bundle.exit} ${bundle.params} ${bundle.id} ${bundle.ids.map((id) => `${id} ${metadata.get(id)?.entry_policy ?? ''}`).join(' ')}`.toLocaleLowerCase('vi').includes(search));
  const rank = (ids: string[]) => ids.some((id) => holding.includes(id)) ? 0 : ids.some((id) => drawn.has(id)) ? 1 : ids.some(hasSetup) ? 2 : 3;
  visible.sort((left, right) => rank(left.ids) - rank(right.ids));
  const count = Object.keys(strategies).filter((id) => drawn.has(id)).length;
  const toggle = (ids: string[]) => onChange(toggleOverlayGroup(selected, ids, automatic));

  return (
    <div className="relative inline-flex min-w-0" ref={wrapper}>
      <button ref={trigger} type="button" aria-expanded={open} aria-controls={panelId}
        onClick={() => setOpen(!open)}
        className="inline-flex min-h-8 items-center gap-2 rounded-lg border border-border-base px-3 py-1.5 text-xs text-text-secondary hover:bg-surface-2 focus-visible:outline-2 focus-visible:outline-sky-400">
        <Layers size={14} /><span>Lớp tham khảo</span>
        <span className="rounded bg-surface-3 px-1.5 py-0.5 tabular-nums">{count ? `${count} cây` : 'Tắt'}</span>
      </button>
      {open && (
        <section id={panelId} aria-label="Lớp tham khảo trên chart" className="fixed inset-x-3 z-40 mt-10 flex max-h-[min(620px,75dvh)] flex-col overflow-hidden rounded-xl border border-border-active bg-surface-2 shadow-xl sm:absolute sm:inset-x-auto sm:left-0 sm:top-full sm:mt-2 sm:w-[min(460px,calc(100vw-32px))]">
          <header className="shrink-0 space-y-3 border-b border-border-base p-3">
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0"><h3 className="text-sm font-semibold text-text-primary">Lớp tham khảo trên chart</h3>
                <p className="mt-1 text-xs leading-relaxed text-text-muted">{bundles.length} bộ · {Object.keys(strategies).length} cây. Không phải danh sách setup đã ghi nhận; chọn ở đây chỉ bật lớp vẽ, không đặt lệnh.</p>
              </div>
              <button type="button" aria-label="Đóng lớp tham khảo" onClick={() => { setOpen(false); trigger.current?.focus(); }} className="shrink-0 rounded-lg p-2 text-text-secondary hover:bg-surface-3"><X size={16} /></button>
            </div>
            <label className="flex min-w-0 items-center gap-2 rounded-lg border border-border-base bg-canvas px-3 py-2 text-text-muted">
              <Search size={14} className="shrink-0" /><span className="sr-only">Tìm bộ chiến thuật</span>
              <input autoFocus value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Tìm setup H1, trigger M15, cách thoát…" className="min-w-0 flex-1 bg-transparent text-xs text-text-primary outline-none" />
            </label>
            <div role="group" aria-label="Lọc bộ chiến thuật" className="flex flex-wrap gap-1">
              {([['all', 'Tất cả'], ['holding', 'Giữ lệnh'], ['live', 'Có vùng/tín hiệu'], ['active', 'Đang vẽ']] as const).map(([value, text]) => (
                <button key={value} type="button" aria-pressed={tab === value} onClick={() => setTab(value)} className="rounded-md px-2 py-1.5 text-xs text-text-secondary hover:bg-surface-3 aria-pressed:bg-sky-400/10 aria-pressed:text-sky-400">{text} ({bundles.filter((bundle) => matchesTab(bundle.ids, value)).length})</button>
              ))}
            </div>
          </header>
          <div className="min-h-0 space-y-2 overflow-y-auto overscroll-contain p-3">
            <label className="flex items-start gap-2 rounded-lg border border-border-subtle p-3 text-xs text-text-secondary">
              <input type="checkbox" className="mt-0.5 accent-sky-400" checked={selected.includes(CONTEXT_STRATEGY_KEY)} onChange={() => toggle([CONTEXT_STRATEGY_KEY])} />
              <span className="min-w-0"><span className="block font-medium">EMA20 & Cản H1/H4</span><span className="mt-1 block leading-relaxed text-text-muted">Tham khảo chung · độc lập với các bộ bên dưới</span></span>
            </label>
            {visible.map((bundle) => {
              const label = treeLabel(strategies[bundle.ids[0]], bundle.ids[0]);
              const grouped = bundle.ids.some((id) => metadata.get(id)?.bundle_id);
              const editable = bundle.ids.filter((id) => !automatic.includes(id));
              const allSelected = editable.length > 0 && editable.every((id) => selected.includes(id));
              return (
                <article key={bundle.id} className="min-w-0 space-y-2 rounded-lg border border-border-base bg-canvas/40 p-3">
                  <div className="flex items-start justify-between gap-2">
                    <div className="min-w-0"><h4 className="break-words text-xs font-semibold leading-relaxed text-text-primary">{bundle.title}</h4>
                      <p className="mt-1 flex flex-wrap gap-x-2 gap-y-1 text-xs text-text-muted"><span>{bundle.exit}</span><span>G{bundle.generation}</span>{bundle.params && <span>{bundle.params}</span>}</p>
                    </div>
                    {grouped && <button type="button" disabled={!editable.length} onClick={() => toggle(bundle.ids)} className="shrink-0 rounded px-2 py-1 text-xs text-sky-400 hover:bg-sky-400/10 disabled:opacity-40">{allSelected ? 'Bỏ bộ' : 'Cả bộ'}</button>}
                  </div>
                  {grouped ? <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
                    {policies.map(([policy, text]) => {
                      const id = bundle.ids.find((name) => metadata.get(name)?.entry_policy === policy);
                      const locked = Boolean(id && automatic.includes(id));
                      const status = id && holding.includes(id) ? 'Theo vị thế' : id === setupStrategy ? 'Theo setup' : id && strategies[id]?.status === 'confirmed' ? 'Tín hiệu M15' : id && hasSetup(id) ? 'Có vùng H1' : id ? 'Tham khảo' : 'Chưa có dữ liệu';
                      return <button key={policy} type="button" disabled={!id || locked} aria-pressed={Boolean(id && drawn.has(id))} aria-label={`${text} · ${bundle.title} · ${status}`} onClick={() => id && toggle([id])} className="flex min-w-0 flex-col items-center gap-1 rounded-lg border border-border-base px-1 py-2 text-xs text-text-secondary hover:bg-surface-3 focus-visible:outline-2 focus-visible:outline-sky-400 aria-pressed:border-sky-400/60 aria-pressed:bg-sky-400/10 aria-pressed:text-sky-400 disabled:cursor-default disabled:opacity-70"><span className="font-semibold">{text}</span><span className="text-center text-[10px] leading-snug">{status}</span></button>;
                    })}
                  </div> : <button type="button" disabled={automatic.includes(bundle.ids[0])} aria-pressed={drawn.has(bundle.ids[0])} onClick={() => toggle(bundle.ids)} className="rounded border border-border-base px-3 py-1.5 text-xs text-text-secondary aria-pressed:border-sky-400 aria-pressed:text-sky-400">{automatic.includes(bundle.ids[0]) ? 'Tự vẽ theo vị thế/setup' : drawn.has(bundle.ids[0]) ? 'Bỏ lớp tham khảo' : 'Vẽ tham khảo'} · #{label.short}</button>}
                </article>
              );
            })}
            {!visible.length && <p className="p-4 text-center text-xs leading-relaxed text-text-muted">{bundles.length ? 'Không có bộ khớp bộ lọc.' : 'Chưa có dữ liệu lớp chiến thuật.'}</p>}
          </div>
          <footer className="flex shrink-0 items-center justify-between gap-3 border-t border-border-base p-3 text-xs text-text-muted">
            <span className="min-w-0 leading-relaxed">Lớp theo vị thế/setup tự vẽ. Bỏ setup bằng × trên chart; thao tác này không đóng lệnh.</span>
            <button type="button" disabled={!selected.length} onClick={() => onChange([])} className="shrink-0 rounded px-2 py-1.5 text-text-secondary hover:bg-surface-3 disabled:opacity-40">Xóa tham khảo</button>
          </footer>
        </section>
      )}
    </div>
  );
}
