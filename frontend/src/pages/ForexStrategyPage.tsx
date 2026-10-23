import { forexSetupGuides, forexTriggerGuides, forexExitGuides } from '../lib/forex-strategy-info';
import { useEffect, useMemo, useRef, useState } from 'react';
import { Eye } from 'lucide-react';
import type { ForexDashboard } from '../App';
import {
  MtfObservationChart,
  type ObservationChartSnapshot,
} from '../components/MtfObservationChart';
import { StrategyOverlayMenu } from '../components/StrategyOverlayMenu';
import { treeLabel, shortTreeId } from '../lib/forex-tree-label';
import { formatForexPrice as formatPrice, observationStatusLabels } from '../lib/format';
import { cachedCandles, candleKey, storeCandles } from '../lib/forex-cache';
import { feedErrorText } from '../lib/forex-labels';
import { clockTime, relativeTime, formatTime } from '../lib/time-format';
import { timeZoneLabel } from '../lib/time-zone';
import type { BrokerPosition, ExnessExecution, LearnerAnalysis } from '../types/exness-execution';

interface ForexCandle {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  tick_volume?: number | null;
}

const noCandles: ForexCandle[] = [];

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

export function ForexCatchupStatus({
  dashboard,
  disconnected,
  compact = false,
}: {
  dashboard: ForexDashboard;
  disconnected: boolean;
  compact?: boolean;
}) {
  const progress = dashboard.observation.catch_up;
  const frames = dashboard.frames ?? [];
  const downloading = frames.some(
    (frame) => frame.remaining_intervals === null || frame.remaining_intervals > 0,
  );
  const paused = dashboard.observation.status === 'data_paused';
  // Lỗi feed thường giống nhau cho cả 3 khung (vd. rate limit): gộp thành một dòng.
  const failing = frames.find((frame) => frame.error);
  const retryAt = frames
    .map((frame) => frame.retry_at)
    .filter((value): value is string => !!value && new Date(value).getTime() > Date.now())
    .sort()
    .at(-1);
  const feedError = failing
    ? `${feedErrorText(failing.error)}${retryAt ? ` · tự thử lại ${relativeTime(retryAt)} (${formatTime(retryAt, true)})` : ''}`
    : null;
  const title = disconnected
    ? 'Mất kết nối API · dữ liệu lưu gần nhất'
    : !dashboard.market.worker_online
      ? 'Worker nến offline · chờ chạy lại để tải bù'
      : feedError
        ? feedError
        : downloading
          ? 'Đang tải bù nến Exness'
          : paused
            ? 'Tạm dừng phân tích · kiểm tra lỗi dữ liệu'
            : progress?.pending_bars
              ? 'Đang tính bù MTF'
              : !progress || progress.last_processed_candle === null
                ? 'Chờ khởi tạo MTF'
                : 'Đã phân tích hết nến hiện có';

  const healthy =
    !disconnected &&
    dashboard.market.worker_online &&
    !feedError &&
    !downloading &&
    !paused &&
    !progress?.pending_bars;
  const body = (
    <>
      <div className="forex-catchup-counts">
        {frames.map((frame) => (
          <span key={frame.timeframe}>
            <strong>{frame.timeframe.toUpperCase()}</strong> ·{' '}
            {frame.remaining_intervals === null
              ? 'Chưa khởi tạo'
              : `${frame.remaining_intervals.toLocaleString('vi-VN')} khoảng nến cần kiểm tra`}
            {frame.error && frame.error !== failing?.error && <> · {feedErrorText(frame.error)}</>}
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
          ? `${formatTime(progress.last_processed_candle)} (giờ mở nến)`
          : 'Chưa có'}
        . Tính bù chỉ bắt đầu khi cả 3 khung đồng bộ và hợp lệ. Khoảng cần kiểm tra có thể thuộc giờ
        nghỉ phiên, không đồng nghĩa mất nến.
      </p>
      <p className="micro">
        Setup chạy bù mang nhãn “Chạy bù”; không đặt lệnh cho tín hiệu đã bỏ lỡ.
      </p>
    </>
  );
  if (compact)
    return (
      <details
        className={`forex-catchup-compact ${healthy ? 'is-ok' : 'is-warn'}`}
        role="status"
        aria-live="polite"
      >
        <summary>
          <span className="forex-catchup-dot" aria-hidden="true" />
          {title}
        </summary>
        {body}
      </details>
    );
  return (
    <section className="panel forex-catchup" role="status" aria-live="polite">
      <strong>{title}</strong>
      {body}
    </section>
  );
}

const rejectLabels: Record<string, [string, string]> = {
  adx_weak: ['ADX H4 yếu', 'ADX14 chưa vượt ngưỡng của cây'],
  price_zone_opposed: ['Ngoài vùng giá H4', 'Long cần nửa dưới, Short cần nửa trên biên H4'],
  adr_exhausted: ['Đã dùng biên ADR', 'Biên ngày đạt ngưỡng ADR, không phải trần giá'],
  spread_wide: ['Spread rộng', 'Spread vượt hệ số so với các nến trước'],
  outside_liquid_session: ['Ngoài phiên thanh khoản', 'Giờ London/New York có điều chỉnh DST'],
  smt_no_reference: [
    'SMT không áp dụng',
    'Cặp này chưa có cặp tham chiếu, cây không được bỏ qua filter',
  ],
  smt_not_confirmed: ['Chưa có SMT', 'Cặp chính và tham chiếu chưa tạo phân kỳ cực trị H1'],
  smt_history_missing: ['Chờ dữ liệu SMT', 'Thiếu nến H1 đồng thời của cặp tham chiếu'],
  daily_history_missing: ['Chờ lịch sử ngày', 'Chưa đủ ngày UTC hoàn chỉnh theo lịch chuẩn'],
  spread_history_missing: ['Chờ lịch sử spread', 'Thiếu nến bid/ask đóng đồng thời'],
  adx_history_missing: ['Chờ dữ liệu ADX', 'Chưa đủ nến H4 đóng'],
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
  sl_too_tight: ['SL quá sát', 'Dưới 1.0 ATR H1'],
  no_target: ['Không có mục tiêu', 'Không có cản H1/H4 phía trước'],
  poor_rr: ['R/R kém', 'Dưới 1.5R sau chi phí'],
  h1_not_closed: ['H1 chưa đóng', 'Chờ nến H1 đóng'],
  h4_stale: ['H4 cũ', 'Dữ liệu H4 không còn mới'],
};

type Tree2 = NonNullable<
  NonNullable<ExnessExecution['learning']>['htf_ast3_shadow']
>['trees'][number];

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
          `${treeLabel(row, name).full} ${row.chart?.note ?? ''}`.toLowerCase().includes(needle),
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
          <GenBadge tree={lineage.get(name)} />{' '}
          <strong title={name}>{treeLabel(row, name).full}</strong> · {row.side?.toUpperCase()} · SL{' '}
          {row.stop ?? '—'} · mức {row.level ?? '—'}
          <small>{note(row)}</small>
        </div>
      ))}
      {needle ? (
        <table>
          <tbody>
            {matches.slice(0, 30).map(([name, row]) => (
              <tr key={name}>
                <td>
                  <GenBadge tree={lineage.get(name)} />{' '}
                  <span title={name}>{treeLabel(row, name).full}</span>
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
  setup_reclaim: 'Lấy lại mốc setup H1',
  engulfing: 'Nến nhấn chìm',
  order_block: 'Kiểm định vùng OB',
  pdh_pdl_sweep: 'Quét đỉnh/đáy ngày trước',
  asia_sweep: 'Quét biên phiên Á UTC',
  inside_bar: 'Nến trong / NR7',
  round_level: 'Từ chối mốc giá tròn',
  partial_be_h1: 'BE / Trail theo H1',
  partial_be_m15: 'BE / Trail theo M15',
  fixed_structural: 'Cố định theo cản',
  evidence_h1: 'Giữ 100%, thoát khi nến H1 yếu',
  evidence_m15: 'Giữ 100%, thoát khi nến M15 yếu',
  barrier_partial_h1: 'Chốt tại cản H1 gần nhất',
  runner_h1: 'Gồng 100% + trail ATR H1',
  trail_swing_h1: 'Gồng 100% + trail swing H1',
  ladder_h1: 'Chốt từng nấc +1R/+2R',
};

const paramLabels: Record<string, string> = {
  fraction: 'Tỷ lệ chốt',
  min_r: 'R tối thiểu',
  trail_r: 'Bắt đầu trail ở R',
  atr_multiple: 'Trail × ATR',
  step_r: 'Bước R',
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
        {treeLabel(found, id).full}
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
            Chưa có cây con. Cần cây bố mẹ đủ ≥10 mẫu forward đã đóng với R trung bình dương
            {nextEvolutionAt ? ` · chu kỳ kế tiếp ${formatTime(nextEvolutionAt)}` : ''}.
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
              <span className="lineage-status-tag">Chờ gom mẫu ({detail.resolved}/10)</span>
            )}
            <button
              type="button"
              className="secondary lineage-close-btn"
              onClick={() => setSelected(null)}
            >
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
                <strong>{treeLabel(detail, detail.id).full}</strong>
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
                <p>
                  Nến H4 đóng thuận chiều EMA50 (trên EMA50 chỉ Buy, dưới EMA50 chỉ Sell). Ngược
                  trend thì bỏ qua.
                </p>
              </div>
            </div>

            <div className="lineage-step-card">
              <span className="step-num">2</span>
              <div className="step-content">
                <strong>Setup H1</strong>
                <span className="step-tag">{label(detail.h1_setup)}</span>
                <p>
                  {forexSetupGuides[detail.h1_setup] ??
                    'Cấu trúc hình thành trên khung H1 đã đóng.'}{' '}
                  SL đặt tại mức vô hiệu cấu trúc H1 + 0.25 ATR buffer (hiệu lực 4h).
                </p>
              </div>
            </div>

            <div className="lineage-step-card">
              <span className="step-num">3</span>
              <div className="step-content">
                <strong>Trigger M15</strong>
                <span className="step-tag">{label(detail.m15_trigger)}</span>
                <p>
                  {forexTriggerGuides[detail.m15_trigger] ??
                    'Xác nhận thời điểm vào lệnh tại nến M15 đóng.'}{' '}
                  M15 chỉ trigger, không quyết định SL/TP.
                </p>
              </div>
            </div>

            <div className="lineage-step-card">
              <span className="step-num">4</span>
              <div className="step-content">
                <strong>Quản lý lệnh</strong>
                <span className="step-tag">{label(detail.exit_policy)}</span>
                <p>
                  {forexExitGuides[detail.exit_policy] ?? 'Quản lý rủi ro theo cấu trúc HTF.'} TP
                  runner bám theo cản H1/H4 gần nhất (R/R tối thiểu 1.5R).
                </p>
                {!!Object.keys(detail.exit_params ?? {}).length && (
                  <p className="micro">
                    Tham số tiến hoá:{' '}
                    {Object.entries(detail.exit_params ?? {})
                      .map(([name, value]) => `${paramLabels[name] ?? name} ${value}`)
                      .join(' · ')}
                  </p>
                )}
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
              <strong
                className={
                  detail.mean_r == null ? '' : detail.mean_r >= 0 ? 'positive' : 'negative'
                }
              >
                {detail.mean_r == null
                  ? '—'
                  : `${detail.mean_r >= 0 ? '+' : ''}${detail.mean_r.toFixed(2)}R`}
              </strong>
            </div>
            <div className="lineage-stat-box">
              <span>Cận dưới 95%</span>
              <strong
                className={
                  detail.lower_r == null ? '' : detail.lower_r >= 0 ? 'positive' : 'negative'
                }
              >
                {detail.lower_r == null
                  ? '—'
                  : `${detail.lower_r >= 0 ? '+' : ''}${detail.lower_r.toFixed(2)}R`}
              </strong>
            </div>
          </div>
          {detail.sides && (
            <p className="micro">
              Long: {detail.sides.long.resolved} mẫu · {detail.sides.long.mean_r?.toFixed(2) ?? '—'}
              R{' · '}Short: {detail.sides.short.resolved} mẫu ·{' '}
              {detail.sides.short.mean_r?.toFixed(2) ?? '—'}R
            </p>
          )}
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
                      <span
                        className="lineage-row-id"
                        title={`${treeLabel(row, row.id).full}\n${row.id}`}
                      >
                        {treeLabel(row, row.id).title}
                      </span>

                      <div className="lineage-tags-inline">
                        <span className="lineage-tag tag-h1">H1 {label(row.h1_setup)}</span>
                        <span className="lineage-tag tag-m15">M15 {label(row.m15_trigger)}</span>
                        <span className="lineage-tag tag-exit">{label(row.exit_policy)}</span>
                      </div>

                      <span className="lineage-row-meta">
                        {row.parents?.length ? (
                          <span className="lineage-parents-brief">
                            từ {row.parents.map(shortTreeId).join(' × ')}
                          </span>
                        ) : (
                          <span className="muted">hạt giống gốc</span>
                        )}
                        {children.get(row.id)?.length ? (
                          <span className="lineage-child-badge">
                            {' '}
                            · {children.get(row.id)!.length} con
                          </span>
                        ) : null}
                      </span>

                      <span className="lineage-row-metric font-mono">
                        {row.resolved ? (
                          <span
                            className={
                              row.mean_r != null && row.mean_r >= 0 ? 'positive' : 'negative'
                            }
                          >
                            {row.mean_r != null && row.mean_r >= 0 ? '+' : ''}
                            {row.mean_r?.toFixed(2)}R
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
  const symbol = dashboard.workspace.symbol;
  // Nến gắn với khoá cặp/khung/phía để lần render đầu sau khi đổi không dùng nến của khoá cũ.
  const [candleState, setCandleState] = useState<{ key: string; rows: ForexCandle[] }>({
    key: '',
    rows: noCandles,
  });
  const [analysis, setAnalysis] = useState<LearnerAnalysis | null>(null);
  const [htf2Shadow, setHtf2Shadow] =
    useState<NonNullable<ExnessExecution['learning']>['htf_ast3_shadow']>();
  useEffect(() => {
    const controller = new AbortController();
    setAnalysis(null);
    setHtf2Shadow(undefined);
    const load = async () => {
      try {
        const res = await fetch(`/api/workspaces/exness_forex/learning/analysis?symbol=${symbol}`, {
          signal: controller.signal,
        });
        const body = (await res.json()) as (LearnerAnalysis | { status: string }) & {
          htf_ast3_shadow?: typeof htf2Shadow;
        };
        if (controller.signal.aborted) return;
        setHtf2Shadow(res.ok ? body.htf_ast3_shadow : undefined);
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
  }, [symbol]);
  const [loading, setLoading] = useState(false);
  const [frame, setFrame] = useState('15m');
  const [side, setSide] = useState('bid');
  // Lựa chọn lớp vẽ theo từng cặp (không lẫn cây giữa các cặp); giữ nguyên khi đổi khung.
  const [pickedBySymbol, setPickedBySymbol] = useState<Record<string, string[]>>({});
  const picked = pickedBySymbol[symbol] ?? [];
  const setShownStrategies = (names: string[]) =>
    setPickedBySymbol((prev) => ({ ...prev, [symbol]: names }));
  // Cây đang giữ vị thế broker tự vẽ; cây khác chỉ vẽ khi người dùng chọn.
  const holdingTrees = useMemo(
    () =>
      [
        ...new Set(
          executions
            .filter((row) => row.status === 'open' && /^hast[23]_/.test(row.strategy ?? ''))
            .map((row) => row.strategy as string),
        ),
      ].filter((id) => analysis?.strategies[id]),
    [executions, analysis],
  );
  const shownStrategies = useMemo(
    () => [...new Set([...picked, ...holdingTrees])],
    [picked, holdingTrees],
  );
  const historyBefore = selectedPosition?.status === 'closed' ? selectedPosition.close_time : null;
  const cacheKey = historyBefore ? null : candleKey(symbol, frame, side);
  const viewKey = `${symbol}:${frame}:${side}:${historyBefore ?? 'live'}`;
  const candles =
    candleState.key === viewKey
      ? candleState.rows
      : ((cacheKey ? cachedCandles(cacheKey)?.rows : undefined) ?? noCandles);
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
    setQuoteState({ status: 'connecting', quote: null });
    const stream = new EventSource(`/api/workspaces/exness_forex/quotes/stream?symbol=${symbol}`);
    const timer = window.setInterval(() => setClock(Date.now()), 1000);
    stream.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        if (data.symbol !== symbol || typeof data.status !== 'string') return;
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
  }, [symbol]);
  const snap = dashboard.observation.snapshot;
  const currentStatus = dashboard.observation.status;
  const hasRange =
    snap?.range_high != null &&
    snap?.range_low != null &&
    snap.range_start !== undefined &&
    snap.range_end !== undefined;
  const fullSnapshot: ObservationChartSnapshot | null =
    hasRange && snap
      ? {
          strategy: 'exness_mtf_v1',
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
    const controller = new AbortController();
    // Vị thế đã đóng cần cửa sổ lịch sử riêng, không dùng cache nến mới nhất.
    const key = historyBefore ? null : candleKey(symbol, frame, side);
    const stateKey = `${symbol}:${frame}:${side}:${historyBefore ?? 'live'}`;
    const cached = key ? cachedCandles(key) : undefined;
    let oldest = cached?.rows[0]?.time;
    let more = cached?.hasMore ?? false;
    setLoading(!cached);
    setLoadingOlder(false);
    setHasMore(cached?.hasMore ?? false);
    setCandleState({ key: stateKey, rows: cached?.rows ?? noCandles });
    setChartError('');
    const load = async (older = false) => {
      if (busy || (older && (!more || oldest === undefined))) return;
      busy = true;
      if (older) setLoadingOlder(true);
      setChartError('');
      try {
        const res = await fetch(
          `/api/workspaces/exness_forex/candles?symbol=${symbol}&timeframe=${frame}&price_type=${side}&limit=500${older ? `&before=${oldest}` : historyBefore ? `&before=${historyBefore + 18000}` : ''}`,
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
        setCandleState((current) => {
          const base = current.key === stateKey ? current.rows : noCandles;
          const merged = new Map(base.map((bar) => [bar.time, bar]));
          incoming.forEach((bar) => merged.set(bar.time, bar));
          const rows = [...merged.values()].sort((left, right) => left.time - right.time);
          if (key) storeCandles(key, rows, more);
          return { key: stateKey, rows };
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
  }, [frame, side, historyBefore, symbol]);

  useEffect(() => {
    historyActions.current.refresh();
  }, [dashboard.market.last_sync]);

  const pnlAt = (position: BrokerPosition, price: number) => {
    const contract = Number(dashboard.instrument?.profile?.contract_size);
    if (
      (position.side !== 'buy' && position.side !== 'sell') ||
      ![contract, position.volume, position.open_price, price].every(
        (value) => typeof value === 'number' && Number.isFinite(value) && value > 0,
      )
    )
      return null;
    const move =
      position.side === 'buy' ? price - position.open_price! : position.open_price! - price;
    const conversion = Number(
      move >= 0
        ? dashboard.instrument?.profile?.quote_to_account_gain
        : dashboard.instrument?.profile?.quote_to_account_loss,
    );
    return Number.isFinite(conversion) && conversion > 0
      ? move * position.volume! * contract * conversion
      : null;
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
            <span className="broker-symbol">{symbol.slice(0, 3)}</span>
            <div>
              <h2>
                {symbol} <span>Exness</span>
              </h2>
              <p>
                Exness Demo <span>·</span> Giá gốc sàn <span>·</span> {timeZoneLabel()}
              </p>
            </div>
          </div>
          <div className="broker-quote-pair" aria-label="Giá Exness trực tiếp">
            <span>
              BID{' '}
              <strong className="broker-bid">
                {quoteLive ? formatPrice(quoteState.quote!.bid) : '—'}
              </strong>
            </span>
            <span>
              ASK{' '}
              <strong className="broker-ask">
                {quoteLive ? formatPrice(quoteState.quote!.ask) : '—'}
              </strong>
            </span>
            <span>
              SPREAD{' '}
              <strong>
                {quoteLive ? formatPrice(quoteState.quote!.ask - quoteState.quote!.bid) : '—'}
              </strong>
            </span>
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
              {executionOnly ? 'Chart khớp lệnh sàn' : 'Quan sát MTF'} · {symbol}
            </h2>
            <p>
              {executionOnly
                ? 'Dấu BUY/SELL/EXIT lấy từ deal Exness. Chọn vị thế để xem giá khớp, SL và TP sàn.'
                : '4h EMA50 xu hướng → 1h breakout vùng 20 nến → 15m retest và nến xác nhận giữ vùng. Chỉ để quan sát, không phải tín hiệu đặt lệnh.'}
            </p>
          </div>
          <span className="pill amber">
            <Eye size={12} style={{ marginRight: 4, verticalAlign: -1 }} />
            {executionOnly
              ? 'Lệnh demo trên sàn · Bid/Ask Exness'
              : 'Quan sát · nến bid gốc Exness'}
          </span>
        </div>
      )}
      <div className="mtf-source-strip">
        <span>Nến M15/H1/H4 Exness Demo</span>
        {snap?.['available_at'] && <span>Snapshot {formatTime(snap.available_at)}</span>}
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
            <span className="muted">Xu hướng 4h</span>
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
            <span className="muted">Vùng 20 nến 1h</span>
            <strong>
              {snap?.range_low && snap?.range_high
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
            <strong>Chi tiết quan sát:</strong> {dashboard.observation.message}
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
          <StrategyOverlayMenu
            analysis={analysis}
            selected={picked}
            onChange={setShownStrategies}
            holding={holdingTrees}
            trees={htf2Shadow?.trees}
          />
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
          {quoteState.quote && ` Tick gần nhất: ${formatTime(quoteState.quote.time, true)}`}
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
        key={`${symbol}-${frame}-${side}-${historyBefore ?? 'live'}`}
        pricePrecision={
          dashboard.instrument?.profile?.point_digits ??
          (symbol === 'XAUUSD' ? 3 : symbol.endsWith('JPY') ? 3 : 5)
        }
        onLoadOlder={hasMore && !chartError ? () => historyActions.current.older() : undefined}
        candles={candles}
        liveCandles={liveCandles}
        snapshot={!executionOnly && frame === '15m' && side === 'bid' ? fullSnapshot : null}
        analysis={executionOnly ? analysis : null}
        shown={shownStrategies}
        executions={executions}
        dark={executionOnly}
        onClosePosition={onClosePosition}
        onSelectPosition={onSelectPosition}
        pnlAt={pnlAt}
        pnlOf={pnlOf}
        selectedPositionId={selectedPosition?.position_id}
        frameSeconds={frame === '4h' ? 14400 : frame === '1h' ? 3600 : 900}
        trendLabel="Xu hướng 4h"
        levelLabel="Mức breakout"
        livePrice={
          quoteLive && !(executionOnly && selectedPosition?.status === 'closed')
            ? {
                price: side === 'bid' ? quoteState.quote!.bid : quoteState.quote!.ask,
                label: `${side.toUpperCase()} LIVE`,
              }
            : null
        }
        title={`Exness ${symbol} · ${executionOnly ? 'FILL SÀN DEMO' : 'MTF'} · ${side.toUpperCase()} ${frame} · ${timeZoneLabel()}`}
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
                <span>nến {clockTime(analysis.candle_time)}</span>
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
              <h4>AST HTF V3 — Hai chiều · thuận H4</h4>
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
