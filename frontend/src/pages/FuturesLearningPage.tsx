import { futuresHeaders } from '../lib/futures-symbol';
import { fetchFuturesHistory } from '../lib/api';
import type { FuturesHistoryJob } from '../types/paper';
import { formatTime } from '../lib/time-format';
import { useEffect, useState } from 'react';
import { Activity, Brain, Dna, Power } from 'lucide-react';
import {
  exitLabel,
  HTF_LABELS,
  profileOf,
  riskText,
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
    <span className="evo-chip-wrap">
      <code className="evo-parent-chip" tabIndex={0} aria-describedby={id}>
        {tag && <b>{tag}</b>} {label}
      </code>
      <span id={id} className="evo-tooltip" role="tooltip">
        <span className="evo-tooltip-title">{label}</span>
        <span className="evo-tooltip-body">{title}</span>
      </span>
    </span>
  );
}

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
    const timer = setInterval(load, 15000);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, []);

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
    <section className="panel evo">
      <header className="evo-head">
        <div>
          <h2>
            <Brain size={17} /> Bot học Futures · Tiến hóa
          </h2>
          <p>
            Khung HTF: <b>H4</b> lọc xu hướng EMA50 · <b>H1</b> cấu trúc điểm tựa · <b>M15</b>{' '}
            trigger đóng nến. Cây luật tự sinh biến đổi cấu trúc và điều kiện.
          </p>
        </div>
        <span className="pill amber">Paper · không gửi lệnh sàn</span>
      </header>
      {error && (
        <p className="negative" role="alert">
          {error}
        </p>
      )}
      {data && (
        <>
          <div className="evo-kpis">
            {kpis.map(([label, value]) => (
              <div key={label}>
                <span>{label}</span>
                <strong>{value}</strong>
              </div>
            ))}
          </div>
          {replay?.report && (
            <p className="evo-replay" role="status">
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
            <p className="evo-warn" role="status">
              {data.stale && 'Dữ liệu học đang chậm hoặc chưa chạy. '}
              {data.error}
            </p>
          )}
          <div className="evo-controls">
            <div className="evo-seg" role="group" aria-label="Chế độ chọn chiến thuật">
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
              className="evo-power"
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
          <p className="evo-note">
            {data.auto_mode === 'paper_learning'
              ? 'Học thử Paper: bot thử cả chiến thuật chưa kiểm chứng và cập nhật điểm từ mẫu đã đóng. Không có tín hiệu thì đứng ngoài.'
              : data.auto_mode === 'learner'
                ? 'Chỉ giao dịch chiến thuật đã qua cổng forward và được duyệt thủ công.'
                : ''}{' '}
            Đổi chế độ khi Auto Paper đang dừng.
          </p>
          <div className="evo-filters" role="group" aria-label="Lọc theo trạng thái">
            {statuses.map(([key, label, match]) => {
              const count = scoped.filter(match).length;
              return (
                <button
                  key={key}
                  aria-pressed={status === key}
                  disabled={count === 0 && status !== key}
                  onClick={() => setStatus(key)}
                >
                  {label} <span className="evo-count">{count}</span>
                </button>
              );
            })}
            {(pausedCount > 0 || showPaused) && (
              <label className="evo-paused-toggle">
                <input
                  type="checkbox"
                  checked={showPaused}
                  onChange={(event) => setShowPaused(event.target.checked)}
                />
                Hiện ngừng thử ({pausedCount})
              </label>
            )}
          </div>
          <div className="evo-tools">
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
                    {familyName(name)} ({all.filter((row) => profileOf(row.genome) === name).length}
                    )
                  </option>
                ))}
              </select>
            </label>
            <label className="evo-search">
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
                    {g === 0 ? ' · gốc' : ''} ({all.filter((row) => row.generation === g).length})
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
          <div className="evo-result" role="status" aria-live="polite">
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
            <p className="evo-empty">Không có chiến thuật khớp bộ lọc. Thử đổi họ hoặc từ khóa.</p>
          )}
          <div className="evo-grid">
            {sorted.map((row) => {
              const learned = row.learned_mean_r;
              const tone = learned === null ? '' : learned > 0 ? 'good' : 'bad';
              const info = strategyInfo(row.genome);
              const openTrades = row.open_trades ?? [];
              return (
                <article
                  key={row.strategy}
                  className={`evo-card ${tone} ${row.strategy === selected ? 'selected' : ''} ${openTrades.length ? 'holding' : ''} ${signalSet.has(row.strategy) ? 'signaling' : ''}`}
                >
                  <div className="evo-card-top">
                    <div>
                      <strong title={strategyName(row.genome)}>
                        {strategyShortTitle(row.genome, row.strategy)}
                      </strong>
                      <code title={row.strategy}>{row.strategy.split(':')[1]}</code>
                    </div>
                    <span className={`pill ${row.generation ? 'green' : ''}`}>
                      <Dna size={11} /> G{row.generation}
                    </span>
                  </div>
                  <p className="evo-basis">
                    {openTrades.length > 0 && (
                      <span
                        className="pill evo-open-trades"
                        title={openTrades
                          .map((position) => `#${position.id} ${position.side.toUpperCase()}`)
                          .join(' · ')}
                      >
                        <Activity size={12} aria-hidden="true" /> Đang có {openTrades.length} lệnh
                        Paper
                      </span>
                    )}
                    {signalSet.has(row.strategy) && (
                      <span
                        className="pill evo-signal"
                        title="Có tín hiệu ở nến 15m vừa đóng. Chưa chắc đã vào lệnh: cần được bộ chọn ưu tiên và qua kiểm tra rủi ro."
                      >
                        ⚡ Tín hiệu {signalSides.get(row.strategy)?.toUpperCase() ?? ''}
                        {openTrades.length === 0 ? ' · chưa vào lệnh' : ''}
                      </span>
                    )}
                    {row.strategy === selected && (
                      <span
                        className="pill green"
                        title="Được bộ chọn ưu tiên ở nến gần nhất; không xác nhận đã mở lệnh."
                      >
                        Được chọn gần nhất
                      </span>
                    )}
                    {row.paused && (
                      <span
                        className="pill"
                        title="Chiến thuật không bị loại; chính sách thoát này chưa được bật nên không mở lệnh mới. Mẫu cũ vẫn được giữ."
                      >
                        Ngừng thử · thoát {row.genome.exit_policy ?? 'fixed'} chưa bật
                      </span>
                    )}
                    <span className="pill">{row.shadow_only ? 'Shadow' : 'Thử Paper'}</span>
                    <span className="pill blue">{exitLabel(row.genome.exit_policy)}</span>
                  </p>
                  {row.rule_text && (
                    <p className="evo-rule" title={row.rule_text}>
                      {row.rule_text}
                    </p>
                  )}
                  {row.parents.length > 0 && (
                    <div className="evo-origin">
                      <div className="evo-origin-head">
                        <span>{row.parents.length === 1 ? 'Đột biến từ' : 'Lai'}</span>
                        {row.parents.map((parent, idx) => {
                          const pRow = all.find((r) => r.strategy === parent);
                          return (
                            <span key={`${parent}-${idx}`} className="evo-origin-parent">
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
                          <span className="evo-origin-tag">Lấy từ B</span>
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
                          <span className="evo-origin-tag mutated">✦ Đột biến</span>
                          {GENE_LABELS[item.gene] ?? item.gene}: {geneText(item.gene, item.before)}{' '}
                          → <b>{geneText(item.gene, item.after)}</b>
                        </p>
                      ))}
                      {row.lineage &&
                        !row.lineage.mutated.length &&
                        !row.lineage.from_mate.length && (
                          <p>Gen trùng cha mẹ; khác ở cách ghép điều kiện.</p>
                        )}
                    </div>
                  )}
                  <div className="evo-score">
                    {row.learned_samples > 0 ? (
                      <b className={tone}>{`${learned! > 0 ? '+' : ''}${fmt(learned)}R`}</b>
                    ) : (
                      <em>Chưa có mẫu học</em>
                    )}
                    <span>
                      {row.learned_samples > 0 && `${row.learned_samples} mẫu · `}Replay{' '}
                      {row.replay_samples ?? 0} · Forward {row.forward_samples ?? row.resolved} đã
                      đóng
                      {row.forward_open ? ` · ${row.forward_open} đang chạy` : ''}
                      {row.profit_factor !== null && ` · PF ${fmt(row.profit_factor)}`}
                    </span>
                  </div>
                  <div
                    className={`evo-paper ${openTrades.length || row.paper?.closed ? 'active' : ''}`}
                  >
                    <span>Auto Paper</span>
                    {row.paper?.closed ? (
                      <b className={row.paper.net_pnl >= 0 ? 'good' : 'bad'}>
                        {usdt(row.paper.net_pnl)}
                      </b>
                    ) : null}
                    {openTrades.length || row.paper?.closed ? (
                      <span className="evo-paper-detail">
                        {openTrades.length > 0 && (
                          <>
                            Đang mở{' '}
                            {openTrades.map((trade) =>
                              onViewTrade ? (
                                <button
                                  key={trade.id}
                                  type="button"
                                  className="text-button"
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
                      <span className="muted">Chưa vào lệnh</span>
                    )}
                  </div>
                  <dl>
                    <div>
                      <dt>Tham số</dt>
                      <dd>
                        N{row.genome.lookback} · TP {row.genome.target_r}R · SL +
                        {row.genome.stop_buffer}
                      </dd>
                    </div>
                    {(row.qualification.windows > 0 || row.qualification.trades > 0) && (
                      <div className="evo-gate">
                        <dt>Cổng duyệt</dt>
                        <dd>
                          <progress
                            value={Math.min(row.qualification.trades, 100)}
                            max={100}
                            aria-label={`${row.qualification.trades}/100 lệnh forward`}
                          />
                          {row.qualification.trades}/100 lệnh · {row.qualification.windows} cửa sổ
                        </dd>
                      </div>
                    )}
                  </dl>
                  <details className="evo-more">
                    <summary>Cách đánh</summary>
                    {/* Capped and scrolled so one open card doesn't stretch its whole grid row. */}
                    <div className="evo-more-body" tabIndex={0}>
                      {!row.rule_text && (
                        <p>
                          <b>Vào lệnh:</b> {info.entry}
                        </p>
                      )}
                      <p>
                        <b>Thoát:</b> {riskText(row.genome, info.stop)}
                      </p>
                      <p>
                        <b>Nguồn:</b>{' '}
                        {row.generation === 0
                          ? 'Chiến thuật gốc, tham số mặc định.'
                          : `Biến thể G${row.generation}; chưa chứng minh tốt hơn cha mẹ.`}
                      </p>
                    </div>
                  </details>
                  {row.promoted ? (
                    <button
                      disabled={busy}
                      onClick={() => act('/promotion', { strategy: row.strategy, promoted: false })}
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
          {(data.retired_strategies?.length ?? 0) > 0 && (
            <details className="evo-retired">
              <summary>Cây đã loại ({data.retired_strategies!.length})</summary>
              <div className="table-scroll">
                <table>
                  <thead>
                    <tr>
                      <th>Cây · thế hệ</th>
                      <th>Lý do · thời điểm</th>
                      <th>Mẫu forward đóng</th>
                      <th>R trung bình</th>
                      <th>Drawdown R</th>
                      <th>Cây giữ lại</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.retired_strategies!.map((row) => (
                      <tr key={row.strategy}>
                        <td title={row.strategy}>
                          {strategyShortTitle(row.genome, row.strategy)} · G{row.generation}
                        </td>
                        <td>
                          {row.retirement ? (
                            <>
                              {(
                                {
                                  negative_forward_r: 'R forward âm',
                                  forward_drawdown: 'Drawdown ≥10R',
                                  redundant_forward: 'Trùng mẫu forward với cây tốt hơn',
                                } as Record<string, string>
                              )[row.retirement.reason] ?? row.retirement.reason}
                              <small>{formatTime(row.retirement.at)}</small>
                            </>
                          ) : (
                            'Lịch sử cũ · chưa ghi lý do'
                          )}
                        </td>
                        <td>{row.retirement?.samples ?? '—'}</td>
                        <td>{row.retirement ? `${row.retirement.mean_r.toFixed(2)}R` : '—'}</td>
                        <td>
                          {row.retirement ? `${row.retirement.max_drawdown_r.toFixed(2)}R` : '—'}
                        </td>
                        <td>
                          <span title={row.retirement?.retained_strategy ?? undefined}>
                            {row.retirement?.retained_strategy ?? '—'}
                          </span>
                          {row.retirement?.overlap && (
                            <small>
                              {row.retirement.overlap.shared} mẫu khớp ·{' '}
                              {(100 * row.retirement.overlap.ratio).toFixed(1)}% trùng
                            </small>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </details>
          )}
          {(data.retired_paper?.length ?? 0) > 0 && (
            <details className="evo-retired">
              <summary>
                Chiến thuật đã loại nhưng từng chạy Auto Paper ({data.retired_paper!.length})
              </summary>
              <div className="table-scroll">
                <table>
                  <thead>
                    <tr>
                      <th>Chiến thuật</th>
                      <th>Lệnh đã đóng</th>
                      <th>Thắng</th>
                      <th>PnL ròng</th>
                      <th>Lệnh gần nhất</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.retired_paper!.map((row) => (
                      <tr key={row.strategy}>
                        <td title={row.strategy}>
                          {row.genome ? strategyShortTitle(row.genome, row.strategy) : row.strategy}
                          {row.open > 0 && <small>{row.open} lệnh còn mở</small>}
                        </td>
                        <td>{row.closed}</td>
                        <td>{row.wins}</td>
                        <td className={row.net_pnl >= 0 ? 'positive' : 'negative'}>
                          {usdt(row.net_pnl)}
                        </td>
                        <td>
                          {row.last_trade_id === null ? (
                            '—'
                          ) : onViewTrade ? (
                            <button
                              type="button"
                              className="text-button"
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
            </details>
          )}
          <p className="evo-note">
            Đào thải G0 và cây con cùng tiêu chí, chỉ khi đầy quần thể và có con hợp lệ thay thế:
            ≥50 mẫu forward đã khớp và đóng, R ròng âm, drawdown ≥10R hoặc trùng mẫu forward với cây
            tốt hơn đã đủ bằng chứng. 10R không phải 10% tài khoản. Replay không dùng để quyết định
            loại. Cây còn mẫu mở, vị thế hoặc lệnh chờ được bảo vệ.
          </p>
          <p className="evo-note">
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
