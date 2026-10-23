import { forexSetupGuides, forexTriggerGuides, forexExitGuides } from '../lib/forex-strategy-info';
import { useEffect, useMemo, useRef, useState } from 'react';
import { subscribeForexStream } from '../lib/forex-stream';
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
import { ForexSessionStatus } from '../components/ForexSessionStatus';
import { feedErrorText, marketSessionDisplay, priceDigits } from '../lib/forex-labels';
import { clockTime, relativeTime, formatTime } from '../lib/time-format';
import { timeZoneLabel } from '../lib/time-zone';
import type { BrokerPosition, ExnessExecution, LearnerAnalysis } from '../types/exness-execution';
import type { ForexEntryPilot } from '../types/forex-entry';

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
  quote_stale: ['Quote không đủ mới', 'Cần bid/ask đúng cặp, tuổi không quá 2 giây'],
  quote_gap: ['Mất liên tục giá', 'Khoảng quote vượt 2 giây; không vào đuổi'],
  quote_out_of_order: ['Quote ngược thời gian', 'Hủy để tránh suy đoán nhánh chạm trước'],
  stream_disconnected: ['Mất luồng giá', 'Hủy chờ; kết nối lại không tạo lệnh đuổi'],
  level_already_crossed: ['Giá đã vượt trước khi canh', 'Không giả định mức đã khớp trước đó'],
  entry_expired: ['Hết thời gian chờ', 'Tối đa 4 M15, không quá hạn setup'],
  setup_invalidated: ['Setup mất hiệu lực', 'Giữ SL/TP đã chụp, không tìm lại vùng mới'],
  control_disabled: ['Quyền execution đang tắt', 'Không gửi lệnh demo'],
  pilot_disabled: ['Nhóm thử đang tắt', 'Hủy chờ; không tự đóng vị thế'],
  worker_restart: ['Worker khởi động lại', 'Hủy chờ; lệnh đã gửi phải đối soát'],
  worker_stopped: ['Worker đã dừng', 'Không tiếp tục canh giá'],
  preflight_failed: ['Không đạt kiểm tra trước gửi', 'Không chuyển sang nhánh còn lại'],
  trigger_preflight_failed: ['Nhánh đã chạm nhưng chưa gửi', 'Không thử lại dưới khóa mới'],
  capacity_unavailable: ['Không đủ chỗ cho toàn bộ leg', 'Giới hạn 66 toàn tài khoản / 6 mỗi cặp'],
  entry_order_unknown: ['Không rõ nhánh chạm trước', 'OHLC không xác định được thứ tự'],
  entry_exit_order_unknown: ['Không rõ entry trước exit', 'Không dùng TP/SL xảy ra trước entry'],
};

const entryStateLabels: Record<string, string> = {
  waiting: 'Đang canh giá',
  pending: 'Chờ khớp mô phỏng',
  open: 'Shadow đang mở',
  triggered: 'Đã chạm mức ảo',
  submitting: 'Đang gửi broker',
  submitted: 'Đã gửi · chờ đối soát',
  uncertain: 'Chưa rõ kết quả · giữ reservation',
  completed: 'Đã đối soát kết thúc',
  cancelled: 'Đã hủy',
  expired: 'Hết hạn',
  resolved: 'Shadow đã đóng',
  ambiguous: 'Không rõ thứ tự',
  excluded: 'Bị loại',
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
                <strong>Vào lệnh</strong>
                <span className="step-tag">Market</span>
                <p>
                  Gửi lệnh Market ngay khi nến M15 trigger đóng. Tín hiệu chỉ có hiệu lực 2 phút sau
                  khi nến đóng; quá hạn thì bỏ, không đuổi giá.
                </p>
              </div>
            </div>

            <div className="lineage-step-card">
              <span className="step-num">5</span>
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
const signedR = (value: number | null | undefined) =>
  value == null ? '—' : `${value > 0 ? '+' : ''}${value.toFixed(2)}R`;
const rTone = (value: number | null | undefined) =>
  value == null ? '' : value >= 0 ? 'positive' : 'negative';
const winnerLabels: Record<string, string> = {
  limit: 'Limit giữa',
  stop: 'Stop phá nến',
  market: 'Market',
};

const entryDefaults: Record<string, number> = {
  limit_ratio: 0.5,
  stop_buffer_atr: 0.05,
  wait_bars: 4,
};
// Tham số mỗi kiểu vào lệnh dùng (khớp backend forex_entry_policy.entry_node).
const entryPolicyParams: Record<string, string[]> = {
  market: [],
  limit_setup_mid: ['limit_ratio', 'wait_bars'],
  stop_trigger_break: ['stop_buffer_atr', 'wait_bars'],
  limit_or_stop: ['limit_ratio', 'stop_buffer_atr', 'wait_bars'],
};
const entryPolicyLabels: Record<string, string> = {
  market: 'Market',
  limit_setup_mid: 'Limit ảo',
  stop_trigger_break: 'Stop ảo',
  limit_or_stop: 'Limit/Stop ảo',
};
const entryParamText = (name: string, value: number) =>
  name === 'limit_ratio'
    ? `Limit ${Math.round(value * 100)}%`
    : name === 'stop_buffer_atr'
      ? `Stop +${value} ATR`
      : `chờ ${value} nến`;
/** Giá trị một tham số trên cả nhóm: một số nếu mọi cây giống nhau, ngược lại khoảng min–max. */
function paramSpread(values: number[]) {
  if (!values.length) return null;
  const min = Math.min(...values);
  const max = Math.max(...values);
  return { min, max, same: min === max };
}

export function ForexEntryPilotPanel({
  entryPilot,
  symbol,
}: {
  entryPilot?: ForexEntryPilot;
  symbol: string;
}) {
  const [entrySearch, setEntrySearch] = useState('');
  const digits = priceDigits(symbol);
  const price = (value: number | null) => (value == null ? '—' : value.toFixed(digits));
  const pilot = entryPilot?.pilot;
  const trees = pilot?.trees ?? [];
  const intents = entryPilot?.intents ?? [];
  const waiting = intents.filter((intent) => intent.status === 'waiting').length;
  const samples = trees.reduce((total, tree) => total + tree.samples_count, 0);
  const resolved = trees.reduce((total, tree) => total + tree.resolved_count, 0);
  const comparison = pilot?.comparison;
  // Mọi cây cùng một kiểu vào lệnh thì đưa lên tiêu đề, không lặp lại từng dòng.
  const policies = new Set(trees.map((tree) => tree.entry_policy));
  const policyLabel = (policy: string) => entryPolicyLabels[policy] ?? policy;
  const spread = (name: string) =>
    paramSpread(
      trees
        .map((tree) => tree.entry_params?.[name])
        .filter((value): value is number => typeof value === 'number'),
    );
  const limitRatio = spread('limit_ratio');
  const stopBuffer = spread('stop_buffer_atr');
  const waitBars = spread('wait_bars');
  const showValue = (
    value: ReturnType<typeof paramSpread>,
    format: (n: number) => string,
    name: string,
  ) =>
    !value
      ? format(entryDefaults[name])
      : value.same
        ? format(value.min)
        : `${format(value.min)}–${format(value.max)} tuỳ cây`;
  // Cột tham số từng cây chỉ cần khi các cây khác nhau.
  const paramsVary = [limitRatio, stopBuffer, waitBars].some((value) => value && !value.same);
  const needle = entrySearch.trim().toLowerCase();
  const shownTrees = needle
    ? trees.filter((tree) =>
        [
          tree.id,
          tree.control_tree_id,
          tree.h1_setup,
          tree.m15_trigger,
          tree.exit_policy,
          label(tree.h1_setup),
          label(tree.m15_trigger),
          label(tree.exit_policy),
        ].some((value) => value.toLowerCase().includes(needle)),
      )
    : trees;
  const states = Object.entries(pilot?.counts ?? {});
  return (
    <>
      {entryPilot && pilot && pilot.status !== 'ready' && (
        <div className="learning-section-card entry-pilot is-blocked" role="status">
          <div className="card-header">
            <div>
              <h4>AST V4 Pilot · Limit/Stop ảo</h4>
              <p className="muted-desc">
                {pilot.status === 'not_initialized'
                  ? 'Chờ worker khởi tạo nhóm đối chiếu. Không dùng dữ liệu Market thay thế.'
                  : 'Nhóm đối chiếu chưa sẵn sàng. Entry thử nghiệm bị chặn.'}
              </p>
            </div>
            <span className="entry-pilot-state is-off">Chưa sẵn sàng</span>
          </div>
          {(entryPilot.error || pilot.error) && (
            <p className="entry-pilot-error">{entryPilot.error || pilot.error}</p>
          )}
        </div>
      )}
      {entryPilot && pilot?.trees && (
        <div className="learning-section-card entry-pilot">
          <div className="card-header">
            <div>
              <h4>
                AST V4 Pilot · {pilot.tree_count} cây{' '}
                {policies.size === 1 ? policyLabel([...policies][0]) : 'Limit/Stop ảo'} đối chiếu
              </h4>
              <p className="muted-desc">
                {pilot.note ||
                  'Bot tự canh giá qua tick stream, nhánh nào chạm trước gửi Market trên Exness Demo'}
              </p>
            </div>
            <span
              className={`entry-pilot-state ${entryPilot.enabled ? 'is-on' : 'is-off'}`}
              title={
                entryPilot.enabled
                  ? 'Nhóm thử được bật; vẫn cần quyền execution demo hiện có'
                  : 'Execution nhóm thử đang tắt'
              }
            >
              {entryPilot.enabled ? 'Đang bật' : 'Đang tắt'}
            </span>
          </div>

          <div className="entry-pilot-kpis">
            <div>
              <small>Đang canh giá</small>
              <strong className={waiting ? 'is-live' : ''}>{waiting}</strong>
              <span>{intents.length} setup gần nhất</span>
            </div>
            <div>
              <small>Mẫu shadow</small>
              <strong>{samples}</strong>
              <span>{resolved} đã đóng</span>
            </div>
            <div>
              <small>Mean R</small>
              <strong>
                <span className={rTone(comparison?.hybrid_mean_r)}>
                  {signedR(comparison?.hybrid_mean_r)}
                </span>
                <em> vs {signedR(comparison?.market_mean_r)} Market</em>
              </strong>
              <span>
                {comparison?.matched_signals
                  ? `Limit/Stop · ${comparison.matched_signals} tín hiệu, ${comparison.resolved_pairs} cặp đã đóng`
                  : 'Chưa có tín hiệu trùng setup'}
              </span>
            </div>
            <div>
              <small>Chênh lệch</small>
              <strong className={rTone(comparison?.delta_r)}>{signedR(comparison?.delta_r)}</strong>
              <span>Limit/Stop − Market</span>
            </div>
          </div>

          <div className="entry-pilot-meta">
            <div className="entry-pilot-states" aria-label="Trạng thái mẫu shadow">
              {states.length ? (
                states.map(([state, count]) => (
                  <span key={state} className={`entry-state-chip state-${state}`}>
                    {entryStateLabels[state] ?? state} <b>{count}</b>
                  </span>
                ))
              ) : (
                <span className="muted">Chưa có mẫu shadow</span>
              )}
            </div>
            <p>
              Shadow, không phải giao dịch broker · R sau chi phí, chưa swap
              {pilot.since
                ? ` · cutoff ${formatTime(pilot.since, true)}, không nhận fitness từ cây nguồn`
                : ''}
            </p>
          </div>

          <div className="entry-pilot-howto" aria-label="Cách vào lệnh">
            <h5>Cách vào lệnh</h5>
            <p>
              Khi nến M15 trigger đóng, bot không vào Market ngay mà đặt hai mức ảo (không phải
              pending trên sàn) và canh giá qua tick stream. Mức nào chạm trước thì gửi Market lên
              Exness Demo, mức còn lại huỷ.
            </p>
            <ol>
              {policies.has('limit_or_stop') || policies.has('limit_setup_mid') ? (
                <li>
                  <strong>Limit · chờ hồi</strong>
                  <span>
                    Giá hồi về{' '}
                    <b>{showValue(limitRatio, (n) => `${Math.round(n * 100)}%`, 'limit_ratio')}</b>{' '}
                    quãng từ giá đóng M15 tới mức cấu trúc H1 (Mua: dưới giá đóng · Bán: trên giá
                    đóng).
                  </span>
                </li>
              ) : null}
              {policies.has('limit_or_stop') || policies.has('stop_trigger_break') ? (
                <li>
                  <strong>Stop · phá nến</strong>
                  <span>
                    Giá vượt đỉnh (Mua) hoặc thủng đáy (Bán) nến trigger thêm{' '}
                    <b>{showValue(stopBuffer, (n) => `${n} ATR`, 'stop_buffer_atr')}</b>.
                  </span>
                </li>
              ) : null}
              <li>
                <strong>Hết hạn</strong>
                <span>
                  Chờ tối đa{' '}
                  <b>{showValue(waitBars, (n) => `${n} nến M15 (${n * 15} phút)`, 'wait_bars')}</b>{' '}
                  và không quá 4h15 kể từ setup H1. Hết hạn mà chưa chạm thì bỏ setup.
                </span>
              </li>
            </ol>
            <p className="micro">
              SL/TP vẫn theo cấu trúc H1 như cây V3 gốc; chỉ giá vào lệnh khác.
              {!paramsVary && ' Tất cả cây đang dùng cùng tham số entry.'}
            </p>
          </div>

          <details className="entry-pilot-intents">
            <summary>
              <span>Canh giá & kết quả broker</span>
              <span className="badge-count">{intents.length} setup</span>
            </summary>
            {intents.length ? (
              <div className="table-wrap">
                <table className="learner-clean-table">
                  <thead>
                    <tr>
                      <th>Cây / hướng</th>
                      <th>Trạng thái</th>
                      <th className="num">Limit / Stop ảo</th>
                      <th>Hết hạn</th>
                      <th>Nhánh thắng</th>
                      <th>Khớp broker / lệch mức</th>
                      <th>Lý do hủy</th>
                    </tr>
                  </thead>
                  <tbody>
                    {intents.map((intent) => (
                      <tr key={intent.id}>
                        <td>
                          <code>{intent.strategy}</code>{' '}
                          <span className={`side-chip ${intent.side}`}>
                            {intent.side === 'long' ? 'Mua' : 'Bán'}
                          </span>
                        </td>
                        <td>
                          <span
                            className={`entry-state-chip state-${intent.broker_fills?.length ? 'completed' : intent.status}`}
                          >
                            {intent.broker_fills?.length
                              ? 'Broker đã xác nhận khớp'
                              : (entryStateLabels[intent.status] ?? intent.status)}
                          </span>
                        </td>
                        <td className="num font-mono">
                          {price(intent.limit_price)} / {price(intent.stop_price)}
                        </td>
                        <td>{formatTime(intent.expires_at, true)}</td>
                        <td>{winnerLabels[intent.winner ?? ''] ?? '—'}</td>
                        <td className="font-mono">
                          {intent.broker_fills?.length ? (
                            intent.broker_fills.map((fill) => (
                              <div key={fill.position_id}>
                                {price(fill.price)} · lệch{' '}
                                {fill.deviation === null
                                  ? '—'
                                  : `${fill.deviation > 0 ? '+' : ''}${Number(fill.deviation.toPrecision(6))}`}
                              </div>
                            ))
                          ) : (
                            <span className="muted">Chưa xác nhận</span>
                          )}
                        </td>
                        <td title={intent.reason}>
                          {rejectLabels[intent.reason ?? '']?.[0] ?? intent.reason ?? '—'}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <p className="entry-pilot-empty">
                Chưa có setup nào được canh giá. Bot sẽ ghi lại ở đây khi một cây V4 bắt được tín
                hiệu M15.
              </p>
            )}
          </details>

          <div className="entry-pilot-toolbar">
            <input
              type="search"
              value={entrySearch}
              onChange={(e) => setEntrySearch(e.target.value)}
              placeholder="Lọc theo ID, setup H1, trigger M15, quản lý…"
              aria-label="Lọc cây đối chiếu"
            />
            <span className="muted">
              {needle ? `${shownTrees.length}/${trees.length} cây` : `${trees.length} cây`}
            </span>
          </div>
          <div className="table-wrap">
            <table className="learner-clean-table entry-pilot-trees">
              <thead>
                <tr>
                  <th>Cây V4 · gốc V3</th>
                  {(policies.size > 1 || paramsVary) && <th>Vào lệnh</th>}
                  <th>Setup H1 → Trigger M15</th>
                  <th>Quản lý</th>
                  <th className="num">Mẫu</th>
                  <th className="num">Đã đóng</th>
                  <th className="num">Mean R</th>
                </tr>
              </thead>
              <tbody>
                {shownTrees.map((t) => (
                  <tr key={t.id}>
                    <td>
                      <code className="entry-tree-id">{t.id}</code>
                      <small className="entry-tree-parent">từ {t.control_tree_id}</small>
                    </td>
                    {(policies.size > 1 || paramsVary) && (
                      <td>
                        {policies.size > 1 && (
                          <span className="entry-tree-policy">{policyLabel(t.entry_policy)}</span>
                        )}
                        <span className="entry-tree-params">
                          {(entryPolicyParams[t.entry_policy] ?? Object.keys(t.entry_params ?? {}))
                            .filter((name) => typeof t.entry_params?.[name] === 'number')
                            .map((name) => (
                              <span
                                key={name}
                                className={
                                  t.entry_params[name] === entryDefaults[name] ? '' : 'is-tuned'
                                }
                              >
                                {entryParamText(name, t.entry_params[name])}
                              </span>
                            ))}
                        </span>
                      </td>
                    )}
                    <td>
                      <span className="lineage-tags-inline">
                        <span className="lineage-tag tag-h1">{label(t.h1_setup)}</span>
                        <span className="lineage-tag tag-m15">{label(t.m15_trigger)}</span>
                      </span>
                    </td>
                    <td>
                      <span className="lineage-tag tag-exit">{label(t.exit_policy)}</span>
                    </td>
                    <td className={`num ${t.samples_count ? '' : 'muted'}`}>{t.samples_count}</td>
                    <td className={`num ${t.resolved_count ? '' : 'muted'}`}>{t.resolved_count}</td>
                    <td className={`num font-mono ${rTone(t.mean_r) || 'muted'}`}>
                      {signedR(t.mean_r)}
                    </td>
                  </tr>
                ))}
                {!shownTrees.length && (
                  <tr>
                    <td
                      colSpan={policies.size > 1 || paramsVary ? 7 : 6}
                      className="entry-pilot-empty"
                    >
                      Không có cây nào khớp “{entrySearch.trim()}”.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </>
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
  const [entryPilot, setEntryPilot] = useState<ForexEntryPilot>();
  useEffect(() => {
    setAnalysis(null);
    setHtf2Shadow(undefined);
    setEntryPilot(undefined);
    return subscribeForexStream(
      symbol,
      'analysis',
      (value) => {
        const body = value as (LearnerAnalysis | { status: string }) & {
          htf_ast3_shadow?: typeof htf2Shadow;
          entry_pilot?: ForexEntryPilot;
        };
        setHtf2Shadow(body.htf_ast3_shadow);
        setEntryPilot(body.entry_pilot);
        setAnalysis('levels' in body ? body : null);
      },
      () => {},
    );
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
            .filter((row) => row.status === 'open' && /^hast[234]_/.test(row.strategy ?? ''))
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
  const session = marketSessionDisplay(dashboard.market_session, clock);
  const closedSession = session.status === 'closed';
  const quoteLive =
    !closedSession &&
    quoteState.status === 'live' &&
    clock - quoteHeartbeat < 5000 &&
    quoteState.quote !== null &&
    clock / 1000 - quoteState.quote.time <= 15;
  const visibleQuote = quoteLive || closedSession ? quoteState.quote : null;
  const streamInterrupted =
    clock - quoteHeartbeat >= 5000 ||
    !['connecting', 'connected', 'waiting', 'live', 'stale'].includes(quoteState.status);

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
            <ForexSessionStatus session={dashboard.market_session} now={clock} />
            <span>
              BID{' '}
              <strong className="broker-bid">
                {visibleQuote ? formatPrice(visibleQuote.bid) : '—'}
              </strong>
            </span>
            <span>
              ASK{' '}
              <strong className="broker-ask">
                {visibleQuote ? formatPrice(visibleQuote.ask) : '—'}
              </strong>
            </span>
            <span>
              SPREAD{' '}
              <strong>
                {visibleQuote ? formatPrice(visibleQuote.ask - visibleQuote.bid) : '—'}
              </strong>
            </span>
          </div>
          <span
            className={`broker-stream-state ${quoteLive ? 'is-live' : closedSession && !streamInterrupted ? 'is-closed' : ''}`}
            role="status"
          >
            <span aria-hidden="true" />
            {quoteLive
              ? 'Tick trực tiếp'
              : closedSession && !streamInterrupted
                ? visibleQuote
                  ? `Giá cuối phiên · ${formatTime(visibleQuote.time, true)}`
                  : 'Nghỉ phiên · chờ mở phiên'
                : 'Stream gián đoạn'}
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
          <ForexSessionStatus session={dashboard.market_session} now={clock} />
          <span
            className={`mtf-quote-state ${quoteLive ? 'is-live' : closedSession && !streamInterrupted ? 'is-closed' : ''}`}
          >
            {closedSession && !streamInterrupted
              ? visibleQuote
                ? `Giá cuối phiên · ${formatTime(visibleQuote.time, true)}`
                : 'Nghỉ phiên · chưa có giá'
              : (quoteStatusLabels[
                  quoteState.status === 'live' && !quoteLive ? 'stale' : quoteState.status
                ] ?? 'Stream chưa sẵn sàng')}
          </span>
          <span>
            BID <strong>{visibleQuote ? formatPrice(visibleQuote.bid) : '—'}</strong>
          </span>
          <span>
            ASK <strong>{visibleQuote ? formatPrice(visibleQuote.ask) : '—'}</strong>
          </span>
          <span>
            SPREAD{' '}
            <strong>{visibleQuote ? formatPrice(visibleQuote.ask - visibleQuote.bid) : '—'}</strong>
          </span>
        </div>
      )}
      {!executionOnly && (
        <p className="micro">
          Nến đang chạy ghép từ tick {side.toUpperCase()} Exness theo thời gian thực. Nến live chỉ
          để theo dõi; nến đóng từ REST thay thế khi đồng bộ, MTF và paper không đổi.{' '}
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
        entryIntents={entryPilot?.intents}
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
            ? [
                { price: quoteState.quote!.bid, label: 'BID', color: '#bd7164' },
                { price: quoteState.quote!.ask, label: 'ASK', color: '#2c856c' },
              ]
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
            <span
              className={
                quoteLive && selectedPosition?.status !== 'closed'
                  ? 'is-live'
                  : closedSession && !streamInterrupted
                    ? 'is-closed'
                    : ''
              }
            >
              {selectedPosition?.status === 'closed'
                ? 'Xem lịch sử vị thế · không ghép tick'
                : closedSession && !streamInterrupted
                  ? `${session.label} · ${session.transition}`
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
      {!executionOnly && (
        <ForexEntryPilotPanel key={symbol} symbol={symbol} entryPilot={entryPilot} />
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
