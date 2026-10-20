import { forexSetupGuides, forexTriggerGuides, forexExitGuides } from '../lib/forex-strategy-info';
import { useEffect, useMemo, useRef, useState } from 'react';
import { Eye, FlaskConical, AlertTriangle } from 'lucide-react';
import type { ForexDashboard } from '../App';
import {
  MtfObservationChart,
  type ObservationChartSnapshot,
} from '../components/MtfObservationChart';
import { StrategyOverlayMenu } from '../components/StrategyOverlayMenu';
import { formatPrice, observationStatusLabels } from '../lib/format';
import type { BrokerPosition, ExnessExecution, LearnerAnalysis } from '../types/exness-execution';

interface ForexCandle {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  tick_volume?: number | null;
}

interface QuoteState {
  status: string;
  quote: { bid: number; ask: number; time: number } | null;
  candles?: Record<
    string,
    {
      time: number;
      partial: boolean;
      bid: Omit<ForexCandle, 'time'>;
      ask: Omit<ForexCandle, 'time'>;
    }[]
  >;
}

const quoteStatusLabels: Record<string, string> = {
  connecting: 'Đang kết nối Exness',
  connected: 'Đang chờ giá',
  waiting: 'Đang chờ giá',
  live: 'Giá trực tiếp',
  stale: 'Giá cũ / chưa có tick mới',
  reconnecting: 'Mất kết nối · đang kết nối lại',
  disconnected: 'Mất kết nối stream',
  disabled: 'Stream chưa bật',
  unavailable: 'Stream chưa sẵn sàng',
  rejected: 'Exness từ chối stream · kiểm tra quyền key',
  rate_limited: 'Stream bị giới hạn quota · cần kiểm tra trước khi khởi động lại API',
};

interface ReplayReportItem {
  start: number;
  end: number;
  as_of: number;
  dataset_sha256: string;
  summary: {
    confirmations: number;
    resolved: number;
    win_rate: number | null;
    mean_r_ex_swap: number | null;
    sum_r_ex_swap: number;
    profit_factor_ex_swap: number | null;
    closed_sample_drawdown_r: number;
  };
  by_side: {
    long: { resolved: number; sum_r_ex_swap: number; win_rate: number | null };
    short: { resolved: number; sum_r_ex_swap: number; win_rate: number | null };
  };
  assumptions: {
    slippage_bps_each_side: number;
    entry: string;
    exit: string;
    swap_included: boolean;
    portfolio_simulation: boolean;
  };
}

interface ResearchApiResponse {
  available: boolean;
  gap_audits?: {
    after: number;
    before: number;
    status: string;
    checked_at: number;
    m15_bars?: number;
    m1_bars?: number;
  }[];
  reports: ReplayReportItem[];
  comparison?: {
    start: number;
    end: number;
    as_of: number;
    processed_bars: number;
    results: {
      strategy: string;
      slippage_bps: number;
      signals: number;
      skipped_busy: number;
      trades: number;
      win_rate: number | null;
      final_balance: number;
      return_pct: number;
      profit_factor: number | null;
      max_drawdown_pct: number;
      mean_r: number | null;
      status: 'blocked' | 'open' | 'complete';
      blocked_at: number | null;
      equity_verified: boolean;
      blocked_gap?: {
        after: number;
        before: number;
        missing_intervals: number;
        classification: string;
      } | null;
      excluded_trades?: number;
      session_closes?: number;
      rejection_reasons: Record<string, number>;
    }[];
  } | null;
  error: string | null;
}

export function ForexCatchupStatus({
  dashboard,
  disconnected,
}: {
  dashboard: ForexDashboard;
  disconnected: boolean;
}) {
  const progress = dashboard.observation.catch_up;
  const frames = dashboard.frames ?? [];
  const downloading = frames.some(
    (frame) => frame.remaining_intervals === null || frame.remaining_intervals > 0,
  );
  const paused = dashboard.observation.status === 'data_paused';
  const rateLimited = frames.some((frame) => frame.error?.includes('rate limit'));
  const title = disconnected
    ? 'Mất kết nối API · dữ liệu lưu gần nhất'
    : !dashboard.market.worker_online
      ? 'Worker ngoại tuyến · chờ chạy lại để tải bù'
      : downloading
        ? 'Đang tải bù nến Exness'
        : paused
          ? rateLimited
            ? 'Exness giới hạn API · chờ tự thử lại'
            : 'Tạm dừng phân tích · kiểm tra lỗi dữ liệu'
          : progress?.pending_bars
            ? 'Đang tính bù MTF'
            : !progress || progress.last_processed_candle === null
              ? 'Chờ khởi tạo MTF'
              : 'Đã phân tích hết nến hiện có';

  return (
    <section className="panel forex-catchup" role="status" aria-live="polite">
      <strong>{title}</strong>
      <div className="forex-catchup-counts">
        {frames.map((frame) => (
          <span key={frame.timeframe}>
            <strong>{frame.timeframe.toUpperCase()}</strong> ·{' '}
            {frame.remaining_intervals === null
              ? 'Chưa khởi tạo'
              : `${frame.remaining_intervals.toLocaleString('vi-VN')} khoảng nến cần kiểm tra`}
            {frame.error && (
              <>
                {' '}
                · {frame.error}
                {frame.retry_at &&
                  ` · thử lại sau ${new Date(frame.retry_at).toISOString().slice(11, 19)} UTC`}
              </>
            )}
          </span>
        ))}
        <span>
          <strong>MTF 15m</strong> ·{' '}
          {progress
            ? `${progress.pending_bars.toLocaleString('vi-VN')} nến đã tải chờ phân tích`
            : 'Chưa có tiến độ'}
        </span>
      </div>
      <p className="micro">
        Đã phân tích đến nến:{' '}
        {progress?.last_processed_candle != null
          ? `${new Date(progress.last_processed_candle * 1000).toISOString().slice(0, 16).replace('T', ' ')} UTC (giờ mở nến)`
          : 'Chưa có'}
        . Tính bù chỉ bắt đầu khi cả 3 khung đồng bộ và hợp lệ. Khoảng cần kiểm tra có thể thuộc giờ
        nghỉ phiên, không đồng nghĩa mất nến.
      </p>
      <p className="micro">
        Setup chạy bù mang nhãn “Chạy bù”; không đặt lệnh cho tín hiệu đã bỏ lỡ.
      </p>
    </section>
  );
}

const rejectLabels: Record<string, [string, string]> = {
  h4_trend_opposed: ['Ngược xu hướng H4', 'Hướng setup đi ngược EMA50 H4'],
  no_h1_setup: ['Chưa có setup H1', 'H1 chưa hình thành cấu trúc cần thiết'],
  no_m15_trigger: ['Chưa có trigger M15', 'Setup H1 đạt nhưng nến M15 chưa kích hoạt'],
  no_trigger: ['Chưa có trigger M15', 'Setup H1 đạt nhưng M15 chưa kích hoạt'],
  h1_setup_expired: ['Setup H1 hết hạn', 'Quá thời hạn hiệu lực của cấu trúc'],
  session_filtered: ['Ngoài phiên giao dịch', 'Bộ lọc phiên từ chối thời điểm này'],
  invalid_spread: ['Spread vượt ngưỡng', 'Chi phí vào lệnh quá cao'],
  invalid_m15: ['Nến M15 không hợp lệ', 'Thiếu dữ liệu nến M15 đóng'],
  setup_reused: ['Setup đã dùng', 'Mỗi setup chỉ vào một lần'],
  no_h1_invalidation: ['Thiếu mức vô hiệu H1', 'Không xác định được SL cấu trúc'],
  sl_too_wide: ['SL quá xa', 'Vượt 3 ATR H1'],
  sl_too_tight: ['SL quá sát', 'Dưới 0.3 ATR H1'],
  no_target: ['Không có mục tiêu', 'Không có cản H1/H4 phía trước'],
  poor_rr: ['R/R kém', 'Dưới 1.5R sau chi phí'],
  h1_not_closed: ['H1 chưa đóng', 'Chờ nến H1 đóng'],
  h4_stale: ['H4 cũ', 'Dữ liệu H4 không còn mới'],
};

type Tree2 = NonNullable<
  NonNullable<ExnessExecution['learning']>['htf_ast2_shadow']
>['trees'][number];

const shortId = (id: string) => id.replace('hast2_', '').slice(0, 6);

function GenBadge({ tree }: { tree?: Tree2 }) {
  return tree ? (
    <span className={`gen-badge gen-${Math.min(tree.generation ?? 0, 3)}`}>
      G{tree.generation ?? 0}
    </span>
  ) : null;
}

function BotAnalysisBreakdown({
  strategies,
  lineage,
}: {
  strategies: NonNullable<LearnerAnalysis['strategies']>;
  lineage: Map<string, Tree2>;
}) {
  const [query, setQuery] = useState('');
  const [open, setOpen] = useState<string | null>(null);
  const rows = Object.entries(strategies);
  const confirmed = rows.filter(([, row]) => row.status === 'confirmed');
  const groups = new Map<string, string[]>();
  rows
    .filter(([, row]) => row.status !== 'confirmed')
    .forEach(([name, row]) => {
      const code = row.reason ?? 'unknown';
      groups.set(code, [...(groups.get(code) ?? []), name]);
    });
  const sorted = [...groups.entries()].sort((x, y) => y[1].length - x[1].length);
  const needle = query.trim().toLowerCase();
  const matches = needle
    ? rows.filter(
        ([name, row]) =>
          name.toLowerCase().includes(needle) ||
          (row.chart?.note ?? '').toLowerCase().includes(needle),
      )
    : [];
  const note = (row: (typeof rows)[number][1]) => row.chart?.note ?? '';
  return (
    <div className="bot-breakdown">
      <div className="bot-breakdown-head">
        <strong>
          {confirmed.length ? `${confirmed.length} cây có tín hiệu` : 'Chưa cây nào có tín hiệu'}
        </strong>
        <input
          type="search"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="Tìm ID hoặc setup…"
          aria-label="Tìm cây"
        />
      </div>
      {confirmed.map(([name, row]) => (
        <div key={name} className="bot-breakdown-hit">
          <GenBadge tree={lineage.get(name)} /> <strong>{name}</strong> · {row.side?.toUpperCase()}{' '}
          · SL {row.stop ?? '—'} · mức {row.level ?? '—'}
          <small>{note(row)}</small>
        </div>
      ))}
      {needle ? (
        <table>
          <tbody>
            {matches.slice(0, 30).map(([name, row]) => (
              <tr key={name}>
                <td>
                  <GenBadge tree={lineage.get(name)} /> {name}
                </td>
                <td>{row.status === 'confirmed' ? 'xác nhận' : 'loại'}</td>
                <td>
                  {row.status === 'confirmed'
                    ? note(row)
                    : (rejectLabels[row.reason ?? '']?.[0] ?? row.reason ?? '—')}
                </td>
              </tr>
            ))}
            {!matches.length && (
              <tr>
                <td colSpan={3}>Không có cây khớp.</td>
              </tr>
            )}
          </tbody>
        </table>
      ) : (
        sorted.map(([code, names]) => {
          const [title, hint] = rejectLabels[code] ?? [code, 'Mã lý do chưa có mô tả'];
          const expanded = open === code;
          return (
            <div key={code} className="bot-breakdown-group">
              <button
                type="button"
                aria-expanded={expanded}
                onClick={() => setOpen(expanded ? null : code)}
              >
                <span>{title}</span>
                <small>{hint}</small>
                <b>
                  {names.length}/{rows.length} cây
                </b>
                <i style={{ width: `${(names.length / rows.length) * 100}%` }} />
              </button>
              {expanded && (
                <ul>
                  {names.map((name) => (
                    <li key={name}>
                      <GenBadge tree={lineage.get(name)} /> <code>{name}</code>{' '}
                      <small>{note(strategies[name])}</small>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          );
        })
      )}
    </div>
  );
}

const familyLabels: Record<string, string> = {
  channel_breakout: 'Phá kênh',
  channel: 'Phá kênh',
  sweep: 'Quét thanh khoản',
  rsi: 'RSI đảo chiều',
  fvg: 'Khoảng trống FVG',
  squeeze: 'Nén biến động',
  pullback: 'Hồi về xu hướng',
  retest: 'Retest',
  mss: 'Đổi cấu trúc MSS',
  session: 'Phá phiên UTC',
  orb: 'Phá vùng mở phiên ORB',
  range: 'Biên range',
  vwap: 'Hồi quy VWAP',
  vsa: 'Khối lượng VSA',
  router: 'Bộ định tuyến Router',
  partial_be_h1: 'BE / Trail theo H1',
  partial_be_m15: 'BE / Trail theo M15',
  fixed_structural: 'Cố định theo cản',
};



const label = (key: string) => familyLabels[key] ?? key;

type GroupBy = 'm15_trigger' | 'h1_setup' | 'exit_policy';
const groupTitles: Record<GroupBy, string> = {
  m15_trigger: 'Trigger M15',
  h1_setup: 'Setup H1',
  exit_policy: 'Quản lệnh',
};

export function HtfLineage({
  trees,
  nextEvolutionAt,
}: {
  trees: Tree2[];
  nextEvolutionAt?: number | null;
}) {
  const byId = new Map(trees.map((tree) => [tree.id, tree]));
  const children = new Map<string, Tree2[]>();
  trees.forEach((tree) =>
    (tree.parents ?? []).forEach((id) => children.set(id, [...(children.get(id) ?? []), tree])),
  );
  const maxGen = Math.max(0, ...trees.map((tree) => tree.generation ?? 0));
  const [query, setQuery] = useState('');
  const [filter, setFilter] = useState<'all' | 'signal' | 'eligible' | 'child'>('all');
  const [groupBy, setGroupBy] = useState<GroupBy>('m15_trigger');
  const [openGroups, setOpenGroups] = useState<Set<string>>(new Set());
  const [selected, setSelected] = useState<string | null>(null);

  const needle = query.trim().toLowerCase();
  const visible = trees.filter((tree) => {
    if (filter === 'signal' && !tree.signals) return false;
    if (filter === 'eligible' && !tree.eligible) return false;
    if (filter === 'child' && !(tree.generation ?? 0)) return false;
    if (!needle) return true;
    return [
      tree.id,
      tree.h1_setup,
      tree.m15_trigger,
      tree.exit_policy,
      label(tree.h1_setup),
      label(tree.m15_trigger),
      label(tree.exit_policy),
    ]
      .join(' ')
      .toLowerCase()
      .includes(needle);
  });

  const groups = new Map<string, Tree2[]>();
  visible.forEach((tree) =>
    groups.set(tree[groupBy], [...(groups.get(tree[groupBy]) ?? []), tree]),
  );
  const ordered = [...groups.entries()].sort((x, y) => y[1].length - x[1].length);
  const detail = selected ? byId.get(selected) : undefined;

  const toggle = (key: string) =>
    setOpenGroups((prev) => {
      const next = new Set(prev);
      if (!next.delete(key)) next.add(key);
      return next;
    });

  const sum = (rows: Tree2[], field: 'signals' | 'resolved') =>
    rows.reduce((total, row) => total + row[field], 0);

  const chip = (id: string, key = id) => {
    const found = byId.get(id);
    return (
      <button
        type="button"
        key={key}
        className={`parent-chip ${found ? '' : 'is-gone'}`}
        disabled={!found}
        title={found ? `Xem cây ${id}` : 'Cây đã nghỉ, không còn trong quần thể'}
        onClick={() => found && setSelected(id)}
      >
        {found ? `G${found.generation ?? 0} · ` : ''}
        {shortId(id)}
        {!found && ' (đã nghỉ)'}
      </button>
    );
  };

  const options: [typeof filter, string][] = [
    ['all', `Tất cả (${trees.length})`],
    ['signal', `Có tín hiệu (${trees.filter((t) => t.signals).length})`],
    ['eligible', `Đủ chuẩn (${trees.filter((t) => t.eligible).length})`],
    ['child', `Cây con (${trees.filter((t) => (t.generation ?? 0) > 0).length})`],
  ];

  return (
    <div className="lineage">
      <div className="lineage-gens" aria-label="Thế hệ">
        {Array.from({ length: maxGen + 1 }, (_, gen) => {
          const count = trees.filter((tree) => (tree.generation ?? 0) === gen).length;
          return (
            <div key={gen} className={`lineage-gen-card ${count ? '' : 'is-empty'}`}>
              <span className={`gen-badge gen-${Math.min(gen, 3)}`}>G{gen}</span>
              <strong>{count}</strong>
              <small>{gen === 0 ? 'hạt giống' : 'cây con'}</small>
            </div>
          );
        })}
        {maxGen === 0 && (
          <p className="micro lineage-hint">
            Chưa có cây con. Cần ít nhất 2 cây bố mẹ đủ 40 mẫu đã đóng trên 10 ngày
            {nextEvolutionAt
              ? ` · sớm nhất ${new Date(nextEvolutionAt * 1000).toISOString().slice(0, 10)} UTC`
              : ''}
            .
          </p>
        )}
      </div>

      <div className="lineage-toolbar">
        <input
          type="search"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="Tìm ID, setup H1, trigger M15…"
          aria-label="Tìm cây"
        />
        <div className="lineage-seg" role="group" aria-label="Lọc">
          {options.map(([key, text]) => (
            <button
              type="button"
              key={key}
              aria-pressed={filter === key}
              onClick={() => setFilter(key)}
            >
              {text}
            </button>
          ))}
        </div>
        <div className="lineage-seg" role="group" aria-label="Nhóm theo">
          {(Object.keys(groupTitles) as GroupBy[]).map((key) => (
            <button
              type="button"
              key={key}
              aria-pressed={groupBy === key}
              onClick={() => {
                setGroupBy(key);
                setOpenGroups(new Set());
              }}
            >
              {groupTitles[key]}
            </button>
          ))}
        </div>
      </div>

      {detail && (
        <div className="lineage-detail" role="region" aria-label="Chi tiết cây">
          <div className="lineage-detail-head">
            <span className={`gen-badge gen-${Math.min(detail.generation ?? 0, 3)}`}>
              G{detail.generation ?? 0}
            </span>
            <code className="lineage-detail-id">{detail.id}</code>
            {detail.eligible ? (
              <span className="gen-badge gen-ok">✓ Đủ chuẩn đặt lệnh</span>
            ) : (
              <span className="lineage-status-tag">Chờ gom mẫu ({detail.resolved}/40)</span>
            )}
            <button type="button" className="secondary lineage-close-btn" onClick={() => setSelected(null)}>
              ✕ Đóng
            </button>
          </div>

          <div className="lineage-path">
            <span className="lineage-path-label">Dòng dõi:</span>
            <div className="lineage-path-flow">
              <span className="lineage-path-node">
                <small>Bố mẹ</small>
                {detail.parents?.length ? (
                  detail.parents.map((id) => chip(id, `p-${id}`))
                ) : (
                  <span className="muted">Gốc (khởi nguyên)</span>
                )}
              </span>
              <span className="lineage-path-arrow">→</span>
              <span className="lineage-path-node is-self">
                <small>Cây này</small>
                <strong>G{detail.generation ?? 0} · {shortId(detail.id)}</strong>
              </span>
              <span className="lineage-path-arrow">→</span>
              <span className="lineage-path-node">
                <small>Thế hệ con</small>
                {children.get(detail.id)?.length ? (
                  children.get(detail.id)!.map((child) => chip(child.id, `c-${child.id}`))
                ) : (
                  <span className="muted">Chưa sinh con</span>
                )}
              </span>
            </div>
          </div>

          <div className="lineage-steps-grid">
            <div className="lineage-step-card">
              <span className="step-num">1</span>
              <div className="step-content">
                <strong>Bối cảnh H4</strong>
                <span className="step-tag">Xu hướng EMA50</span>
                <p>Nến H4 đóng thuận chiều EMA50 (trên EMA50 chỉ Buy, dưới EMA50 chỉ Sell). Ngược trend thì bỏ qua.</p>
              </div>
            </div>

            <div className="lineage-step-card">
              <span className="step-num">2</span>
              <div className="step-content">
                <strong>Setup H1</strong>
                <span className="step-tag">{label(detail.h1_setup)}</span>
                <p>{forexSetupGuides[detail.h1_setup] ?? 'Cấu trúc hình thành trên khung H1 đã đóng.'} SL đặt tại mức vô hiệu cấu trúc H1 + 0.25 ATR buffer (hiệu lực 4h).</p>
              </div>
            </div>

            <div className="lineage-step-card">
              <span className="step-num">3</span>
              <div className="step-content">
                <strong>Trigger M15</strong>
                <span className="step-tag">{label(detail.m15_trigger)}</span>
                <p>{forexTriggerGuides[detail.m15_trigger] ?? 'Xác nhận thời điểm vào lệnh tại nến M15 đóng.'} M15 chỉ trigger, không quyết định SL/TP.</p>
              </div>
            </div>

            <div className="lineage-step-card">
              <span className="step-num">4</span>
              <div className="step-content">
                <strong>Quản lý lệnh</strong>
                <span className="step-tag">{label(detail.exit_policy)}</span>
                <p>{forexExitGuides[detail.exit_policy] ?? 'Quản lý rủi ro theo cấu trúc HTF.'} TP runner bám theo cản H1/H4 gần nhất (R/R tối thiểu 1.5R).</p>
              </div>
            </div>
          </div>

          <div className="lineage-stats-strip">
            <div className="lineage-stat-box">
              <span>Tín hiệu</span>
              <strong>{detail.signals}</strong>
            </div>
            <div className="lineage-stat-box">
              <span>Đang chạy</span>
              <strong>{detail.pending}</strong>
            </div>
            <div className="lineage-stat-box">
              <span>Đã đóng</span>
              <strong>{detail.resolved}</strong>
            </div>
            <div className="lineage-stat-box">
              <span>Bị loại</span>
              <strong>{detail.excluded}</strong>
            </div>
            <div className="lineage-stat-box">
              <span>R trung bình</span>
              <strong className={detail.mean_r == null ? '' : detail.mean_r >= 0 ? 'positive' : 'negative'}>
                {detail.mean_r == null ? '—' : `${detail.mean_r >= 0 ? '+' : ''}${detail.mean_r.toFixed(2)}R`}
              </strong>
            </div>
            <div className="lineage-stat-box">
              <span>Cận dưới 95%</span>
              <strong className={detail.lower_r == null ? '' : detail.lower_r >= 0 ? 'positive' : 'negative'}>
                {detail.lower_r == null ? '—' : `${detail.lower_r >= 0 ? '+' : ''}${detail.lower_r.toFixed(2)}R`}
              </strong>
            </div>
          </div>
        </div>
      )}

      {!ordered.length && <p className="micro">Không có cây khớp bộ lọc.</p>}

      {ordered.map(([key, rows]) => {
        const open = openGroups.has(key) || !!needle;
        return (
          <div key={key} className="lineage-group">
            <button type="button" aria-expanded={open} onClick={() => toggle(key)}>
              <span className="lineage-caret">{open ? '▾' : '▸'}</span>
              <span className="lineage-group-title">{label(key)}</span>
              <span className="lineage-count-pill">{rows.length} cây</span>
              <span className="lineage-group-stats">
                {sum(rows, 'signals')} tín hiệu · {sum(rows, 'resolved')} đã đóng
              </span>
            </button>
            {open && (
              <ul className="lineage-rows-list">
                {rows.map((row) => (
                  <li key={row.id}>
                    <button
                      type="button"
                      className={`lineage-row-btn ${selected === row.id ? 'is-selected' : ''}`}
                      onClick={() => setSelected(row.id)}
                    >
                      <span className={`gen-badge gen-${Math.min(row.generation ?? 0, 3)}`}>
                        G{row.generation ?? 0}
                      </span>
                      <code className="lineage-row-id">{shortId(row.id)}</code>

                      <div className="lineage-tags-inline">
                        <span className="lineage-tag tag-h1">H1 {label(row.h1_setup)}</span>
                        <span className="lineage-tag tag-m15">M15 {label(row.m15_trigger)}</span>
                        <span className="lineage-tag tag-exit">{label(row.exit_policy)}</span>
                      </div>

                      <span className="lineage-row-meta">
                        {row.parents?.length ? (
                          <span className="lineage-parents-brief">từ {row.parents.map(shortId).join(' × ')}</span>
                        ) : (
                          <span className="muted">hạt giống gốc</span>
                        )}
                        {children.get(row.id)?.length ? (
                          <span className="lineage-child-badge"> · {children.get(row.id)!.length} con</span>
                        ) : null}
                      </span>

                      <span className="lineage-row-metric font-mono">
                        {row.resolved ? (
                          <span className={row.mean_r != null && row.mean_r >= 0 ? 'positive' : 'negative'}>
                            {row.mean_r != null && row.mean_r >= 0 ? '+' : ''}{row.mean_r?.toFixed(2)}R
                          </span>
                        ) : (
                          <span className="muted">{row.signals} tín hiệu</span>
                        )}
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        );
      })}
    </div>
  );
}
export function ForexStrategyPage({
  dashboard,
  disconnected,
  executions = [],
  selectedPosition,
  executionOnly = false,
  includeReplay = true,
  onClosePosition,
  onSelectPosition,
}: {
  dashboard: ForexDashboard;
  disconnected: boolean;
  executions?: BrokerPosition[];
  selectedPosition?: BrokerPosition;
  executionOnly?: boolean;
  includeReplay?: boolean;
  onClosePosition?: (position: BrokerPosition) => void;
  onSelectPosition?: (id: string) => void;
}) {
  const [candles, setCandles] = useState<ForexCandle[]>([]);
  const [analysis, setAnalysis] = useState<LearnerAnalysis | null>(null);
  const [htf2Shadow, setHtf2Shadow] =
    useState<NonNullable<ExnessExecution['learning']>['htf_ast2_shadow']>();
  useEffect(() => {
    const controller = new AbortController();
    const load = async () => {
      try {
        const res = await fetch('/api/workspaces/exness_forex/learning/analysis', {
          signal: controller.signal,
        });
        const body = (await res.json()) as (LearnerAnalysis | { status: string }) & {
          htf_ast2_shadow?: typeof htf2Shadow;
        };
        setHtf2Shadow(res.ok ? body.htf_ast2_shadow : undefined);
        setAnalysis(res.ok && 'levels' in body ? body : null);
      } catch {
        if (!controller.signal.aborted) {
          setAnalysis(null);
          setHtf2Shadow(undefined);
        }
      }
    };
    void load();
    const timer = window.setInterval(() => void load(), 15000);
    return () => {
      controller.abort();
      window.clearInterval(timer);
    };
  }, []);
  const [loading, setLoading] = useState(false);
  const [frame, setFrame] = useState('15m');
  const [side, setSide] = useState('bid');
  const [shownStrategies, setShownStrategies] = useState<string[]>([]); // default: draw nothing
  const [view, setView] = useState<'mtf' | 'trend' | 'range' | 'router'>('mtf');
  const historyBefore = selectedPosition?.status === 'closed' ? selectedPosition.close_time : null;
  const [loadingOlder, setLoadingOlder] = useState(false);
  const [hasMore, setHasMore] = useState(false);
  const historyActions = useRef({ older: () => {}, refresh: () => {} });
  const [chartError, setChartError] = useState('');
  const [quoteState, setQuoteState] = useState<QuoteState>({
    status: 'connecting',
    quote: null,
  });
  const [quoteHeartbeat, setQuoteHeartbeat] = useState(0);
  const [clock, setClock] = useState(Date.now());
  const quoteLive =
    quoteState.status === 'live' &&
    clock - quoteHeartbeat < 5000 &&
    quoteState.quote !== null &&
    clock / 1000 - quoteState.quote.time <= 15;

  const liveCandles = useMemo(
    () =>
      quoteLive && !(executionOnly && selectedPosition?.status === 'closed')
        ? (quoteState.candles?.[frame] ?? []).map((row) => ({
            time: row.time,
            ...(side === 'bid' ? row.bid : row.ask),
          }))
        : [],
    [quoteLive, executionOnly, selectedPosition?.status, quoteState.candles, frame, side],
  );

  useEffect(() => {
    const stream = new EventSource('/api/workspaces/exness_forex/quotes/stream');
    const timer = window.setInterval(() => setClock(Date.now()), 1000);
    stream.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        if (data.symbol !== 'XAUUSD' || typeof data.status !== 'string') return;
        const quote = data.quote;
        if (
          quote !== null &&
          (!Number.isFinite(quote?.bid) ||
            !Number.isFinite(quote?.ask) ||
            !Number.isFinite(quote?.time) ||
            quote.bid <= 0 ||
            quote.ask < quote.bid)
        )
          return;
        const candles = data.candles;
        if (!candles || typeof candles !== 'object' || Array.isArray(candles)) return;
        for (const [frame, seconds] of Object.entries({
          '15m': 900,
          '1h': 3600,
          '4h': 14400,
        })) {
          const rows = candles[frame];
          if (!Array.isArray(rows) || rows.length > 32) return;
          for (let index = 0; index < rows.length; index++) {
            const row = rows[index];
            if (
              !Number.isSafeInteger(row?.time) ||
              row.time < 0 ||
              row.time % seconds !== 0 ||
              typeof row.partial !== 'boolean' ||
              (index > 0 && row.time <= rows[index - 1].time)
            )
              return;
            for (const side of ['bid', 'ask']) {
              const bar = row[side];
              if (
                !bar ||
                !['open', 'high', 'low', 'close'].every(
                  (field) => Number.isFinite(bar[field]) && bar[field] > 0,
                ) ||
                bar.low > Math.min(bar.open, bar.close) ||
                bar.high < Math.max(bar.open, bar.close)
              )
                return;
            }
          }
        }
        setQuoteState({ status: data.status, quote, candles });
        setQuoteHeartbeat(Date.now());
      } catch {
        setQuoteState({ status: 'unavailable', quote: null });
      }
    };
    stream.onerror = () => setQuoteState({ status: 'disconnected', quote: null });
    return () => {
      stream.close();
      window.clearInterval(timer);
    };
  }, []);
  const shadow =
    view === 'mtf' ? null : dashboard.paper.portfolios?.find((row) => row.channel === view);
  const shadowObs = shadow?.observation ?? null;
  const snap = view === 'mtf' ? dashboard.observation.snapshot : shadowObs;
  const currentStatus =
    view === 'mtf' ? dashboard.observation.status : (shadowObs?.status ?? 'not_started');
  const hasRange =
    snap?.range_high != null &&
    snap?.range_low != null &&
    snap.range_start !== undefined &&
    snap.range_end !== undefined;
  const fullSnapshot: ObservationChartSnapshot | null =
    (hasRange || view === 'trend') && snap
      ? {
          strategy:
            view === 'mtf'
              ? 'exness_mtf_v1'
              : view === 'trend'
                ? 'trend_pullback'
                : view === 'range'
                  ? 'range_rejection'
                  : 'regime_router',
          parameters: snap.parameters ?? {
            lookback_1h: 20,
            ema_4h: 50,
            wait_hours: 12,
            target_r: 2,
          },
          candle_time: snap.candle_time ?? 0,
          closed_4h_time: snap.closed_4h_time ?? 0,
          status: snap.status ?? 'not_started',
          reason: snap.reason ?? '',
          range_start: snap.range_start ?? 0,
          range_end: snap.range_end ?? 0,
          range_high: snap.range_high ?? 0,
          range_low: snap.range_low ?? 0,
          trend: snap.trend ?? null,
          side: (snap.side as 'long' | 'short' | null) ?? null,
          level: snap.level ?? null,
          breakout_time: snap.breakout_time ?? null,
          touch_time: snap.touch_time ?? null,
          confirm_time: snap.confirm_time ?? null,
          expires_at: snap.expires_at ?? null,
          stop: snap.stop ?? null,
          target: snap.target ?? null,
          provenance: snap.provenance as 'live' | 'catch_up' | 'historical' | undefined,
        }
      : null;

  useEffect(() => {
    let active = true;
    let busy = false;
    let oldest: number | undefined;
    let more = false;
    const controller = new AbortController();
    setLoading(true);
    setLoadingOlder(false);
    setHasMore(false);
    setCandles([]);
    setChartError('');
    const load = async (older = false) => {
      if (busy || (older && (!more || oldest === undefined))) return;
      busy = true;
      if (older) setLoadingOlder(true);
      setChartError('');
      try {
        const res = await fetch(
          `/api/workspaces/exness_forex/candles?timeframe=${frame}&price_type=${side}&limit=500${older ? `&before=${oldest}` : historyBefore ? `&before=${historyBefore + 18000}` : ''}`,
          {
            signal: AbortSignal.any([controller.signal, AbortSignal.timeout(10000)]),
          },
        );
        if (!res.ok) throw new Error('Cannot load Exness candles');
        const data = await res.json();
        if (
          data.workspace?.id !== 'exness_forex' ||
          data.timeframe !== frame ||
          data.price_type !== side
        )
          throw new Error('Wrong candle source');
        if (!active) return;
        const incoming: ForexCandle[] = data.candles ?? [];
        if (older || oldest === undefined) {
          oldest = incoming[0]?.time ?? oldest;
          more = data.has_more;
          setHasMore(more);
        }
        setCandles((current) => {
          const merged = new Map(current.map((bar) => [bar.time, bar]));
          incoming.forEach((bar) => merged.set(bar.time, bar));
          return [...merged.values()].sort((left, right) => left.time - right.time);
        });
      } catch {
        if (active)
          setChartError('Không tải được nến Exness. Bấm tải lại; không thay bằng dữ liệu MEXC.');
      } finally {
        busy = false;
        if (active) {
          setLoading(false);
          setLoadingOlder(false);
        }
      }
    };
    historyActions.current = {
      older: () => void load(true),
      refresh: () => void load(),
    };
    void load();
    return () => {
      active = false;
      controller.abort();
    };
  }, [frame, side, historyBefore]);

  useEffect(() => {
    historyActions.current.refresh();
  }, [dashboard.market.last_sync]);

  const pnlAt = (position: BrokerPosition, price: number) => {
    const contract = Number(dashboard.paper?.calibration?.profile?.contract_size);
    if (
      (position.side !== 'buy' && position.side !== 'sell') ||
      ![contract, position.volume, position.open_price, price].every(
        (value) => typeof value === 'number' && Number.isFinite(value) && value > 0,
      )
    )
      return null;
    const move =
      position.side === 'buy' ? price - position.open_price! : position.open_price! - price;
    return move * position.volume! * contract;
  };
  const pnlOf = (position: BrokerPosition) => {
    const quote = quoteState.quote;
    return quoteLive && quote
      ? pnlAt(position, position.side === 'buy' ? quote.bid : quote.ask)
      : null;
  };

  return (
    <section
      className={`panel prose mtf-observation ${executionOnly ? 'broker-chart-terminal' : ''}`}
    >
      {executionOnly ? (
        <header className="broker-market-header">
          <div className="broker-instrument">
            <span className="broker-symbol">XAU</span>
            <div>
              <h2>
                Gold / US Dollar <span>XAUUSD</span>
              </h2>
              <p>
                Exness Demo <span>·</span> Giá gốc sàn <span>·</span> UTC
              </p>
            </div>
          </div>
          <span className={`broker-stream-state ${quoteLive ? 'is-live' : ''}`} role="status">
            <span aria-hidden="true" />
            {quoteLive ? 'Tick trực tiếp' : 'Stream gián đoạn'}
          </span>
        </header>
      ) : (
        <div className="section-head">
          <div>
            <h2>
              {executionOnly
                ? 'Chart khớp lệnh sàn'
                : view === 'mtf'
                  ? 'Breakout + Retest'
                  : view === 'trend'
                    ? 'Trend Pullback'
                    : 'Range Rejection'}{' '}
              · XAUUSD
            </h2>
            <p>
              {executionOnly
                ? 'Dấu BUY/SELL/EXIT lấy từ deal Exness. Chọn vị thế để xem giá khớp, SL và TP sàn.'
                : view === 'mtf'
                  ? '4h EMA50 xu hướng → 1h breakout vùng 20 nến → 15m retest và nến xác nhận giữ vùng.'
                  : view === 'trend'
                    ? 'H1 EMA20/50 xác định xu hướng → M15 chạm EMA20 → nến đóng xác nhận cùng chiều.'
                    : 'Range 20 nến H1 đi ngang → M15 chạm biên → nến đóng từ chối vào trong range.'}
            </p>
          </div>
          <span className="pill amber">
            <Eye size={12} style={{ marginRight: 4, verticalAlign: -1 }} />
            {executionOnly
              ? 'Lệnh demo trên sàn · Bid/Ask Exness'
              : 'Paper tự động · nến bid gốc Exness'}
          </span>
        </div>
      )}
      {!executionOnly && (
        <div className="forex-paper-switch" role="group" aria-label="Chọn chiến lược">
          <button
            className="secondary"
            aria-pressed={view === 'mtf'}
            onClick={() => setView('mtf')}
          >
            Breakout + Retest
          </button>
          <button
            className="secondary"
            aria-pressed={view === 'trend'}
            onClick={() => setView('trend')}
          >
            Trend Pullback
          </button>
          <button
            className="secondary"
            aria-pressed={view === 'range'}
            onClick={() => setView('range')}
          >
            Range Rejection
          </button>
          <button
            className="secondary"
            aria-pressed={view === 'router'}
            onClick={() => setView('router')}
          >
            Regime Router
          </button>
        </div>
      )}
      {!executionOnly && view !== 'mtf' && (
        <p role="status">
          Ví paper{' '}
          {shadow?.status === 'tracking'
            ? 'đang theo dõi tự động'
            : shadow?.status === 'blocked'
              ? 'BỊ CHẶN do gap chưa xác minh'
              : 'chưa khởi tạo'}
          : {shadow?.confirmed_count ?? 0} setup · {shadow?.accepted_count ?? 0} được duyệt ·{' '}
          {shadow?.open_trades.length ?? 0} lệnh chờ/mở · {shadow?.resolved_count ?? 0} lệnh đóng ·
          số dư {shadow?.balance.toFixed(2) ?? '—'} USD. Khớp ở open M15 kế tiếp theo Bid/Ask, SL
          theo cấu trúc, TP 2R. Mô phỏng cục bộ, không gửi lệnh Demo.
        </p>
      )}
      <div className="mtf-source-strip">
        <span>Nến M15/H1/H4 Exness Demo</span>
        {snap?.['available_at'] && (
          <span>Snapshot {new Date(snap.available_at * 1000).toISOString().slice(0, 16)} UTC</span>
        )}
        {disconnected && <span className="negative">Mất kết nối API · dữ liệu lưu gần nhất</span>}
      </div>
      {(!dashboard.market.worker_online || currentStatus === 'data_paused') && (
        <p role="status" className="negative">
          Worker hoặc dữ liệu chưa sẵn sàng. Chart và setup là dữ liệu đã lưu, không phải tín hiệu
          mới.
        </p>
      )}

      {!executionOnly && (
        <div className="research-metrics">
          <div>
            <span className="muted">{view === 'mtf' ? 'Xu hướng 4h' : 'Xu hướng H1'}</span>
            <strong>
              {snap?.trend === 'up'
                ? 'TĂNG (chỉ Long)'
                : snap?.trend === 'down'
                  ? 'GIẢM (chỉ Short)'
                  : snap?.trend === 'sideway'
                    ? 'ĐI NGANG'
                    : 'CHƯA RÕ'}
            </strong>
          </div>
          <div>
            <span className="muted">Trạng thái MTF</span>
            <strong>{observationStatusLabels[currentStatus] ?? currentStatus}</strong>
          </div>
          <div>
            <span className="muted">{view === 'trend' ? 'EMA20 M15' : 'Vùng 20 nến 1h'}</span>
            <strong>
              {view === 'trend' && snap?.level != null
                ? formatPrice(snap.level)
                : snap?.range_low && snap?.range_high
                  ? `${formatPrice(snap.range_low)} – ${formatPrice(snap.range_high)}`
                  : '—'}
            </strong>
          </div>
          <div>
            <span className="muted">SL / TP tham chiếu</span>
            <strong>
              {snap?.stop && snap?.target
                ? `${formatPrice(snap.stop)} / ${formatPrice(snap.target)}`
                : 'Chưa có'}
            </strong>
          </div>
        </div>
      )}

      {!executionOnly && (
        <div style={{ marginTop: 12 }}>
          <p>
            <strong>Chi tiết quan sát:</strong>{' '}
            {view === 'mtf'
              ? dashboard.observation.message
              : (shadowObs?.reason ?? 'Chờ worker khởi tạo')}
            {snap?.['provenance'] && (
              <span className="pill" style={{ marginLeft: 8 }}>
                {snap.provenance}
              </span>
            )}
          </p>
        </div>
      )}

      {loading && <p className="micro">Đang tải nến {frame.toUpperCase()} Exness...</p>}
      {executionOnly ? (
        <div className="broker-chart-toolbar">
          <div className="broker-segments" role="group" aria-label="Khung thời gian chart">
            {['15m', '1h', '4h'].map((value) => (
              <button
                type="button"
                key={value}
                aria-pressed={frame === value}
                onClick={() => setFrame(value)}
              >
                {value.toUpperCase()}
              </button>
            ))}
          </div>
          <span className="broker-toolbar-divider" aria-hidden="true" />
          <div className="broker-segments" role="group" aria-label="Phía giá chart">
            {['bid', 'ask'].map((value) => (
              <button
                type="button"
                key={value}
                aria-pressed={side === value}
                onClick={() => setSide(value)}
              >
                {value.toUpperCase()}
              </button>
            ))}
          </div>
          {frame === '15m' && (
            <StrategyOverlayMenu
              analysis={analysis}
              selected={shownStrategies}
              onChange={setShownStrategies}
            />
          )}
          <div className="broker-quote-pair" aria-label="Giá Exness trực tiếp">
            <span>
              BID <strong>{quoteLive ? formatPrice(quoteState.quote!.bid) : '—'}</strong>
            </span>
            <span>
              ASK <strong>{quoteLive ? formatPrice(quoteState.quote!.ask) : '—'}</strong>
            </span>
            <span>
              SPREAD{' '}
              <strong>
                {quoteLive ? formatPrice(quoteState.quote!.ask - quoteState.quote!.bid) : '—'}
              </strong>
            </span>
          </div>
          {(hasMore || chartError) && (
            <button
              className="secondary broker-history-button"
              type="button"
              disabled={loading || loadingOlder}
              onClick={() =>
                chartError ? historyActions.current.refresh() : historyActions.current.older()
              }
            >
              {chartError ? 'Thử tải lại' : loadingOlder ? 'Đang tải…' : 'Thêm lịch sử'}
            </button>
          )}
        </div>
      ) : (
        <div className="mtf-toolbar">
          <div className="mtf-seg" role="group" aria-label="Khung nến">
            {[
              ['15m', '15m · Xác nhận'],
              ['1h', '1h · Breakout'],
              ['4h', '4h · Xu hướng'],
            ].map(([value, label]) => (
              <button
                type="button"
                key={value}
                aria-pressed={frame === value}
                onClick={() => setFrame(value)}
              >
                {label}
              </button>
            ))}
          </div>
          <div className="mtf-seg" role="group" aria-label="Phía giá">
            {['bid', 'ask'].map((value) => (
              <button
                type="button"
                key={value}
                aria-pressed={side === value}
                onClick={() => setSide(value)}
              >
                {value.toUpperCase()}
              </button>
            ))}
          </div>
          <span role="status" className="mtf-toolbar-note">
            {candles.length.toLocaleString()} nến ·{' '}
            {loadingOlder
              ? 'Đang tải lịch sử...'
              : hasMore
                ? 'Kéo trái để tải thêm'
                : 'Hết lịch sử lưu trong DB'}
          </span>
          {(hasMore || chartError) && (
            <button
              type="button"
              className="mtf-toolbar-action"
              disabled={loading || loadingOlder}
              onClick={() =>
                hasMore ? historyActions.current.older() : historyActions.current.refresh()
              }
            >
              {chartError ? 'Tải lại' : 'Tải nến cũ'}
            </button>
          )}
        </div>
      )}
      {chartError && <p role="alert">{chartError}</p>}
      {!executionOnly && (
        <div className="mtf-quote-strip" aria-label="Giá Exness trực tiếp">
          <span className={`mtf-quote-state ${quoteLive ? 'is-live' : ''}`}>
            {quoteStatusLabels[
              quoteState.status === 'live' && !quoteLive ? 'stale' : quoteState.status
            ] ?? 'Stream chưa sẵn sàng'}
          </span>
          <span>
            BID <strong>{quoteLive ? formatPrice(quoteState.quote!.bid) : '—'}</strong>
          </span>
          <span>
            ASK <strong>{quoteLive ? formatPrice(quoteState.quote!.ask) : '—'}</strong>
          </span>
          <span>
            SPREAD{' '}
            <strong>
              {quoteLive ? formatPrice(quoteState.quote!.ask - quoteState.quote!.bid) : '—'}
            </strong>
          </span>
        </div>
      )}
      {!executionOnly && (
        <p className="micro">
          Nến đang chạy ghép từ tick {side.toUpperCase()} Exness, cập nhật mỗi giây. Nến live chỉ để
          theo dõi; nến đóng từ REST thay thế khi đồng bộ, MTF và paper không đổi.{' '}
          {!quoteLive
            ? 'Stream tạm dừng; không hiển thị nến live cũ.'
            : quoteState.candles?.[frame]?.at(-1)?.partial
              ? 'Nến hiện tại thiếu tick đầu nến hoặc có gián đoạn; OHLC tạm thời.'
              : 'OHLC từ tick đã nhận, có thể khác nến chính thức.'}
          {quoteState.quote &&
            ` Tick gần nhất: ${new Date(quoteState.quote.time * 1000).toISOString()}`}
        </p>
      )}
      {!loading && !chartError && candles.length === 0 && (
        <p role="status">Chưa có nến đã đóng cho lựa chọn này.</p>
      )}
      {executionOnly && executions.length > 0 && (
        <div className="broker-position-picker" role="group" aria-label="Chọn vị thế đang mở">
          <span className="broker-position-count">{executions.length} vị thế mở</span>
          {executions.map((position) => {
            const selected = position.position_id === selectedPosition?.position_id;
            const pnl = pnlOf(position);
            return (
              <button
                type="button"
                key={position.position_id}
                className="broker-position-chip"
                aria-pressed={selected}
                onClick={() => onSelectPosition?.(position.position_id)}
              >
                <span className={position.side === 'buy' ? 'positive' : 'negative'}>
                  {position.side?.toUpperCase() ?? '—'}
                </span>
                <span>#{position.position_id}</span>
                <span>
                  {position.volume ?? '—'} lot{' '}
                  {position.role === 'tp1' ? '· TP1' : position.role === 'runner' ? '· TP2' : ''}
                </span>
                <strong className={pnl == null ? '' : pnl >= 0 ? 'positive' : 'negative'}>
                  {pnl == null ? '— USD' : `${pnl >= 0 ? '+' : '−'}${Math.abs(pnl).toFixed(2)} USD`}
                </strong>
              </button>
            );
          })}
        </div>
      )}
      {executionOnly && selectedPosition && (
        <div className="broker-position-strip" aria-label="Vị thế đang xem">
          <span
            className={`broker-direction ${selectedPosition.side === 'buy' ? 'positive' : 'negative'}`}
          >
            {selectedPosition.side?.toUpperCase() ?? '—'}{' '}
            <small>
              {selectedPosition.volume} lot · #{selectedPosition.position_id}
            </small>
          </span>
          <span>
            GIÁ VÀO
            <strong>
              {selectedPosition.open_price != null ? formatPrice(selectedPosition.open_price) : '—'}
            </strong>
          </span>
          <span className="broker-stop">
            STOP LOSS
            <strong>
              {selectedPosition.stop != null ? formatPrice(selectedPosition.stop) : '—'}
            </strong>
          </span>
          <span className="broker-target">
            TAKE PROFIT
            <strong>
              {selectedPosition.target != null ? formatPrice(selectedPosition.target) : '—'}
            </strong>
          </span>
          <span>
            {selectedPosition.status === 'closed' ? 'PROFIT SÀN' : 'TRẠNG THÁI'}
            <strong>
              {selectedPosition.status === 'closed'
                ? `${selectedPosition.pnl?.toFixed(2) ?? '—'} USD`
                : selectedPosition.status === 'open'
                  ? [
                      selectedPosition.role === 'tp1' ? 'TP1 · 1R' : 'TP2 · 2R',
                      selectedPosition.partial_taken ? 'Đã chốt TP1' : '',
                      selectedPosition.management_status === 'confirmed'
                        ? selectedPosition.management_stage === 'trailing'
                          ? 'Đã dời trailing SL'
                          : 'Đã dời SL về entry'
                        : selectedPosition.management_status === 'submitted'
                          ? 'Đang xác minh dời SL'
                          : '',
                    ]
                      .filter(Boolean)
                      .join(' · ')
                  : 'Chưa đủ lịch sử'}
            </strong>
          </span>
        </div>
      )}
      <MtfObservationChart
        key={`${frame}-${side}`}
        onLoadOlder={hasMore && !chartError ? () => historyActions.current.older() : undefined}
        candles={candles}
        liveCandles={liveCandles}
        snapshot={!executionOnly && frame === '15m' && side === 'bid' ? fullSnapshot : null}
        analysis={executionOnly && frame === '15m' ? analysis : null}
        shown={shownStrategies}
        executions={executions}
        dark={executionOnly}
        onClosePosition={onClosePosition}
        onSelectPosition={onSelectPosition}
        pnlAt={pnlAt}
        pnlOf={pnlOf}
        selectedPositionId={selectedPosition?.position_id}
        frameSeconds={frame === '4h' ? 14400 : frame === '1h' ? 3600 : 900}
        trendLabel={view === 'mtf' ? 'Xu hướng 4h' : 'Xu hướng H1'}
        levelLabel={view === 'trend' ? 'EMA20 M15' : 'Mức breakout'}
        livePrice={
          quoteLive && !(executionOnly && selectedPosition?.status === 'closed')
            ? {
                price: side === 'bid' ? quoteState.quote!.bid : quoteState.quote!.ask,
                label: `${side.toUpperCase()} LIVE`,
              }
            : null
        }
        title={`Exness XAUUSD · ${executionOnly ? 'FILL SÀN DEMO' : view} · ${side.toUpperCase()} ${frame} · USD/oz · UTC`}
      />
      {executionOnly && (
        <div className="bot-footer">
          <div className="bot-footer-bar">
            <span>
              {loading
                ? 'Đang tải nến…'
                : `${candles.length.toLocaleString()} nến ${frame.toUpperCase()}`}
              {' · '}
              {hasMore ? 'Kéo trái để tải thêm' : 'Hết lịch sử đã lưu'}
            </span>
            <span className={quoteLive && selectedPosition?.status !== 'closed' ? 'is-live' : ''}>
              {selectedPosition?.status === 'closed'
                ? 'Xem lịch sử vị thế · không ghép tick'
                : quoteLive
                  ? 'OHLC tạm thời từ tick · REST xác nhận khi đóng nến'
                  : 'Tick tạm dừng · không dùng giá cũ'}
            </span>
          </div>
          {analysis && (
            <details className="bot-analysis">
              <summary>
                <strong>Bot quan sát</strong>
                <span>
                  nến {new Date(analysis.candle_time * 1000).toISOString().slice(11, 16)} UTC
                </span>
                <span className={analysis.selected_signal ? 'is-pick' : ''}>
                  {analysis.selected_signal
                    ? `Chọn ${analysis.selected_signal.strategy} ${analysis.selected_signal.side} · dự kiến, chưa vào lệnh`
                    : `Chưa chọn tín hiệu · ${analysis.policy_mode}`}
                </span>
                <span className="bot-analysis-count">
                  {
                    Object.values(analysis.strategies).filter((row) => row.status === 'confirmed')
                      .length
                  }{' '}
                  xác nhận ·{' '}
                  {
                    Object.values(analysis.strategies).filter((row) => row.status !== 'confirmed')
                      .length
                  }{' '}
                  cây bị loại
                </span>
              </summary>
              <BotAnalysisBreakdown
                strategies={analysis.strategies}
                lineage={new Map((htf2Shadow?.trees ?? []).map((tree) => [tree.id, tree]))}
              />
              <p>Đường BOT/DỰ KIẾN là phân tích, không phải lệnh trên sàn.</p>
            </details>
          )}
          <p className="bot-footer-note">
            Chart chỉ hiện vị thế đang mở: mũi tên là fill vào, SL/TP theo dữ liệu sàn. Lệnh đã đóng
            xem ở tab Lệnh trên sàn.
          </p>
        </div>
      )}
      {!executionOnly && htf2Shadow && (
        <div className="learning-section-card">
          <div className="card-header">
            <div>
              <h4>AST HTF V2 — Quần thể & Dòng dõi tiến hoá</h4>
              <p className="muted-desc">
                {htf2Shadow.note}. Thế hệ {htf2Shadow.generation}, {htf2Shadow.tree_count} cây.
              </p>
            </div>
            <span className="badge-count">
              {htf2Shadow.status} · {htf2Shadow.resolved} đã đóng · {htf2Shadow.pending} đang chạy
            </span>
          </div>
          <HtfLineage trees={htf2Shadow.trees} nextEvolutionAt={htf2Shadow.next_evolution_at} />
        </div>
      )}
    </section>
  );
}

export function ForexObservationLog({
  events,
}: {
  events: (Partial<ObservationChartSnapshot> & {
    candle_time: number;
    status: string;
    reason: string;
  })[];
}) {
  return (
    <section className="panel prose">
      <div className="section-head">
        <div>
          <h2>Nhật ký quan sát Exness XAUUSD</h2>
          <p>
            20 nến quan sát gần nhất; trạng thái setup và lý do hủy/hết hạn. Không phải lịch sử đặt
            lệnh.
          </p>
        </div>
      </div>
      {events.length === 0 ? (
        <p className="micro">Chưa có bản ghi nhật ký quan sát nào được lưu.</p>
      ) : (
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>Thời gian nến (UTC)</th>
                <th>Trạng thái</th>
                <th>Chiều</th>
                <th>Nguồn quan sát</th>
                <th>Mức breakout</th>
                <th>SL / TP</th>
                <th>Lý do</th>
              </tr>
            </thead>
            <tbody>
              {events.map((ev) => (
                <tr key={ev.candle_time}>
                  <td>
                    {new Date(ev.candle_time * 1000).toISOString().slice(0, 16).replace('T', ' ')}
                  </td>
                  <td>{observationStatusLabels[ev.status] ?? ev.status}</td>
                  <td>{ev.side ? ev.side.toUpperCase() : '—'}</td>
                  <td>
                    {ev.provenance === 'catch_up'
                      ? 'Chạy bù'
                      : ev.provenance === 'historical'
                        ? 'Lịch sử'
                        : 'Trực tiếp'}
                  </td>
                  <td>{ev.level ? `${formatPrice(ev.level)}` : '—'}</td>
                  <td>
                    {ev.stop && ev.target
                      ? `${formatPrice(ev.stop)} / ${formatPrice(ev.target)}`
                      : '—'}
                  </td>
                  <td className="micro">{ev.reason}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

export function ForexReplayControl({
  onComplete,
}: {
  onComplete?: (data: ResearchApiResponse) => void;
}) {
  const [running, setRunning] = useState(false);
  const [error, setError] = useState('');
  const [result, setResult] = useState<ResearchApiResponse | null>(null);

  async function runReplay() {
    if (running) return;
    setRunning(true);
    setError('');
    try {
      const response = await fetch('/api/workspaces/exness_forex/research/replay', {
        method: 'POST',
        signal: AbortSignal.timeout(180000),
      });
      if (!response.ok) {
        const body = await response.json();
        throw new Error(typeof body.detail === 'string' ? body.detail : 'Replay không thành công');
      }
      const data: ResearchApiResponse = await response.json();
      if (!data.comparison || data.error)
        throw new Error(data.error ?? 'Chưa có báo cáo so sánh hợp lệ');
      setResult(data);
      onComplete?.(data);
    } catch (failure) {
      setError(
        failure instanceof Error
          ? failure.message
          : 'Không nhận được kết quả replay; kiểm tra tab Backtest trước khi chạy lại.',
      );
    } finally {
      setRunning(false);
    }
  }

  return (
    <div className="panel prose" style={{ marginTop: '1rem' }}>
      <h3>Thử ngay trên nến Exness đã lưu</h3>
      <p>
        Replay các chiến lược trên toàn bộ nến M15 trong cơ sở dữ liệu. Mỗi chiến lược một ví
        $10,000 độc lập, cùng spread Bid/Ask và trượt giá 0/1/2 bps. Không gọi API sàn, không sửa ví
        paper đang chạy.
      </p>
      <button type="button" disabled={running} onClick={() => void runReplay()}>
        {running ? 'Đang chạy replay…' : 'Chạy so sánh chiến lược'}
      </button>
      {running && <p role="status">Đang tính các nến đã đóng; có thể mất 10–30 giây.</p>}
      {error && (
        <p role="alert" className="negative">
          {error}
        </p>
      )}
      {result?.comparison && (
        <div role="status">
          <p>
            Đã replay {result.comparison.processed_bars.toLocaleString()} nến M15. Báo cáo chi tiết
            ở tab Backtest.
          </p>
          {result.comparison.results
            .filter((row) => row.slippage_bps === 2)
            .map((row) => (
              <p key={row.strategy}>
                <code>{row.strategy}</code>: {row.signals} setup · {row.trades} lệnh đóng ·{' '}
                {row.status === 'blocked'
                  ? 'Ví bị chặn bởi gap chưa xác minh; kết quả chưa đầy đủ'
                  : row.status === 'open'
                    ? 'Còn lệnh chờ/mở'
                    : 'Đã xử lý hết lịch sử'}
                . Đóng trước nghỉ phiên: {row.session_closes ?? 0} · Mẫu qua gap bị loại:{' '}
                {row.excluded_trades ?? 0}.
              </p>
            ))}
        </div>
      )}
    </div>
  );
}

export function ForexResearchPage({ revision }: { revision: number }) {
  const [data, setData] = useState<ResearchApiResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError('');
    fetch('/api/workspaces/exness_forex/research', {
      signal: AbortSignal.timeout(10000),
    })
      .then(async (res) => {
        if (!res.ok) throw new Error('Chưa có báo cáo replay');
        const json = await res.json();
        if (active) setData(json);
      })
      .catch(() => {
        if (active) setError('Không tải được báo cáo từ API. Bấm Làm mới để thử lại.');
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [revision]);

  const baseline =
    data?.reports?.find((report) => report.assumptions.slippage_bps_each_side === 0) ??
    data?.reports?.[0];

  return (
    <section className="panel prose">
      <div className="section-head">
        <div>
          <h2>Báo cáo replay cũ · XAUUSD (đã ngừng)</h2>
          <p>Báo cáo lưu từ family cũ, chỉ để đọc. Không dùng để cấp quyền cho cây V2.</p>
        </div>
        <span className="pill amber">
          <FlaskConical size={12} style={{ marginRight: 4, verticalAlign: -1 }} />
          Nghiên cứu · Chưa bật giao dịch
        </span>
      </div>

      <div className="replay-discontinued-notice">
        <p>
          <strong>Hệ thống đã chuyển sang Forex HTF AST V2 duy nhất.</strong>
        </p>
        <p className="micro muted">
          Replay lịch sử trên các chiến lược family cũ (Breakout, Trend, Range, Router) đã ngừng
          hoạt động để tránh nạp dữ liệu quá khứ sai lệch vào bộ học sống. Dữ liệu tiến hoá hiện tại
          được tích luỹ 100% từ nến sống (forward) trong tab Tiến hoá & Cây HTF.
        </p>
      </div>
      {data?.comparison && (
        <div className="table-scroll">
          <h3>Đối chiếu chiến lược · 1 ví $10,000, rủi ro 1%, tối đa 1 lệnh, chưa gồm swap</h3>
          <p>
            {data.comparison.processed_bars} nến M15 ·{' '}
            {new Date(data.comparison.start * 1000).toISOString()} –{' '}
            {new Date(data.comparison.end * 1000).toISOString()}.
          </p>
          <p>
            Số dư, lợi nhuận và drawdown chỉ tính lệnh đã đóng; không phải equity cuối kỳ. Gap chưa
            xác minh khi đang giữ vị thế sẽ chặn ví, không bỏ tổn thất chưa biết để tiếp tục. Ví bị
            chặn hoặc còn vị thế mở chưa có kết quả đầy đủ; chưa dùng để chọn chiến lược thắng. Chưa
            tính swap, commission chưa xác minh, lot và margin thực tế; chưa kiểm định ngoài mẫu.
          </p>
          <table>
            <thead>
              <tr>
                <th>Chiến lược</th>
                <th>Trượt giá bps</th>
                <th>Tín hiệu</th>
                <th>Bỏ vì đang có lệnh</th>
                <th>Lệnh</th>
                <th>Thắng</th>
                <th>Số dư đã đóng</th>
                <th>Lợi nhuận đã đóng</th>
                <th>PF</th>
                <th>Drawdown đã đóng</th>
                <th>R TB</th>
                <th>Trạng thái / bỏ qua</th>
              </tr>
            </thead>
            <tbody>
              {data.comparison.results.map((row) => (
                <tr key={`${row.strategy}-${row.slippage_bps}`}>
                  <td>
                    {{
                      breakout_retest: 'Breakout + Retest',
                      trend_pullback: 'Trend Pullback',
                      range_rejection: 'Range Rejection',
                      regime_router: 'Regime Router (gộp)',
                    }[row.strategy] ?? row.strategy}
                  </td>
                  <td>{row.slippage_bps}</td>
                  <td>{row.signals}</td>
                  <td>{row.skipped_busy}</td>
                  <td>{row.trades}</td>
                  <td>{row.win_rate == null ? '—' : `${(row.win_rate * 100).toFixed(1)}%`}</td>
                  <td>{row.final_balance.toFixed(2)}</td>
                  <td>{row.return_pct.toFixed(2)}%</td>
                  <td>{row.profit_factor == null ? '—' : row.profit_factor.toFixed(2)}</td>
                  <td>{row.max_drawdown_pct.toFixed(2)}%</td>
                  <td>{row.mean_r == null ? '—' : row.mean_r.toFixed(2)}</td>
                  <td>
                    {row.status === 'blocked'
                      ? 'Chặn: gap chưa xác minh'
                      : row.status === 'open'
                        ? 'Còn vị thế chờ / mở'
                        : 'Hoàn tất'}
                    {row.blocked_at != null && (
                      <>
                        {' '}
                        · {new Date(row.blocked_at * 1000).toISOString()}
                        {row.blocked_gap && (
                          <span className="micro block muted">
                            (thiếu {row.blocked_gap.missing_intervals} nến,{' '}
                            {row.blocked_gap.classification})
                          </span>
                        )}
                      </>
                    )}
                    <br />
                    Không nối lệnh qua gap.
                    <span className="micro block">
                      Đóng trước nghỉ phiên: {row.session_closes ?? 0} · Loại do thiếu dữ liệu:{' '}
                      {row.excluded_trades ?? 0}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {!!data?.gap_audits?.length && (
        <div className="panel prose">
          <h3>Đối soát gap trực tiếp với Exness</h3>
          <p>
            API trả rỗng không chứng minh nghỉ lễ. Mẫu qua gap bị loại; PnL chưa biết không được coi
            là 0. Số dư và lợi nhuận chỉ tính phần dữ liệu đủ.
          </p>
          {data.gap_audits.slice(0, 10).map((gap) => (
            <p key={`${gap.after}-${gap.before}`}>
              {new Date((gap.after + 900) * 1000).toISOString()} –{' '}
              {new Date(gap.before * 1000).toISOString()}:{' '}
              {{
                no_bars_returned: 'Exness không trả nến',
                backfilled: 'Đã tải bù nến',
                api_error: 'API lỗi, sẽ thử lại',
                partial_check: 'Mới kiểm tra một phần',
                inconsistent_timeframes: 'M1/M15 chưa khớp',
              }[gap.status] ?? gap.status}{' '}
              · Kiểm tra: {new Date(gap.checked_at * 1000).toISOString()}
            </p>
          ))}
        </div>
      )}
      {error && <p role="alert">{error}</p>}
      {!loading && !error && !baseline && !data?.comparison && (
        <p className="micro">
          {data?.error ??
            'Chưa tìm thấy file kết quả replay (`research-output/exness-mtf-replay.json`). Hãy chạy lệnh replay trong worker để tạo báo cáo.'}
        </p>
      )}

      {baseline && data && (
        <>
          <p className="micro">
            Kho nến: {new Date(baseline.start * 1000).toISOString()} –{' '}
            {new Date(baseline.end * 1000).toISOString()}. Bao gồm warmup; không phải toàn bộ thời
            gian đều có giao dịch.
          </p>
          <div className="research-metrics">
            <div>
              <span className="muted">Số setup xác nhận</span>
              <strong>
                {baseline.summary.confirmations} mẫu ({baseline.summary.resolved} đã kết thúc)
              </strong>
            </div>
            <div>
              <span className="muted">Tỷ lệ thắng</span>
              <strong>
                {baseline.summary.win_rate !== null
                  ? `${(baseline.summary.win_rate * 100).toFixed(1)}%`
                  : '—'}
              </strong>
            </div>
            <div>
              <span className="muted">Tổng R (chưa swap)</span>
              <strong>{baseline.summary.sum_r_ex_swap.toFixed(2)} R</strong>
            </div>
            <div>
              <span className="muted">Profit Factor</span>
              <strong>{baseline.summary.profit_factor_ex_swap?.toFixed(2) ?? '—'}</strong>
            </div>
          </div>

          <div className="frames-summary" style={{ marginTop: 16 }}>
            <h3>
              Thống kê theo chiều lệnh (Trượt giá {baseline.assumptions.slippage_bps_each_side} bps
              mỗi chiều)
            </h3>
            <ul>
              <li>
                <strong>LONG:</strong> {baseline.by_side.long.resolved} lệnh · Tỷ lệ thắng:{' '}
                {baseline.by_side.long.win_rate !== null
                  ? `${(baseline.by_side.long.win_rate * 100).toFixed(1)}%`
                  : '—'}{' '}
                · Lợi nhuận: {baseline.by_side.long.sum_r_ex_swap.toFixed(2)} R
              </li>
              <li>
                <strong>SHORT:</strong> {baseline.by_side.short.resolved} lệnh · Tỷ lệ thắng:{' '}
                {baseline.by_side.short.win_rate !== null
                  ? `${(baseline.by_side.short.win_rate * 100).toFixed(1)}%`
                  : '—'}{' '}
                · Lợi nhuận: {baseline.by_side.short.sum_r_ex_swap.toFixed(2)} R
              </li>
            </ul>
          </div>

          {data.reports.some((report) => report.dataset_sha256 !== baseline.dataset_sha256) && (
            <p role="status">Một số báo cáo khác bộ nến; không đưa vào bảng so sánh.</p>
          )}
          {data.reports.length > 1 && (
            <div className="table-scroll" style={{ marginTop: 20 }}>
              <h3>Độ nhạy trượt giá (Slippage Sensitivity)</h3>
              <table>
                <thead>
                  <tr>
                    <th>Trượt giá mỗi phía</th>
                    <th>Tổng R</th>
                    <th>R trung bình / lệnh</th>
                    <th>Profit Factor</th>
                    <th>Drawdown mẫu đóng (không phải vốn)</th>
                  </tr>
                </thead>
                <tbody>
                  {data.reports
                    .filter((report) => report.dataset_sha256 === baseline.dataset_sha256)
                    .map((rep) => (
                      <tr key={rep.assumptions.slippage_bps_each_side}>
                        <td>{rep.assumptions.slippage_bps_each_side} bps</td>
                        <td>
                          <strong>{rep.summary.sum_r_ex_swap.toFixed(2)} R</strong>
                        </td>
                        <td>
                          {rep.summary.mean_r_ex_swap !== null
                            ? rep.summary.mean_r_ex_swap.toFixed(4)
                            : '—'}
                        </td>
                        <td>{rep.summary.profit_factor_ex_swap?.toFixed(2) ?? '—'}</td>
                        <td>{rep.summary.closed_sample_drawdown_r.toFixed(2)} R</td>
                      </tr>
                    ))}
                </tbody>
              </table>
            </div>
          )}

          <div className="notice-card" style={{ marginTop: 20 }}>
            <AlertTriangle
              size={18}
              style={{ color: '#b59854', marginRight: 8, verticalAlign: -2 }}
            />
            <strong>Đánh giá thực tế:</strong>
            <p className="micro" style={{ marginTop: 6, lineHeight: 1.5 }}>
              Có {baseline.summary.resolved} mẫu kết thúc và{' '}
              {baseline.summary.confirmations - baseline.summary.resolved} mẫu chưa đủ dữ liệu/không
              hợp lệ. Spread đã nằm trong bid/ask. Chi phí chưa hiệu chỉnh theo tài khoản; swap chưa
              tính. Mẫu có thể chồng lấn, tổng R không phải lợi nhuận tài khoản. Chưa kiểm định
              ngoài mẫu; không dùng báo cáo này để tự bật giao dịch.
            </p>
          </div>
        </>
      )}
    </section>
  );
}
