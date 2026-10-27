import { futuresHeaders } from '../lib/futures-symbol';
import { fetchFuturesHistory } from '../lib/api';
import type { FuturesHistoryJob } from '../types/paper';
import { formatTime } from '../lib/time-format';
import { useEffect, useRef, useState } from 'react';
import { Activity, Archive, Brain, ChevronDown, ChevronLeft, ChevronRight, ChevronsLeft, ChevronsRight, Dna, Power, X } from 'lucide-react';
import { FuturesDraftsPanel, type Draft, type DraftWorker } from './FuturesDraftsPanel';
import {
  exitLabel,
  HTF_LABELS,
  profileOf,
  targetCapText,
  howToPlay,
  stepKind,
  strategyInfo,
  strategyName,
  strategyShortTitle,
  searchText,
  type Rule,
} from '../lib/futures-strategy-info';

type Row = {
  strategy: string;
  genome: {
    family: string;
    lookback: number;
    target_r: number;
    stop_buffer: number;
    exit_policy?:
      | 'fixed'
      | 'breakeven'
      | 'trail'
      | 'structure'
      | 'adaptive'
      | 'atr_chandelier'
      | 'time_stop'
      | 'scale_out_ladder'
      | 'signal_invalidation'
      | 'runner'
      | 'volatility_stop'
      | 'funding_exit';
    max_er?: number;
    funding_threshold?: number;
    rule?: Rule;
    exit_profile?: string;
  };
  parents: string[];
  born_at: number;
  learned_samples: number;
  learned_mean_r: number | null;
  origin: string;
  generation: number;
  promoted: boolean;
  paused?: boolean;
  shadow_only?: boolean;
  rule_text?: string | null;
  complexity?: number;
  signals: number;
  open_trades?: { id: number; side: 'long' | 'short'; entry: number; quantity: number }[];
  paper?: { closed: number; wins: number; net_pnl: number; last_trade_id: number | null };
  lineage?: {
    kind: 'mutation' | 'crossover';
    from_mate: { gene: string; value: Gene }[];
    mutated: { gene: string; before: Gene; after: Gene }[];
  } | null;
  replay_samples?: number;
  forward_samples?: number;
  forward_open?: number;
  resolved: number;
  mean_r: number | null;
  profit_factor: number | null;
  qualification: { eligible: boolean; windows: number; trades: number };
};
type Gene = string | number | Record<string, number> | null;
type Retired = {
  strategy: string;
  genome: Row['genome'] | null;
  closed: number;
  wins: number;
  net_pnl: number;
  open: number;
  last_trade_id: number | null;
};
type Learning = {
  enabled: boolean;
  auto_mode: 'learner' | 'paper_learning';
  stale: boolean;
  error: string | null;
  population: number;
  samples: number;
  promoted: number;
  last_decision: {
    regime: string;
    mode: string;
    selected: string | null;
    candidates: number;
    time?: number;
    strategies?: { strategy: string; status: string; side?: 'long' | 'short' }[];
  } | null;
  strategies: Row[];
  drafts?: Draft[];
  draft_worker?: DraftWorker;
  retired_paper?: Retired[];
  retired_strategies?: {
    strategy: string;
    genome: Row['genome'];
    generation: number;
    retirement: {
      at: number;
      reason: string;
      samples: number;
      mean_r: number;
      max_drawdown_r: number;
      retained_strategy: string | null;
      overlap: { shared: number; ratio: number } | null;
    } | null;
  }[];
};

async function call(path: string, body?: object): Promise<Learning> {
  const response = await fetch(`/api/workspaces/mexc_futures/learning${path}`, {
    method: body ? 'POST' : 'GET',
    headers: { ...futuresHeaders(), ...(body ? { 'Content-Type': 'application/json' } : {}) },
    body: body ? JSON.stringify(body) : undefined,
    signal: AbortSignal.timeout(10000),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok)
    throw new Error(typeof data.detail === 'string' ? data.detail : 'Yêu cầu thất bại');
  return data;
}

type StatusKey =
  'all' | 'signal' | 'holding' | 'paper' | 'learned' | 'good' | 'unlearned' | 'promoted';

const fmt = (value: number | null, digits = 2) => (value === null ? '—' : value.toFixed(digits));
const usdt = (value: number) => `${value > 0 ? '+' : ''}${value.toFixed(2)} USDT`;

const GENE_LABELS: Record<string, string> = {
  H4: 'H4 xu hướng',
  H1: 'H1 setup',
  M15: 'M15 trigger',
  rule: 'Luật vào',
  lookback: 'Lookback',
  target_r: 'TP',
  stop_buffer: 'SL buffer',
  max_er: 'ER tối đa',
  funding_threshold: 'Ngưỡng funding',
  exit_policy: 'Thoát',
  exit_params: 'Tham số thoát',
};

function geneText(gene: string, value: Gene) {
  if (value === null || value === undefined) return 'mặc định';
  if (typeof value === 'object')
    return Object.entries(value)
      .map(([key, v]) => `${key} ${v}`)
      .join(', ');
  if (gene === 'exit_policy') return exitLabel(String(value));
  if (gene === 'lookback') return `N${value}`;
  if (gene === 'target_r') return `${value}R`;
  if (gene === 'stop_buffer') return `+${value}`;
  // Slot label already names the frame, so drop the trailing "(H1)".
  return String(value).replace(/ \((H4|H1|M15)\)$/, '');
}

function ParentChip({
  id,
  label,
  title,
  tag,
}: {
  id: string;
  label: string;
  title: string;
  tag?: string;
}) {
  return (
    <span className="evo-chip-wrap inline-flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 text-xs font-medium bg-surface-3 text-text-secondary">
      <code className="evo-parent-chip flex min-w-0 flex-wrap items-center gap-2 text-xs" tabIndex={0} aria-describedby={id}>
        {tag && <b>{tag}</b>} {label}
      </code>
      <span id={id} className="evo-tooltip fixed z-[80] max-h-[70dvh] max-w-[min(360px,calc(100vw-24px))] overflow-auto rounded-lg border border-border-active bg-surface-2 p-3 text-xs text-text-secondary shadow-lg" role="tooltip">
        <span className="evo-tooltip-title mb-2 font-semibold text-text-primary">{label}</span>
        <span className="evo-tooltip-body text-xs leading-relaxed text-text-secondary">{title}</span>
      </span>
    </span>
  );
}

const PAGE = { cards: 12, retired: 10 };
const clampPage = (page: number, total: number, size: number) =>
  Math.min(page, Math.max(1, Math.ceil(total / size)));

function Pager({
  total,
  page,
  size,
  onPage,
  unit,
}: {
  total: number;
  page: number;
  size: number;
  onPage: (page: number) => void;
  unit: string;
}) {
  const pages = Math.max(1, Math.ceil(total / size));
  if (total <= size) return null;
  const from = (page - 1) * size + 1;
  return (
    <nav className="evo-pager flex min-w-0 flex-wrap items-center justify-between gap-3 rounded-lg border border-border-subtle bg-surface-2/40 px-3 py-2.5 text-xs" aria-label={`Phân trang ${unit}`}>
      <span className="text-text-muted" role="status" aria-live="polite">
        <b className="font-medium tabular-nums text-text-primary">{from}–{Math.min(page * size, total)}</b> / {total} {unit}
      </span>
      <div className="flex items-center gap-1.5 [&>button]:inline-flex [&>button]:min-h-9 [&>button]:min-w-9 [&>button]:items-center [&>button]:justify-center [&>button]:gap-1 [&>button]:rounded-md [&>button]:border [&>button]:border-border-base [&>button]:bg-surface-1 [&>button]:px-2 [&>button]:text-text-secondary [&>button]:transition-colors [&>button:enabled:hover]:border-sky-400/40 [&>button:enabled:hover]:bg-sky-400/10 [&>button:enabled:hover]:text-sky-200 [&>button:disabled]:cursor-not-allowed [&>button:disabled]:opacity-35">
        <button type="button" disabled={page <= 1} onClick={() => onPage(1)} aria-label="Trang đầu">
          <ChevronsLeft size={15} aria-hidden="true" />
        </button>
        <button type="button" disabled={page <= 1} onClick={() => onPage(page - 1)}>
          <ChevronLeft size={15} aria-hidden="true" /> Trước
        </button>
        <b className="inline-flex min-h-9 items-center rounded-md border border-sky-400/25 bg-sky-400/10 px-3 font-medium tabular-nums text-sky-200" aria-current="page">
          <span className="sr-only">Trang </span>{page}<span className="mx-1.5 text-text-muted">/</span>{pages}
        </b>
        <button type="button" disabled={page >= pages} onClick={() => onPage(page + 1)}>
          Tiếp <ChevronRight size={15} aria-hidden="true" />
        </button>
        <button
          type="button"
          disabled={page >= pages}
          onClick={() => onPage(pages)}
          aria-label="Trang cuối"
        >
          <ChevronsRight size={15} aria-hidden="true" />
        </button>
      </div>
    </nav>
  );
}

const RETIRE_REASON: Record<string, [string, string]> = {
  negative_forward_r: ['R forward âm', 'bad'],
  forward_drawdown: ['Drawdown ≥10R', 'warn'],
  redundant_forward: ['Trùng cây tốt hơn', 'info'],
  h4_diversified: ['Đa dạng H4', 'info'],
};

export function FuturesLearningPage({ onViewTrade }: { onViewTrade?: (id: number) => void }) {
  const [data, setData] = useState<Learning | null>(null);
  const [replay, setReplay] = useState<FuturesHistoryJob | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [family, setFamily] = useState('all');
  const [query, setQuery] = useState('');
  const [status, setStatus] = useState<StatusKey>('all');
  const [showPaused, setShowPaused] = useState(false);
  const [gen, setGen] = useState('all');
  const [sort, setSort] = useState('score');
  const [tab, setTab] = useState<'forward' | 'drafts'>('forward');
  const [detail, setDetail] = useState<string | null>(null);
  const [page, setPage] = useState(1);
  const [retiredPage, setRetiredPage] = useState(1);
  const [paperPage, setPaperPage] = useState(1);
  useEffect(() => {
    if (!detail) return;
    const onKey = (event: KeyboardEvent) => event.key === 'Escape' && setDetail(null);
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [detail]);

  useEffect(() => {
    setPage(1);
  }, [family, query, status, showPaused, gen, sort]);

  useEffect(() => {
    let active = true;
    const load = () =>
      call('')
        .then((result) => active && (setData(result), setError('')))
        .catch(
          (exc: Error) => active && setError(`Chưa tải được trạng thái bot học: ${exc.message}`),
        );
    void load();
    fetchFuturesHistory()
      .then((job) => active && setReplay(job))
      .catch(() => undefined);
    const timer = setInterval(load, tab === 'drafts' ? 5000 : 15000);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [tab]);

  const act = (path: string, body: object) => {
    if (busy) return;
    setBusy(true);
    return call(path, body)
      .then((result) => (setData(result), setError('')))
      .catch((exc: Error) => setError(exc.message))
      .finally(() => setBusy(false));
  };

  const all = data?.strategies ?? [];
  const families = [...new Set(all.map((row) => profileOf(row.genome)))].sort();
  const generations = [...new Set(all.map((row) => row.generation))].sort((a, b) => a - b);
  const needles = searchText(query).trim().split(/\s+/).filter(Boolean);
  const selected = data?.last_decision?.selected;
  const decision = data?.last_decision;
  const fresh = !!decision?.time && decision.time === Math.floor(Date.now() / 900000) * 900 - 900;
  const signalSides = new Map(
    fresh
      ? (decision?.strategies ?? [])
          .filter((r) => r.status === 'signal')
          .map((r) => [r.strategy, r.side] as const)
      : [],
  );
  const signalSet = new Set(signalSides.keys());
  const titles = new Map(all.map((row) => [row.strategy, strategyName(row.genome)]));
  const replayed = new Set((replay?.report?.strategies ?? []).map((row) => row.strategy));
  const familyName = (name: string) =>
    name === 'default' ? 'Cây luật tự sinh' : (HTF_LABELS[name] ?? name.replace(/_/g, ' '));
  const holding = (row: Row) => (row.open_trades?.length ?? 0) > 0;
  const statuses: [StatusKey, string, (row: Row) => boolean][] = [
    ['all', 'Tất cả', () => true],
    ['signal', '⚡ Tín hiệu', (row) => signalSet.has(row.strategy)],
    ['holding', 'Đang giữ lệnh', holding],
    ['paper', 'Đã chạy Auto Paper', (row) => holding(row) || !!row.paper?.closed],
    ['learned', 'Có mẫu học', (row) => row.learned_samples > 0],
    ['good', 'R học > 0', (row) => row.learned_samples > 0 && (row.learned_mean_r ?? 0) > 0],
    ['unlearned', 'Chưa có mẫu', (row) => row.learned_samples === 0],
    ['promoted', 'Đã duyệt', (row) => row.promoted],
  ];
  const pausedCount = all.filter((row) => row.paused).length;
  // Status chips count within the other filters, so each number matches what clicking shows.
  const scoped = all.filter(
    (row) =>
      (showPaused || !row.paused || holding(row)) &&
      (family === 'all' || profileOf(row.genome) === family) &&
      needles.every((needle) =>
        searchText(
          [
            strategyName(row.genome),
            familyName(profileOf(row.genome)),
            row.strategy,
            row.rule_text ?? '',
            strategyInfo(row.genome).basis,
            `g${row.generation}`,
            ...row.parents,
            ...row.parents.map((parent) => titles.get(parent) ?? ''),
          ].join(' '),
        ).includes(needle),
      ) &&
      (gen === 'all' || row.generation === Number(gen)),
  );
  const rows = scoped.filter(statuses.find(([key]) => key === status)![2]);
  // ponytail: priority pinned (holding > signal > idle); add toggle/config if user wants pure score order.
  const rank = (row: Row) =>
    (row.open_trades?.length ?? 0) > 0 ? 0 : signalSet.has(row.strategy) ? 1 : 2;
  const sorted = [...rows].sort((a, b) => {
    if (rank(a) !== rank(b)) return rank(a) - rank(b);
    if (sort === 'name') return strategyName(a.genome).localeCompare(strategyName(b.genome), 'vi');
    if (sort === 'samples') return b.learned_samples - a.learned_samples;
    if (sort === 'generation') return b.generation - a.generation;
    return (
      (b.learned_mean_r ?? -Infinity) - (a.learned_mean_r ?? -Infinity) ||
      a.strategy.localeCompare(b.strategy)
    );
  });
  const cardPage = clampPage(page, sorted.length, PAGE.cards);
  const treePage = clampPage(retiredPage, data?.retired_strategies?.length ?? 0, PAGE.retired);
  const paperPageNow = clampPage(paperPage, data?.retired_paper?.length ?? 0, PAGE.retired);
  const modes = [
    ['paper_learning', 'Học thử Paper', { enabled: true, auto_mode: 'paper_learning' }],
    ['learner', 'Chỉ đã duyệt', { enabled: true, auto_mode: 'learner' }],
  ] as const;
  const kpis = data
    ? [
        ['Chiến thuật', data.population],
        [
          'Gốc / Biến thể',
          `${data.strategies.filter((r) => r.generation === 0).length} / ${data.strategies.filter((r) => r.generation > 0).length}`,
        ],
        ['Mẫu shadow', data.samples],
        ['Đã duyệt', data.promoted],
        ['Bối cảnh', data.last_decision?.regime ?? '—'],
      ]
    : [];

  return (
    <section className="panel evo min-w-0 rounded-xl border border-border-base bg-surface-1 p-3 space-y-4 sm:p-5">
      <header className="evo-head flex min-w-0 flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 max-w-3xl space-y-1.5">
          <h2 className="flex items-center gap-2 font-display text-base font-semibold text-text-primary">
            <Brain size={17} /> Bot học Futures · Tiến hóa
          </h2>
          <p className="text-xs leading-relaxed text-text-muted">
            Khung HTF: <b>H4</b> lọc xu hướng EMA50 · <b>H1</b> cấu trúc điểm tựa · <b>M15</b>{' '}
            trigger đóng nến. Cây luật tự sinh biến đổi cấu trúc và điều kiện.
          </p>
        </div>
        <span className="pill amber inline-flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 text-xs font-medium bg-surface-3 text-text-secondary text-amber-300">Paper · không gửi lệnh sàn</span>
      </header>
      {error && (
        <p className="negative text-bear" role="alert">
          {error}
        </p>
      )}
      {data && (
        <>
          <div className="evo-kpis grid grid-cols-2 gap-2 sm:grid-cols-3 xl:grid-cols-5">
            {kpis.map(([label, value]) => (
              <div key={label} className="min-w-0 rounded-lg border border-border-subtle bg-surface-2/60 px-3 py-2.5">
                <span className="block text-xs text-text-muted">{label}</span>
                <strong className="mt-1 block break-words font-display text-lg font-semibold tabular-nums text-text-primary">{value}</strong>
              </div>
            ))}
          </div>
          {replay?.report && (
            <p className="evo-replay text-xs leading-relaxed text-text-muted" role="status">
              Replay lịch sử: {replay.days} ngày, dữ liệu đến{' '}
              {formatTime(replay.report.end_time, false, false).split(' ')[0]} · phủ{' '}
              <b>
                {all.filter((row) => replayed.has(row.strategy)).length}/{all.length}
              </b>{' '}
              chiến thuật. Chiến thuật sinh sau lần replay chỉ học từ forward, nên thường hiện “Chưa
              có mẫu”.
            </p>
          )}
          {(data.stale || data.error) && (
            <p className="evo-warn rounded border border-amber-400/30 bg-amber-400/5 p-3 text-xs text-amber-200" role="status">
              {data.stale && 'Dữ liệu học đang chậm hoặc chưa chạy. '}
              {data.error}
            </p>
          )}
          <div className="evo-controls flex min-w-0 flex-wrap items-center justify-between gap-2 text-xs">
            <div className="evo-seg flex flex-wrap items-center gap-1 [&>button]:rounded [&>button]:px-2 [&>button]:py-1 [&>button]:text-xs [&>button]:text-text-secondary [&>button:hover]:bg-surface-3 [&>button[aria-pressed=true]]:bg-surface-3 [&>button[aria-pressed=true]]:text-bull border-b border-border-subtle py-1" role="group" aria-label="Chế độ chọn chiến thuật">
              {modes.map(([key, label, body]) => (
                <button
                  key={key}
                  disabled={busy}
                  aria-pressed={data.auto_mode === key}
                  onClick={() => act('/config', body)}
                >
                  {label}
                </button>
              ))}
            </div>
            <button
              className={`evo-power inline-flex min-h-9 items-center gap-2 rounded-md border px-3 text-xs ${data.enabled ? 'border-bull/30 bg-bull/10 text-bull' : 'border-border-base text-text-muted hover:bg-surface-2'}`}
              disabled={busy}
              aria-pressed={data.enabled}
              onClick={() =>
                act('/config', {
                  enabled: !data.enabled,
                  auto_mode: data.auto_mode || 'paper_learning',
                })
              }
            >
              <Power size={13} /> {data.enabled ? 'Đang học · tắt' : 'Đã tắt · bật học'}
            </button>
          </div>
          <p className="evo-note rounded border border-border-subtle bg-surface-2 p-3 text-xs leading-relaxed text-text-secondary">
            {data.auto_mode === 'paper_learning'
              ? 'Học thử Paper: bot thử cả chiến thuật chưa kiểm chứng và cập nhật điểm từ mẫu đã đóng. Không có tín hiệu thì đứng ngoài.'
              : data.auto_mode === 'learner'
                ? 'Chỉ giao dịch chiến thuật đã qua cổng forward và được duyệt thủ công.'
                : ''}{' '}
            Đổi chế độ khi Auto Paper đang dừng.
          </p>
          <div className="evo-tabs flex flex-wrap items-center gap-1 border-b border-border-base [&>button]:inline-flex [&>button]:min-h-10 [&>button]:items-center [&>button]:gap-2 [&>button]:border-b-2 [&>button]:border-transparent [&>button]:px-3 [&>button]:text-xs [&>button]:font-medium [&>button]:text-text-muted [&>button:hover]:text-text-primary [&>button[aria-selected=true]]:border-bull [&>button[aria-selected=true]]:text-text-primary" role="tablist" aria-label="Bảng tiến hóa">
            <button role="tab" aria-selected={tab === 'forward'} onClick={() => setTab('forward')}>
              Đang forward <span className="evo-count ml-auto text-xs text-text-muted">{data.population}</span>
            </button>
            <button role="tab" aria-selected={tab === 'drafts'} onClick={() => setTab('drafts')}>
              Con nháp{' '}
              <span className="evo-count ml-auto text-xs text-text-muted">
                {(data.drafts ?? []).filter((d) => d.status !== 'admitted').length}
              </span>
            </button>
          </div>
          {tab === 'drafts' && (
            <FuturesDraftsPanel
              drafts={data.drafts ?? []}
              worker={data.draft_worker}
              now={Math.floor(Date.now() / 1000)}
              onViewTree={(strategy) => {
                setTab('forward');
                setFamily('all');
                setStatus('all');
                setGen('all');
                setQuery(strategy);
              }}
            />
          )}
          {tab === 'forward' && (
            <>
              <div className="evo-filters flex min-w-0 flex-wrap items-center gap-2 text-xs [&>button]:inline-flex [&>button]:min-h-8 [&>button]:items-center [&>button]:gap-2 [&>button]:rounded-md [&>button]:border [&>button]:border-border-subtle [&>button]:px-2.5 [&>button]:text-text-muted [&>button:hover]:bg-surface-2 [&>button[aria-pressed=true]]:border-border-active [&>button[aria-pressed=true]]:bg-surface-3 [&>button[aria-pressed=true]]:text-text-primary" role="group" aria-label="Lọc theo trạng thái">
                {statuses.map(([key, label, match]) => {
                  const count = scoped.filter(match).length;
                  return (
                    <button
                      key={key}
                      aria-pressed={status === key}
                      disabled={count === 0 && status !== key}
                      onClick={() => setStatus(key)}
                    >
                      {label} <span className="evo-count ml-auto text-xs text-text-muted">{count}</span>
                    </button>
                  );
                })}
                {(pausedCount > 0 || showPaused) && (
                  <label className="evo-paused-toggle inline-flex items-center gap-2 text-xs">
                    <input
                      type="checkbox"
                      checked={showPaused}
                      onChange={(event) => setShowPaused(event.target.checked)}
                    />
                    Hiện ngừng thử ({pausedCount})
                  </label>
                )}
              </div>
              <div className="evo-tools grid min-w-0 grid-cols-1 gap-3 rounded-lg border border-border-subtle bg-surface-2/40 p-3 text-xs text-text-muted sm:grid-cols-2 xl:grid-cols-[1fr_1.4fr_1fr_1fr] [&>label]:flex [&>label]:min-w-0 [&>label]:flex-col [&>label]:gap-1.5 [&_input]:w-full [&_select]:w-full [&_input]:text-sm [&_select]:text-sm">
                <label>
                  Họ chiến thuật
                  <select
                    aria-label="Lọc theo họ"
                    value={family}
                    onChange={(event) => setFamily(event.target.value)}
                  >
                    <option value="all">Tất cả họ ({all.length})</option>
                    {families.map((name) => (
                      <option key={name} value={name}>
                        {familyName(name)} (
                        {all.filter((row) => profileOf(row.genome) === name).length})
                      </option>
                    ))}
                  </select>
                </label>
                <label className="evo-search min-w-0">
                  Tìm kiếm
                  <input
                    type="search"
                    aria-label="Tìm chiến thuật"
                    placeholder="Tìm tên, mã, luật…"
                    value={query}
                    onChange={(event) => setQuery(event.target.value)}
                  />
                </label>
                <label>
                  Thế hệ
                  <select
                    aria-label="Lọc theo thế hệ"
                    value={gen}
                    onChange={(event) => setGen(event.target.value)}
                  >
                    <option value="all">Tất cả thế hệ</option>
                    {generations.map((g) => (
                      <option key={g} value={String(g)}>
                        G{g}
                        {g === 0 ? ' · gốc' : ''} (
                        {all.filter((row) => row.generation === g).length})
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  Sắp xếp
                  <select value={sort} onChange={(event) => setSort(event.target.value)}>
                    <option value="score">R học cao nhất</option>
                    <option value="name">Tên A–Z</option>
                    <option value="samples">Nhiều mẫu nhất</option>
                    <option value="generation">Thế hệ mới nhất</option>
                  </select>
                </label>
              </div>
              <div className="evo-result flex flex-wrap items-center justify-between gap-2 text-xs text-text-muted [&_b]:text-text-primary [&>button]:text-bull [&>button:hover]:underline" role="status" aria-live="polite">
                <span>
                  Hiển thị <b>{sorted.length}</b> / {all.length} chiến thuật
                </span>
                {(family !== 'all' ||
                  query ||
                  status !== 'all' ||
                  showPaused ||
                  gen !== 'all' ||
                  sort !== 'score') && (
                  <button
                    onClick={() => {
                      setFamily('all');
                      setQuery('');
                      setStatus('all');
                      setShowPaused(false);
                      setGen('all');
                      setSort('score');
                    }}
                  >
                    Xóa bộ lọc
                  </button>
                )}
              </div>
              {sorted.length === 0 && (
                <p className="evo-empty text-center text-text-muted">
                  Không có chiến thuật khớp bộ lọc. Thử đổi họ hoặc từ khóa.
                </p>
              )}
              {detail && (
                <div
                  className="evo-drawer-backdrop fixed inset-0 z-[70] bg-black/65 backdrop-blur-sm animate-in fade-in duration-200"
                  onClick={() => setDetail(null)}
                >
                  <aside
                    className="evo-drawer fixed inset-y-0 right-0 z-[80] w-[min(560px,100vw)] overflow-y-auto border-l border-border-active bg-surface-1 p-5 text-text-secondary shadow-2xl animate-in slide-in-from-right duration-300 motion-reduce:animate-none"
                    role="dialog"
                    aria-modal="true"
                    aria-label="Cách đánh"
                    onClick={(event) => event.stopPropagation()}
                  >
                {(() => {
                  const row = detail ? all.find((r) => r.strategy === detail) : undefined;
                  if (!row) return null;
                  const info = strategyInfo(row.genome);
                  return (
                    <div className="evo-drawer-body space-y-4">
                      <header className="flex items-start justify-between border-b border-border-base pb-3">
                        <div className="space-y-1">
                          <div className="flex flex-wrap items-center gap-2">
                            <strong className="font-display text-base font-bold text-text-primary">
                              {strategyShortTitle(row.genome, row.strategy)}
                            </strong>
                            <span className="inline-flex items-center gap-1 rounded bg-surface-3 px-2 py-0.5 text-xs font-semibold text-bull">
                              G{row.generation}
                            </span>
                          </div>
                          <code className="block text-xs font-mono text-text-muted">
                            ID: {row.strategy.split(':')[1]}
                          </code>
                        </div>
                        <button
                          type="button"
                          className="inline-flex size-8 shrink-0 items-center justify-center rounded-md text-text-muted hover:bg-surface-2 hover:text-text-primary"
                          onClick={() => setDetail(null)}
                          aria-label="Đóng"
                        >
                          <X size={18} />
                        </button>
                      </header>
                      <ol className="evo-steps space-y-3 text-xs leading-relaxed">
                        {howToPlay(row.genome, info.stop).map((step, index) => {
                          const kind = stepKind(step.label);
                          const borderClass =
                            kind === "entry"
                              ? "border-sky-500/30 bg-sky-950/20"
                              : kind === "sl"
                                ? "border-bear/30 bg-bear-bg/30"
                                : kind === "tp"
                                  ? "border-bull/30 bg-bull-bg/30"
                                  : "border-border-base bg-surface-2/40";
                          const badgeClass =
                            kind === "entry"
                              ? "bg-sky-500/20 text-sky-300 border border-sky-500/40"
                              : kind === "sl"
                                ? "bg-bear/20 text-bear border border-bear/40"
                                : kind === "tp"
                                  ? "bg-bull/20 text-bull border border-bull/40"
                                  : "bg-surface-3 text-text-secondary border border-border-subtle";
                          return (
                            <li key={index} className={`rounded-lg border p-3 ${borderClass}`}>
                              <div className="mb-1.5 flex items-center gap-2">
                                <span className={`inline-flex items-center rounded px-2 py-0.5 text-[11px] font-bold ${badgeClass}`}>
                                  {step.label}
                                </span>
                              </div>
                              <span className="block text-text-secondary">
                                {step.text}
                                {step.items && step.items.length > 0 && (
                                  <ul className="mt-2 space-y-1 pl-4 list-disc text-text-muted">
                                    {step.items.map((item) => (
                                      <li key={item}>{item}</li>
                                    ))}
                                  </ul>
                                )}
                              </span>
                            </li>
                          );
                        })}
                      </ol>
                      <p className="evo-drawer-src rounded border border-border-subtle bg-surface-2 p-3 text-xs leading-relaxed text-text-secondary">
                        <b>Nguồn:</b>{' '}
                        {row.generation === 0
                          ? 'Chiến thuật gốc, tham số mặc định.'
                          : `Biến thể G${row.generation}; chưa chứng minh tốt hơn cha mẹ.`}
                      </p>
                    </div>
                  );
                })()}
                  </aside>
                </div>
              )}
              <div className="evo-grid grid min-w-0 gap-3.5 sm:grid-cols-2 lg:grid-cols-3 2xl:grid-cols-4">
                {sorted.slice((cardPage - 1) * PAGE.cards, cardPage * PAGE.cards).map((row) => {
                  const learned = row.learned_mean_r;
                  const tone = learned === null ? '' : learned > 0 ? 'good' : 'bad';
                  const info = strategyInfo(row.genome);
                  const openTrades = row.open_trades ?? [];
                  return (
                    <article
                      key={row.strategy}
                      className={`evo-card ${tone} ${row.strategy === selected ? 'selected' : ''} ${openTrades.length ? 'holding' : ''} ${signalSet.has(row.strategy) ? 'signaling' : ''} flex min-w-0 flex-col gap-2.5 rounded-xl border border-border-base border-t-sky-400/50 bg-gradient-to-br from-sky-950/25 via-surface-1 to-surface-2/60 p-3.5 transition-colors hover:border-sky-400/40 [&.selected]:border-bull [&.holding]:border-bull/50 [&.signaling]:border-amber-300/50`}
                    >
                      <div className="evo-card-top flex min-w-0 flex-wrap items-center justify-between gap-2 text-xs">
                        <div className="min-w-0 flex-1">
                          <div className="flex flex-wrap items-baseline gap-2">
                            <strong className="font-display text-sm font-semibold leading-snug text-text-primary" title={strategyName(row.genome)}>
                              {strategyShortTitle(row.genome)}
                            </strong>
                            <span className="rounded bg-canvas/60 px-1.5 py-0.5 font-mono text-[10px] text-text-muted">
                              #{row.strategy.split(":")[1]?.slice(0, 6)}
                            </span>
                          </div>
                        </div>
                        <span className={`inline-flex shrink-0 items-center gap-1 rounded border px-1.5 py-0.5 text-[11px] font-medium ${row.generation ? 'border-amber-400/25 bg-amber-400/10 text-amber-300' : 'border-sky-400/25 bg-sky-400/10 text-sky-300'}`}>
                          <Dna size={11} /> G{row.generation}
                        </span>
                      </div>
                      <p className="evo-basis flex min-w-0 flex-wrap items-center gap-2 text-xs">
                        {openTrades.length > 0 && (
                          <span
                            className="pill evo-open-trades inline-flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 text-xs font-medium bg-surface-3 text-text-secondary flex min-w-0 flex-wrap items-center gap-2 text-xs"
                            title={openTrades
                              .map((position) => `#${position.id} ${position.side.toUpperCase()}`)
                              .join(' · ')}
                          >
                            <Activity size={12} aria-hidden="true" /> Đang có {openTrades.length}{' '}
                            lệnh Paper
                          </span>
                        )}
                        {signalSet.has(row.strategy) && (
                          <span
                            className="pill evo-signal inline-flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 text-xs font-medium bg-surface-3 text-text-secondary flex min-w-0 flex-wrap items-center gap-2 text-xs"
                            title="Có tín hiệu ở nến 15m vừa đóng. Chưa chắc đã vào lệnh: cần được bộ chọn ưu tiên và qua kiểm tra rủi ro."
                          >
                            ⚡ Tín hiệu {signalSides.get(row.strategy)?.toUpperCase() ?? ''}
                            {openTrades.length === 0 ? ' · chưa vào lệnh' : ''}
                          </span>
                        )}
                        {row.strategy === selected && (
                          <span
                            className="pill green inline-flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 text-xs font-medium bg-surface-3 text-text-secondary text-bull"
                            title="Được bộ chọn ưu tiên ở nến gần nhất; không xác nhận đã mở lệnh."
                          >
                            Được chọn gần nhất
                          </span>
                        )}
                        {row.paused && (
                          <span
                            className="pill inline-flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 text-xs font-medium bg-surface-3 text-text-secondary"
                            title="Chiến thuật không bị loại; chính sách thoát này chưa được bật nên không mở lệnh mới. Mẫu cũ vẫn được giữ."
                          >
                            Ngừng thử · thoát {row.genome.exit_policy ?? 'fixed'} chưa bật
                          </span>
                        )}
                        <span className="inline-flex items-center rounded border border-teal-400/20 bg-teal-400/10 px-1.5 py-0.5 text-[11px] text-teal-300">{row.shadow_only ? 'Shadow' : 'Thử Paper'}</span>
                        <span className="rounded border border-sky-400/20 bg-sky-400/10 px-1.5 py-0.5 text-[11px] leading-relaxed text-sky-200">{exitLabel(row.genome.exit_policy)}</span>
                      </p>
                      {row.rule_text && (
                        <p className="evo-rule line-clamp-2 text-xs leading-relaxed text-text-muted" title={row.rule_text}>
                          {row.rule_text}
                        </p>
                      )}
                      {row.parents.length > 0 && (
                        <details className="evo-origin min-w-0 rounded-md border border-border-subtle p-2 text-xs leading-relaxed text-text-muted [&_p]:mt-2 [&_p]:break-words">
                          <summary className="cursor-pointer font-medium text-text-secondary">Nguồn gốc & biến đổi</summary>
                          <div className="evo-origin-head flex min-w-0 flex-wrap items-center justify-between gap-2 text-xs">
                            <span>{row.parents.length === 1 ? 'Đột biến từ' : 'Lai'}</span>
                            {row.parents.map((parent, idx) => {
                              const pRow = all.find((r) => r.strategy === parent);
                              return (
                                <span key={`${parent}-${idx}`} className="evo-origin-parent text-xs text-text-muted">
                                  {idx > 0 && <span aria-hidden="true">×</span>}
                                  <ParentChip
                                    id={`parent-tooltip-${row.strategy}-${idx}`}
                                    tag={row.parents.length > 1 ? 'AB'[idx] : undefined}
                                    label={
                                      pRow
                                        ? strategyShortTitle(pRow.genome, parent)
                                        : `#${parent.split(':')[1]?.slice(0, 6)} · đã loại`
                                    }
                                    title={titles.get(parent) ?? parent}
                                  />
                                </span>
                              );
                            })}
                          </div>
                          {row.lineage?.from_mate.length ? (
                            <p>
                              <span className="evo-origin-tag text-xs text-text-muted">Lấy từ B</span>
                              {row.lineage.from_mate
                                .map(
                                  (item) =>
                                    `${GENE_LABELS[item.gene] ?? item.gene} ${geneText(item.gene, item.value)}`,
                                )
                                .join(' · ')}
                            </p>
                          ) : null}
                          {row.lineage?.mutated.map((item) => (
                            <p key={item.gene}>
                              <span className="evo-origin-tag mutated text-xs text-text-muted">✦ Đột biến</span>
                              {GENE_LABELS[item.gene] ?? item.gene}:{' '}
                              {geneText(item.gene, item.before)} →{' '}
                              <b>{geneText(item.gene, item.after)}</b>
                            </p>
                          ))}
                          {row.lineage &&
                            !row.lineage.mutated.length &&
                            !row.lineage.from_mate.length && (
                              <p>Gen trùng cha mẹ; khác ở cách ghép điều kiện.</p>
                            )}
                        </details>
                      )}
                      <div className="evo-score mt-auto grid min-w-0 grid-cols-2 items-center gap-2 rounded-lg border border-sky-400/10 bg-canvas/60 p-2.5 text-[11px]">
                        <div>
                        <span className="mb-1 block text-text-muted">R học trung bình</span>
                        {row.learned_samples > 0 ? (
                          <b className={`font-mono text-xl tabular-nums ${learned! > 0 ? 'text-bull' : learned! < 0 ? 'text-bear' : 'text-text-primary'}`}>{`${learned! > 0 ? '+' : ''}${fmt(learned)}R`}</b>
                        ) : (
                          <span className="text-sm text-text-muted">Chưa có mẫu học</span>
                        )}
                        </div>
                        <span className="self-center text-right leading-relaxed text-text-muted">
                          {row.learned_samples > 0 && `${row.learned_samples} mẫu · `}Replay{' '}
                          {row.replay_samples ?? 0} · Forward {row.forward_samples ?? row.resolved}{' '}
                          đã đóng
                          {row.forward_open ? ` · ${row.forward_open} đang chạy` : ''}
                          {row.profit_factor !== null && ` · PF ${fmt(row.profit_factor)}`}
                        </span>
                      </div>
                      <div
                        className={`evo-paper ${openTrades.length || row.paper?.closed ? 'active' : ''} flex min-w-0 flex-wrap items-center justify-between gap-2 border-b border-border-subtle pb-3 text-xs`}
                      >
                        <span>Auto Paper</span>
                        {row.paper?.closed ? (
                          <b className={row.paper.net_pnl >= 0 ? "good text-bull" : "bad text-bear"}>
                            {usdt(row.paper.net_pnl)}
                          </b>
                        ) : null}
                        {openTrades.length || row.paper?.closed ? (
                          <span className="evo-paper-detail w-full text-xs leading-relaxed text-text-muted">
                            {openTrades.length > 0 && (
                              <>
                                Đang mở{' '}
                                {openTrades.map((trade) =>
                                  onViewTrade ? (
                                    <button
                                      key={trade.id}
                                      type="button"
                                      className="text-button inline-flex items-center gap-1 text-xs text-bull underline-offset-4 hover:underline"
                                      onClick={() => onViewTrade(trade.id)}
                                    >
                                      #{trade.id}
                                    </button>
                                  ) : (
                                    <span key={trade.id}>#{trade.id} </span>
                                  ),
                                )}
                              </>
                            )}
                            {openTrades.length > 0 && row.paper?.closed ? ' · ' : ''}
                            {row.paper?.closed ? (
                              <>
                                {row.paper.closed} đã đóng · thắng {row.paper.wins}
                              </>
                            ) : (
                              ''
                            )}
                          </span>
                        ) : (
                          <span className="muted text-text-muted">Chưa vào lệnh</span>
                        )}
                      </div>
                      <dl className="space-y-2 text-xs leading-relaxed text-text-muted [&_dd]:text-text-secondary">
                        <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
                          <dt>Tham số</dt>
                          <dd>
                            N{row.genome.lookback} · {targetCapText(row.genome)} · SL +
                            {row.genome.stop_buffer}
                          </dd>
                        </div>
                        {(row.qualification.windows > 0 || row.qualification.trades > 0) && (
                          <div className="evo-gate flex min-w-0 flex-wrap items-center gap-2 text-xs">
                            <dt>Cổng duyệt</dt>
                            <dd>
                              <progress
                                value={Math.min(row.qualification.trades, 100)}
                                max={100}
                                aria-label={`${row.qualification.trades}/100 lệnh forward`}
                                className="mr-2 h-1.5 w-20 max-w-full accent-bull"
                              />
                              {row.qualification.trades}/100 lệnh · {row.qualification.windows} cửa
                              sổ
                            </dd>
                          </div>
                        )}
                      </dl>
                      <button
                        type="button"
                        className="evo-more-btn min-h-9 w-full rounded-md border border-sky-400/25 bg-sky-400/10 px-3 py-2 text-xs font-medium text-sky-200 transition-colors hover:border-sky-400/50 hover:bg-sky-400/20 hover:text-white focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-bull"
                        onClick={() => setDetail(row.strategy)}
                      >
                        Cách đánh chi tiết →
                      </button>
                      {row.promoted ? (
                        <button
                          disabled={busy}
                          onClick={() =>
                            act('/promotion', { strategy: row.strategy, promoted: false })
                          }
                        >
                          Đã duyệt · gỡ
                        </button>
                      ) : (
                        row.qualification.eligible && (
                          <button
                            disabled={busy}
                            onClick={() =>
                              act('/promotion', { strategy: row.strategy, promoted: true })
                            }
                          >
                            Duyệt cho Auto
                          </button>
                        )
                      )}
                    </article>
                  );
                })}
              </div>
              <Pager
                total={sorted.length}
                page={cardPage}
                size={PAGE.cards}
                onPage={setPage}
                unit="chiến thuật"
              />
              {(data.retired_strategies?.length ?? 0) > 0 && (
                <details className="evo-retired group min-w-0 rounded-xl border border-border-base bg-surface-2/25 open:border-amber-400/25">
                  <summary className="flex min-h-14 list-none items-center gap-3 rounded-xl px-3 py-3 text-sm font-medium text-text-secondary transition-colors hover:bg-surface-2 [&::-webkit-details-marker]:hidden">
                    <span className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-amber-400/10 text-amber-300"><Archive size={16} aria-hidden="true" /></span>
                    <span className="min-w-0 flex-1">Cây đã loại</span>
                    <span className="evo-count rounded-md border border-amber-400/20 bg-amber-400/10 px-2 py-0.5 text-xs tabular-nums text-amber-200">{data.retired_strategies!.length}</span>
                    <ChevronDown size={16} className="shrink-0 text-text-muted transition-transform group-open:rotate-180 motion-reduce:transition-none" aria-hidden="true" />
                  </summary>
                  <div className="evo-retired-body min-w-0 space-y-3 border-t border-border-subtle p-3 [&_table]:w-full [&_table]:min-w-[720px] [&_table]:text-xs [&_th]:whitespace-nowrap [&_small]:mt-1 [&_small]:block [&_small]:text-text-muted [&_td]:break-words [&_td]:align-middle [&_tr:hover]:bg-surface-2/60">
                    <div className="table-scroll min-w-0 max-w-full overflow-x-auto overscroll-x-contain">
                      <table>
                        <thead>
                          <tr>
                            <th>Cây · thế hệ</th>
                            <th>Lý do · thời điểm</th>
                            <th className="num font-mono tabular-nums text-right">Mẫu đóng</th>
                            <th className="num font-mono tabular-nums text-right">R TB</th>
                            <th className="num font-mono tabular-nums text-right">DD</th>
                            <th>Cây giữ lại</th>
                          </tr>
                        </thead>
                        <tbody>
                          {data
                            .retired_strategies!.slice(
                              (treePage - 1) * PAGE.retired,
                              treePage * PAGE.retired,
                            )
                            .map((row) => {
                              const r = row.retirement;
                              const [label, tone] = r
                                ? (RETIRE_REASON[r.reason] ?? [r.reason, 'info'])
                                : ['', ''];
                              return (
                                <tr key={row.strategy}>
                                  <td title={row.strategy}>
                                    {strategyShortTitle(row.genome, row.strategy)}
                                    <small>G{row.generation}</small>
                                  </td>
                                  <td>
                                    {r ? (
                                      <>
                                        <span className={`evo-pill ${tone} inline-flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 text-xs font-medium bg-surface-3 text-text-secondary`}>{label}</span>
                                        <small>{formatTime(r.at)}</small>
                                      </>
                                    ) : (
                                      <span className="evo-pill inline-flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 text-xs font-medium bg-surface-3 text-text-secondary">Lịch sử cũ · chưa ghi lý do</span>
                                    )}
                                  </td>
                                  <td className="num font-mono tabular-nums text-right">{r?.samples ?? '—'}</td>
                                  <td
                                    className={`num ${r ? (r.mean_r >= 0 ? 'positive' : 'negative') : ''} font-mono tabular-nums text-right`}
                                  >
                                    {r ? `${r.mean_r.toFixed(2)}R` : '—'}
                                  </td>
                                  <td className="num font-mono tabular-nums text-right">
                                    {r ? `${r.max_drawdown_r.toFixed(2)}R` : '—'}
                                  </td>
                                  <td>
                                    <span title={r?.retained_strategy ?? undefined}>
                                      {r?.retained_strategy ?? '—'}
                                    </span>
                                    {r?.overlap && (
                                      <small>
                                        {r.overlap.shared} mẫu khớp ·{' '}
                                        {(100 * r.overlap.ratio).toFixed(1)}% trùng
                                      </small>
                                    )}
                                  </td>
                                </tr>
                              );
                            })}
                        </tbody>
                      </table>
                    </div>
                    <Pager
                      total={data.retired_strategies!.length}
                      page={treePage}
                      size={PAGE.retired}
                      onPage={setRetiredPage}
                      unit="cây"
                    />
                  </div>
                </details>
              )}
              {(data.retired_paper?.length ?? 0) > 0 && (
                <details className="evo-retired group min-w-0 rounded-xl border border-border-base bg-surface-2/25 open:border-sky-400/25">
                  <summary className="flex min-h-14 list-none items-center gap-3 rounded-xl px-3 py-3 text-sm font-medium text-text-secondary transition-colors hover:bg-surface-2 [&::-webkit-details-marker]:hidden">
                    <span className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-sky-400/10 text-sky-300"><Activity size={16} aria-hidden="true" /></span>
                    <span className="min-w-0 flex-1">Đã loại nhưng từng chạy Auto Paper</span>
                    <span className="evo-count rounded-md border border-sky-400/20 bg-sky-400/10 px-2 py-0.5 text-xs tabular-nums text-sky-200">{data.retired_paper!.length}</span>
                    <ChevronDown size={16} className="shrink-0 text-text-muted transition-transform group-open:rotate-180 motion-reduce:transition-none" aria-hidden="true" />
                  </summary>
                  <div className="evo-retired-body min-w-0 space-y-3 border-t border-border-subtle p-3 [&_table]:w-full [&_table]:min-w-[600px] [&_table]:text-xs [&_th]:whitespace-nowrap [&_small]:mt-1 [&_small]:block [&_small]:text-text-muted [&_td]:break-words [&_td]:align-middle [&_tr:hover]:bg-surface-2/60">
                    <div className="table-scroll min-w-0 max-w-full overflow-x-auto overscroll-x-contain">
                      <table>
                        <thead>
                          <tr>
                            <th>Chiến thuật</th>
                            <th className="num font-mono tabular-nums text-right">Lệnh đóng</th>
                            <th className="num font-mono tabular-nums text-right">Thắng</th>
                            <th className="num font-mono tabular-nums text-right">PnL ròng</th>
                            <th>Lệnh gần nhất</th>
                          </tr>
                        </thead>
                        <tbody>
                          {data
                            .retired_paper!.slice(
                              (paperPageNow - 1) * PAGE.retired,
                              paperPageNow * PAGE.retired,
                            )
                            .map((row) => (
                              <tr key={row.strategy}>
                                <td title={row.strategy}>
                                  {row.genome
                                    ? strategyShortTitle(row.genome, row.strategy)
                                    : row.strategy}
                                  {row.open > 0 && <small>{row.open} lệnh còn mở</small>}
                                </td>
                                <td className="num font-mono tabular-nums text-right">{row.closed}</td>
                                <td className="num font-mono tabular-nums text-right">{row.wins}</td>
                                <td className={`num ${row.net_pnl >= 0 ? 'positive' : 'negative'} font-mono tabular-nums text-right`}>
                                  {usdt(row.net_pnl)}
                                </td>
                                <td>
                                  {row.last_trade_id === null ? (
                                    '—'
                                  ) : onViewTrade ? (
                                    <button
                                      type="button"
                                      className="text-button inline-flex items-center gap-1 text-xs text-bull underline-offset-4 hover:underline"
                                      onClick={() => onViewTrade(row.last_trade_id!)}
                                    >
                                      #{row.last_trade_id}
                                    </button>
                                  ) : (
                                    `#${row.last_trade_id}`
                                  )}
                                </td>
                              </tr>
                            ))}
                        </tbody>
                      </table>
                    </div>
                    <Pager
                      total={data.retired_paper!.length}
                      page={paperPageNow}
                      size={PAGE.retired}
                      onPage={setPaperPage}
                      unit="chiến thuật"
                    />
                  </div>
                </details>
              )}
            </>
          )}
          <p className="evo-note rounded border border-border-subtle bg-surface-2 p-3 text-xs leading-relaxed text-text-secondary">
            Đào thải G0 và cây con cùng tiêu chí, chỉ khi đầy quần thể và có con hợp lệ thay thế:
            ≥50 mẫu forward đã khớp và đóng, R ròng âm, drawdown ≥10R hoặc trùng mẫu forward với cây
            tốt hơn đã đủ bằng chứng. 10R không phải 10% tài khoản. Replay không dùng để quyết định
            loại. Cây còn mẫu mở, vị thế hoặc lệnh chờ được bảo vệ.
          </p>
          <p className="evo-note rounded border border-border-subtle bg-surface-2 p-3 text-xs leading-relaxed text-text-secondary">
            Mẫu học = replay lịch sử + forward giả lập mọi tín hiệu (kể cả khi Auto Paper không
            vào). Kết quả Auto Paper chỉ để đối chiếu, chưa được tính vào điểm học. Họ gốc đổi tham
            số; cây luật có thể đổi điều kiện và cách ghép. Họ gốc chọn cha mẹ có ≥10 mẫu và R
            dương; cây luật xếp hạng từ ≥10 mẫu, trừ điểm theo độ phức tạp. Biến thể chưa chứng minh
            tốt hơn. Cổng duyệt: ≥100 lệnh forward, PF &gt; 1.3, ≥60% cửa sổ 30 ngày dương, DD &lt;
            10%.
          </p>
        </>
      )}
    </section>
  );
}
