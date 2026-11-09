import { forexSetupGuides, forexTriggerGuides, forexExitGuides } from '../lib/forex-strategy-info';
import { useEffect, useMemo, useRef, useState } from 'react';
import { subscribeForexStream } from '../lib/forex-stream';
import { CheckCircle2, ChevronDown, CircleDashed, CircleX, Clock3, Eye, GitBranch, LayoutGrid, List, Search, X } from 'lucide-react';
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
import { ForexSignalLifecycle } from '../components/ForexSignalLifecycle';
import { signalSetups } from '../lib/forex-entry-lifecycle';
import { shadowPnlText } from '../lib/forex-shadow';
import { feedErrorText, marketSessionDisplay, priceDigits } from '../lib/forex-labels';
import { clockTime, relativeTime, formatTime } from '../lib/time-format';
import { timeZoneLabel } from '../lib/time-zone';
import type { BrokerPosition, ExnessExecution, LearnerAnalysis } from '../types/exness-execution';
import type { ForexEntryPilot, ForexSignalChart } from '../types/forex-entry';
import { estimatedPnl, ticketRole, ticketStatus, type PositionGroup } from '../lib/forex-position-groups';

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
  const failing = frames.find((frame) => frame.error);
  const retryAt = frames
    .map((frame) => frame.retry_at)
    .filter((value): value is string => !!value && new Date(value).getTime() > Date.now())
    .sort()
    .at(-1);

  const errorReason = failing ? feedErrorText(failing.error) : null;
  const retryCountdown = retryAt ? relativeTime(retryAt) : null;

  const title = disconnected
    ? 'Mất kết nối API · dữ liệu lưu gần nhất'
    : !dashboard.market.worker_online
      ? 'Worker nến offline · chờ chạy lại để tải bù'
      : errorReason
        ? errorReason
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
    !errorReason &&
    !downloading &&
    !paused &&
    !progress?.pending_bars;

  const bodyContent = (
    <div className="space-y-3 pt-2 text-xs">
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        {frames.map((frame) => {
          const count = frame.remaining_intervals;
          const isErr = !!frame.error;
          return (
            <div
              key={frame.timeframe}
              className={`rounded-lg border px-2.5 py-1.5 ${
                isErr
                  ? 'border-bear/30 bg-bear/5 text-bear'
                  : count && count > 0
                    ? 'border-amber-400/25 bg-amber-400/5 text-amber-200'
                    : 'border-border-subtle bg-surface-2/60 text-text-secondary'
              }`}
            >
              <div className="flex items-center justify-between font-mono font-semibold uppercase">
                <span>{frame.timeframe}</span>
                {isErr ? (
                  <span className="text-[10px] uppercase tracking-wider text-bear">Lỗi</span>
                ) : count && count > 0 ? (
                  <span className="text-[10px] text-amber-300">Cần kiểm tra</span>
                ) : count === null ? (
                  <span className="text-[10px] text-text-muted">Chờ khởi tạo</span>
                ) : (
                  <span className="text-[10px] text-text-muted">Đã kiểm tra</span>
                )}
              </div>
              {frame.error && frame.error !== failing?.error && <p className="mb-0 mt-1 break-words font-sans leading-relaxed">{feedErrorText(frame.error)}</p>}
              <div className="mt-0.5 font-mono text-[11px] tabular-nums">
                {count === null
                  ? 'Chưa khởi tạo'
                  : `${count.toLocaleString('vi-VN')} khoảng nến`}
              </div>
            </div>
          );
        })}
        <div className="rounded-lg border border-border-subtle bg-surface-2/60 px-2.5 py-1.5 text-text-secondary">
          <div className="flex items-center justify-between font-mono font-semibold">
            <span>MTF 15m</span>
            <span className="text-[10px] text-text-muted">Chờ phân tích</span>
          </div>
          <div className="mt-0.5 font-mono text-[11px] tabular-nums text-text-primary">
            {progress
              ? `${progress.pending_bars.toLocaleString('vi-VN')} nến`
              : '—'}
          </div>
        </div>
      </div>

      {errorReason && <p className="m-0 break-words leading-relaxed text-bear">{errorReason}{retryAt ? ` · Thử lại ${formatTime(retryAt, true)}` : ''}</p>}

      <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1 rounded-lg border border-border-subtle bg-surface-2/30 px-3 py-2 text-[11px] text-text-muted">
        <div>
          <span>Đã phân tích đến: </span>
          <strong className="font-mono text-text-primary">
            {progress?.last_processed_candle != null
              ? `${formatTime(progress.last_processed_candle)} (giờ mở)`
              : 'Chưa có'}
          </strong>
        </div>
        <div>
          <span>Tính bù khi cả 3 khung đồng bộ và hợp lệ. Khoảng cần kiểm tra có thể trùng giờ nghỉ phiên, không đồng nghĩa mất nến. Setup mang nhãn “Chạy bù”; không đặt lệnh cho tín hiệu đã bỏ lỡ.</span>
        </div>
      </div>
    </div>
  );

  if (compact) {
    return (
      <details
        className={`group/catchup my-1.5 overflow-hidden rounded-xl border text-xs transition-colors ${
          healthy
            ? 'border-border-base bg-surface-1/90 text-text-secondary'
            : errorReason
              ? 'border-bear/30 bg-bear/5 text-text-primary'
              : 'border-amber-400/25 bg-amber-400/5 text-text-primary'
        }`}
        role="status"
        aria-live="polite"
      >
        <summary className="flex min-h-10 cursor-pointer list-none flex-wrap items-center gap-x-3 gap-y-1.5 px-3.5 py-2 select-none hover:bg-surface-2/40 transition-colors [&::-webkit-details-marker]:hidden">
          <div className="flex items-center gap-2 font-medium">
            <span
              className={`size-2 shrink-0 rounded-full ${
                healthy
                  ? 'bg-bull shadow-[0_0_8px_rgba(16,185,129,0.4)]'
                  : errorReason
                    ? 'bg-bear shadow-[0_0_8px_rgba(244,63,94,0.4)]'
                    : 'bg-amber-400 shadow-[0_0_8px_rgba(251,191,36,0.4)]'
              }`}
              aria-hidden="true"
            />
            <span className={errorReason ? 'text-bear font-semibold' : !healthy ? 'text-amber-200 font-semibold' : 'text-text-primary'}>
              {errorReason && !disconnected && dashboard.market.worker_online ? 'Kết nối nến Exness gián đoạn' : title}
            </span>
          </div>

          {retryCountdown && (
            <span className="rounded-md border border-bear/20 bg-bear/10 px-2 py-0.5 font-mono text-[11px] text-bear tabular-nums">
              Tự thử lại · {retryCountdown}
            </span>
          )}

          {!healthy && (frames.length > 0 || progress) && (
            <div className="flex flex-wrap items-center gap-1.5 font-mono text-[11px] text-text-muted" aria-label="Khoảng nến cần kiểm tra và nến MTF chờ phân tích">
              {frames.map((frame) => (
                <span
                  key={frame.timeframe}
                  className={`rounded px-1.5 py-0.5 uppercase ${
                    (frame.remaining_intervals ?? 0) > 0
                      ? 'bg-amber-400/10 text-amber-200'
                      : 'bg-surface-3 text-text-muted'
                  }`}
                >
                  {frame.timeframe}: {frame.remaining_intervals ?? '—'}
                </span>
              ))}
              {progress?.pending_bars ? (
                <span className="rounded bg-sky-400/10 px-1.5 py-0.5 text-sky-300">
                  MTF: {progress.pending_bars}
                </span>
              ) : null}
            </div>
          )}

          <div className="ml-auto inline-flex items-center gap-1.5 text-xs text-text-muted">
            <span className="hidden sm:inline">Chi tiết</span>
            <span aria-hidden="true" className="transition-transform group-open/catchup:rotate-180">
              ⌄
            </span>
          </div>
        </summary>

        <div className="border-t border-border-subtle bg-surface-1/40 px-3.5 pb-3">
          {bodyContent}
        </div>
      </details>
    );
  }

  return (
    <section
      className={`panel forex-catchup min-w-0 rounded-xl border p-4 space-y-3 ${
        healthy
          ? 'border-border-base bg-surface-1'
          : errorReason
            ? 'border-bear/30 bg-surface-1 text-text-primary'
            : 'border-amber-400/30 bg-surface-1 text-text-primary'
      }`}
      role="status"
      aria-live="polite"
    >
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border-subtle pb-2.5">
        <div className="flex items-center gap-2">
          <span
            className={`size-2.5 rounded-full ${
              healthy ? 'bg-bull' : errorReason ? 'bg-bear' : 'bg-amber-400'
            }`}
            aria-hidden="true"
          />
          <strong className="text-sm">{title}</strong>
        </div>
        {retryCountdown && (
          <span className="rounded-md border border-bear/20 bg-bear/10 px-2 py-0.5 font-mono text-xs text-bear">
            Tự thử lại: {retryCountdown}
          </span>
        )}
      </div>
      {bodyContent}
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
  quote_gap: ['Mất liên tục giá', 'Không có quote quá 60 giây'],
  quote_out_of_order: ['Quote ngược thời gian', 'Hủy để tránh suy đoán nhánh chạm trước'],
  stream_disconnected: ['Mất luồng giá', 'Hủy chờ; kết nối lại không tạo lệnh đuổi'],
  h1_data_gap: ['Thiếu nến H1', 'Quá 120s đầu giờ chưa có nến H1 mới'],
  level_crossed_during_gap: ['Giá chạm mức khi mất tick', 'Không giả định đã khớp; không vào đuổi'],
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
    <span className={`gen-badge gen-${Math.min(tree.generation ?? 0, 3)} inline-flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 text-xs font-medium bg-surface-3 text-text-secondary`}>
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
    <div className="bot-breakdown space-y-3">
      <div className="flex min-w-0 flex-wrap items-center justify-between gap-2">
        <span className="text-xs font-semibold text-text-primary">
          {confirmed.length ? `${confirmed.length} cây có tín hiệu` : 'Chưa có cây nào đạt tín hiệu'}
        </span>
        <input
          type="search"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="Tìm theo ID hoặc tên setup…"
          aria-label="Tìm cây"
          className="min-h-8 rounded-lg border border-border-base bg-surface-2 px-2.5 text-xs text-text-primary placeholder:text-text-muted focus:border-border-active focus:outline-none"
        />
      </div>

      {confirmed.length > 0 && (
        <div className="space-y-1.5">
          {confirmed.map(([name, row]) => (
            <div
              key={name}
              className="flex min-w-0 flex-wrap items-center gap-2 rounded-lg border border-bull/20 bg-bull/5 px-3 py-2 text-xs text-text-secondary"
            >
              <GenBadge tree={lineage.get(name)} />
              <strong className="text-text-primary" title={name}>
                {treeLabel(row, name).full}
              </strong>
              <span className="font-semibold text-bull">{row.side?.toUpperCase()}</span>
              <span className="font-mono text-text-muted">·</span>
              <span className="font-mono tabular-nums">SL: {row.stop ?? '—'}</span>
              <span className="font-mono tabular-nums">Mức: {row.level ?? '—'}</span>
              {note(row) && <small className="text-text-muted">({note(row)})</small>}
            </div>
          ))}
        </div>
      )}

      {needle ? (
        <div className="table-wrap max-h-60 overflow-y-auto rounded-lg border border-border-subtle bg-surface-2/40">
          <table className="w-full border-collapse text-left text-xs">
            <thead className="sticky top-0 bg-surface-3 text-text-muted">
              <tr>
                <th className="px-3 py-2 font-medium">Cây V4</th>
                <th className="px-3 py-2 font-medium">Trạng thái</th>
                <th className="px-3 py-2 font-medium">Chi tiết</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border-subtle font-mono text-[11px]">
              {matches.slice(0, 30).map(([name, row]) => (
                <tr key={name} className="hover:bg-surface-3/50">
                  <td className="px-3 py-1.5">
                    <span title={name} className="text-text-primary">
                      {treeLabel(row, name).full}
                    </span>
                  </td>
                  <td className="px-3 py-1.5">
                    <span
                      className={
                        row.status === 'confirmed' ? 'text-bull font-semibold' : 'text-text-muted'
                      }
                    >
                      {row.status === 'confirmed' ? 'xác nhận' : 'loại'}
                    </span>
                  </td>
                  <td className="px-3 py-1.5 text-text-secondary">
                    {row.status === 'confirmed'
                      ? note(row)
                      : (rejectLabels[row.reason ?? '']?.[0] ?? row.reason ?? '—')}
                  </td>
                </tr>
              ))}
              {!matches.length && (
                <tr>
                  <td colSpan={3} className="px-3 py-4 text-center text-text-muted">
                    Không có cây khớp.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="grid gap-2 sm:grid-cols-2">
          {sorted.map(([code, names]) => {
            const [title, hint] = rejectLabels[code] ?? [code, 'Mã lý do chưa có mô tả'];
            const expanded = open === code;
            return (
              <div
                key={code}
                className="rounded-lg border border-border-subtle bg-surface-2/40 p-2.5 text-xs transition-colors hover:border-border-base"
              >
                <button
                  type="button"
                  className="flex w-full items-center justify-between text-left select-none"
                  onClick={() => setOpen(expanded ? null : code)}
                  aria-expanded={expanded}
                >
                  <div className="min-w-0 pr-2">
                    <div className="flex items-center gap-2">
                      <strong className="text-text-primary">{title}</strong>
                      <span className="font-mono text-[10px] text-text-muted">({code})</span>
                    </div>
                    <p className="mb-0 mt-0.5 line-clamp-1 text-[11px] text-text-muted">{hint}</p>
                  </div>
                  <span className="inline-flex shrink-0 items-center rounded-md bg-surface-3 px-2 py-0.5 font-mono text-[11px] font-semibold text-text-secondary tabular-nums">
                    {names.length}
                  </span>
                </button>
                {expanded && (
                  <div className="mt-2.5 border-t border-border-subtle pt-2">
                    <div className="flex flex-wrap gap-1 font-mono text-[11px]">
                      {names.map((name) => (
                        <span
                          key={name}
                          className="rounded bg-surface-3 px-1.5 py-0.5 text-text-muted"
                          title={name}
                        >
                          {name}
                        </span>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            );
          })}
        </div>
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
  inside_bar: 'Nến trong / Inside Bar',
  nr7: 'Biên độ hẹp 7 nến NR7',
  asia_break: 'Phá biên phiên Á UTC',
  london_orb_h1: 'Phá biên mở phiên London H1',
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
  const [closedGroups, setClosedGroups] = useState<Set<string>>(new Set());
  const [selected, setSelected] = useState<string | null>(null);
  const detailRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!selected) return;
    detailRef.current?.focus({ preventScroll: true });
    detailRef.current?.scrollIntoView({ block: 'nearest', behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth' });
  }, [selected]);

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
    setClosedGroups((prev) => {
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
        className={`parent-chip ${found ? '' : 'is-gone'} inline-flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 text-xs font-medium bg-surface-3 text-text-secondary`}
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
    <div className="lineage min-w-0 space-y-4">
      <div className="lineage-gens flex flex-wrap items-stretch gap-3" aria-label="Thế hệ">
        {Array.from({ length: maxGen + 1 }, (_, gen) => {
          const count = trees.filter((tree) => (tree.generation ?? 0) === gen).length;
          return (
            <div key={gen} className={`lineage-gen-card ${count ? '' : 'is-empty'} flex min-w-36 items-center gap-3 rounded-xl border border-sky-400/20 bg-linear-to-br from-sky-400/10 to-surface-2 px-4 py-3 [&.is-empty]:opacity-60`}>
              <span className={`gen-badge gen-${Math.min(gen, 3)} inline-flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 text-xs font-medium bg-surface-3 text-text-secondary`}>G{gen}</span>
              <div><strong className="block font-mono text-xl tabular-nums text-text-primary">{count}</strong>
              <small className="text-xs text-text-muted">{gen === 0 ? 'hạt giống' : 'cây con'}</small></div>
            </div>
          );
        })}
        {maxGen === 0 && (
          <p className="micro lineage-hint flex min-w-0 flex-1 basis-64 items-center rounded-xl border border-dashed border-border-active bg-surface-2/40 px-4 py-3 text-xs leading-relaxed text-text-muted">
            Chưa có cây con. Cần cây bố mẹ đủ ≥10 mẫu forward đã đóng với R trung bình dương
            {nextEvolutionAt ? ` · chu kỳ kế tiếp ${formatTime(nextEvolutionAt)}` : ''}.
          </p>
        )}
      </div>

      <div className="lineage-toolbar flex min-w-0 flex-wrap items-center gap-3 rounded-xl border border-border-subtle bg-surface-2/40 p-3">
        <input
          className="min-h-10 w-full min-w-0 rounded-lg border border-border-active bg-surface-1 px-3 text-sm text-text-primary placeholder:text-text-muted focus-visible:outline-2 focus-visible:outline-sky-400 sm:max-w-80"
          type="search"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="Tìm ID, setup H1, trigger M15…"
          aria-label="Tìm cây"
        />
        <div className="lineage-seg flex flex-wrap items-center gap-1 [&>button]:rounded [&>button]:px-2 [&>button]:py-1 [&>button]:text-xs [&>button]:text-text-secondary [&>button:hover]:bg-surface-3 [&>button[aria-pressed=true]]:bg-surface-3 [&>button[aria-pressed=true]]:text-bull border-b border-border-subtle py-1" role="group" aria-label="Lọc">
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
        <div className="lineage-seg flex flex-wrap items-center gap-1 [&>button]:rounded [&>button]:px-2 [&>button]:py-1 [&>button]:text-xs [&>button]:text-text-secondary [&>button:hover]:bg-surface-3 [&>button[aria-pressed=true]]:bg-surface-3 [&>button[aria-pressed=true]]:text-bull border-b border-border-subtle py-1" role="group" aria-label="Nhóm theo">
          {(Object.keys(groupTitles) as GroupBy[]).map((key) => (
            <button
              type="button"
              key={key}
              aria-pressed={groupBy === key}
              onClick={() => {
                setGroupBy(key);
                setClosedGroups(new Set());
              }}
            >
              {groupTitles[key]}
            </button>
          ))}
        </div>
      </div>

      {detail && (
        <div ref={detailRef} tabIndex={-1} className="lineage-detail min-w-0 scroll-mt-32 space-y-4 rounded-xl border border-sky-400/30 bg-surface-1 p-4 shadow-lg focus-visible:outline-2 focus-visible:outline-sky-400" role="region" aria-label="Chi tiết cây">
          <div className="lineage-detail-head mb-3 flex min-w-0 flex-wrap items-center justify-between gap-2 [&_p]:text-xs [&_p]:text-text-muted">
            <span className={`gen-badge gen-${Math.min(detail.generation ?? 0, 3)} inline-flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 text-xs font-medium bg-surface-3 text-text-secondary`}>
              G{detail.generation ?? 0}
            </span>
            <code className="lineage-detail-id min-w-0 break-all font-mono text-xs text-text-primary">{detail.id}</code>
            {detail.eligible ? (
              <span className="gen-badge gen-ok inline-flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 text-xs font-medium bg-surface-3 text-text-secondary">✓ Đủ chuẩn đặt lệnh</span>
            ) : (
              <span className="lineage-status-tag inline-flex items-center rounded-md bg-amber-400/10 px-2 py-1 text-xs text-amber-300">{detail.resolved < 10 ? `Chờ gom mẫu (${detail.resolved}/10)` : 'Chưa đủ chuẩn fitness'}</span>
            )}
            <button
              type="button"
              className="secondary lineage-close-btn inline-flex items-center justify-center gap-1.5 rounded-md border border-border-active bg-surface-2 px-3 py-1.5 text-xs text-text-secondary hover:bg-surface-3 text-xs text-text-muted hover:text-text-primary"
              onClick={() => setSelected(null)}
            >
              ✕ Đóng
            </button>
          </div>

          <div className="lineage-path min-w-0 space-y-2 rounded-md bg-surface-2 p-3">
            <span className="lineage-path-label text-xs text-text-muted">Dòng dõi:</span>
            <div className="lineage-path-flow flex min-w-0 flex-wrap items-center gap-2 overflow-x-auto">
              <span className="lineage-path-node inline-flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 text-xs font-medium border border-border-base [&.is-self]:border-bull">
                <small>Bố mẹ</small>
                {detail.parents?.length ? (
                  detail.parents.map((id) => chip(id, `p-${id}`))
                ) : (
                  <span className="muted text-text-muted">Gốc (khởi nguyên)</span>
                )}
              </span>
              <span className="lineage-path-arrow">→</span>
              <span className="lineage-path-node is-self inline-flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 text-xs font-medium border border-border-base [&.is-self]:border-bull">
                <small>Cây này</small>
                <strong>{treeLabel(detail, detail.id).full}</strong>
              </span>
              <span className="lineage-path-arrow">→</span>
              <span className="lineage-path-node inline-flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 text-xs font-medium border border-border-base [&.is-self]:border-bull">
                <small>Thế hệ con</small>
                {children.get(detail.id)?.length ? (
                  children.get(detail.id)!.map((child) => chip(child.id, `c-${child.id}`))
                ) : (
                  <span className="muted text-text-muted">Chưa sinh con</span>
                )}
              </span>
            </div>
          </div>

          <div className="lineage-steps-grid grid gap-3 sm:grid-cols-2 xl:grid-cols-5 [&>div]:space-y-3 [&>div]:border-t-sky-400/40 [&>div:nth-child(2)]:border-t-amber-400/40 [&>div:nth-child(3)]:border-t-teal-400/40 [&>div:nth-child(4)]:border-t-blue-400/40 [&>div:nth-child(5)]:border-t-rose-400/40 [&_.step-content]:space-y-2 [&_strong]:block [&_strong]:text-sm [&_strong]:text-text-primary [&_p]:text-xs [&_p]:leading-relaxed [&_p]:text-text-muted">
            <div className="lineage-step-card min-w-0 rounded-lg border border-border-base bg-surface-1 p-3 [&.is-empty]:opacity-60">
              <span className="step-num inline-flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 text-xs font-medium bg-bull/10 text-bull">1</span>
              <div className="step-content min-w-0 flex-1 space-y-1">
                <strong>Bối cảnh H4</strong>
                <span className="step-tag inline-flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 text-xs font-medium bg-surface-3">Xu hướng EMA50</span>
                <p>
                  Nến H4 đóng thuận chiều EMA50 (trên EMA50 chỉ Buy, dưới EMA50 chỉ Sell). Ngược
                  trend thì bỏ qua.
                </p>
              </div>
            </div>

            <div className="lineage-step-card min-w-0 rounded-lg border border-border-base bg-surface-1 p-3 [&.is-empty]:opacity-60">
              <span className="step-num inline-flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 text-xs font-medium bg-bull/10 text-bull">2</span>
              <div className="step-content min-w-0 flex-1 space-y-1">
                <strong>Setup H1</strong>
                <span className="step-tag inline-flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 text-xs font-medium bg-surface-3">{label(detail.h1_setup)}</span>
                <p>
                  {forexSetupGuides[detail.h1_setup] ??
                    'Cấu trúc hình thành trên khung H1 đã đóng.'}{' '}
                  SL đặt tại mức vô hiệu cấu trúc H1 + 0.25 ATR buffer (hiệu lực 4h).
                </p>
              </div>
            </div>

            <div className="lineage-step-card min-w-0 rounded-lg border border-border-base bg-surface-1 p-3 [&.is-empty]:opacity-60">
              <span className="step-num inline-flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 text-xs font-medium bg-bull/10 text-bull">3</span>
              <div className="step-content min-w-0 flex-1 space-y-1">
                <strong>Trigger M15</strong>
                <span className="step-tag inline-flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 text-xs font-medium bg-surface-3">{label(detail.m15_trigger)}</span>
                <p>
                  {forexTriggerGuides[detail.m15_trigger] ??
                    'Xác nhận thời điểm vào lệnh tại nến M15 đóng.'}{' '}
                  M15 chỉ trigger, không quyết định SL/TP.
                </p>
              </div>
            </div>

            <div className="lineage-step-card min-w-0 rounded-lg border border-border-base bg-surface-1 p-3 [&.is-empty]:opacity-60">
              <span className="step-num inline-flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 text-xs font-medium bg-bull/10 text-bull">4</span>
              <div className="step-content min-w-0 flex-1 space-y-1">
                <strong>Vào lệnh</strong>
                <span className="step-tag inline-flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 text-xs font-medium bg-surface-3">Market</span>
                <p>
                  Gửi lệnh Market ngay khi nến M15 trigger đóng. Tín hiệu chỉ có hiệu lực 2 phút sau
                  khi nến đóng; quá hạn thì bỏ, không đuổi giá.
                </p>
              </div>
            </div>

            <div className="lineage-step-card min-w-0 rounded-lg border border-border-base bg-surface-1 p-3 [&.is-empty]:opacity-60">
              <span className="step-num inline-flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 text-xs font-medium bg-bull/10 text-bull">5</span>
              <div className="step-content min-w-0 flex-1 space-y-1">
                <strong>Quản lý lệnh</strong>
                <span className="step-tag inline-flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 text-xs font-medium bg-surface-3">{label(detail.exit_policy)}</span>
                <p>
                  {forexExitGuides[detail.exit_policy] ?? 'Quản lý rủi ro theo cấu trúc HTF.'} TP
                  runner bám theo cản H1/H4 gần nhất (R/R tối thiểu 1.5R).
                </p>
                {!!Object.keys(detail.exit_params ?? {}).length && (
                  <p className="micro text-xs leading-relaxed text-text-muted">
                    Tham số tiến hoá:{' '}
                    {Object.entries(detail.exit_params ?? {})
                      .map(([name, value]) => `${paramLabels[name] ?? name} ${value}`)
                      .join(' · ')}
                  </p>
                )}
              </div>
            </div>
          </div>

          <div className="lineage-stats-strip grid grid-cols-2 gap-2 text-xs text-text-muted sm:grid-cols-3 xl:grid-cols-6">
            <div className="lineage-stat-box min-w-0 rounded-md border border-border-subtle bg-surface-1 p-3 [&_strong]:block [&_strong]:font-mono [&_strong]:text-lg [&_strong]:text-text-primary">
              <span>Tín hiệu</span>
              <strong>{detail.signals}</strong>
            </div>
            <div className="lineage-stat-box min-w-0 rounded-md border border-border-subtle bg-surface-1 p-3 [&_strong]:block [&_strong]:font-mono [&_strong]:text-lg [&_strong]:text-text-primary">
              <span>Đang chạy</span>
              <strong>{detail.pending}</strong>
            </div>
            <div className="lineage-stat-box min-w-0 rounded-md border border-border-subtle bg-surface-1 p-3 [&_strong]:block [&_strong]:font-mono [&_strong]:text-lg [&_strong]:text-text-primary">
              <span>Đã đóng</span>
              <strong>{detail.resolved}</strong>
            </div>
            <div className="lineage-stat-box min-w-0 rounded-md border border-border-subtle bg-surface-1 p-3 [&_strong]:block [&_strong]:font-mono [&_strong]:text-lg [&_strong]:text-text-primary">
              <span>Bị loại</span>
              <strong>{detail.excluded}</strong>
            </div>
            <div className="lineage-stat-box min-w-0 rounded-md border border-border-subtle bg-surface-1 p-3 [&_strong]:block [&_strong]:font-mono [&_strong]:text-lg [&_strong]:text-text-primary">
              <span>R trung bình</span>
              <strong
                className={
                  detail.mean_r == null ? '' : detail.mean_r >= 0 ? "positive text-bull" : "negative text-bear"
                }
              >
                {detail.mean_r == null
                  ? '—'
                  : `${detail.mean_r >= 0 ? '+' : ''}${detail.mean_r.toFixed(2)}R`}
              </strong>
            </div>
            <div className="lineage-stat-box min-w-0 rounded-md border border-border-subtle bg-surface-1 p-3 [&_strong]:block [&_strong]:font-mono [&_strong]:text-lg [&_strong]:text-text-primary">
              <span>Cận dưới 95%</span>
              <strong
                className={
                  detail.lower_r == null ? '' : detail.lower_r >= 0 ? "positive text-bull" : "negative text-bear"
                }
              >
                {detail.lower_r == null
                  ? '—'
                  : `${detail.lower_r >= 0 ? '+' : ''}${detail.lower_r.toFixed(2)}R`}
              </strong>
            </div>
          </div>
          {detail.sides && (
            <p className="micro text-xs leading-relaxed text-text-muted">
              Long: {detail.sides.long.resolved} mẫu · {detail.sides.long.mean_r?.toFixed(2) ?? '—'}
              R{' · '}Short: {detail.sides.short.resolved} mẫu ·{' '}
              {detail.sides.short.mean_r?.toFixed(2) ?? '—'}R
            </p>
          )}
        </div>
      )}

      {!ordered.length && <p className="micro text-xs leading-relaxed text-text-muted">Không có cây khớp bộ lọc.</p>}

      {ordered.map(([key, rows]) => {
        const open = !closedGroups.has(key) || !!needle;
        return (
          <div key={key} className="lineage-group min-w-0 overflow-hidden rounded-xl border border-border-base bg-surface-2/20">
            <button type="button" className="flex min-h-14 w-full flex-wrap items-center gap-2 bg-surface-2/60 px-4 py-3 text-left transition-colors hover:bg-surface-3 focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-sky-400" aria-expanded={open} onClick={() => toggle(key)}>
              <span className="lineage-caret">{open ? '▾' : '▸'}</span>
              <span className="lineage-group-title flex flex-wrap items-center gap-2 text-sm font-semibold text-text-primary">{label(key)}</span>
              <span className="lineage-count-pill inline-flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 text-xs font-medium bg-bull/10 text-bull">{rows.length} cây</span>
              <span className="lineage-group-stats ml-auto flex flex-wrap items-center gap-2 text-xs text-text-muted">
                {sum(rows, 'signals')} tín hiệu · {sum(rows, 'resolved')} đã đóng
              </span>
            </button>
            {open && (
              <ul className="lineage-rows-list grid gap-2 p-3 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4">
                {rows.map((row) => (
                  <li key={row.id}>
                    <button
                      type="button"
                      aria-pressed={selected === row.id}
                      className={`lineage-row-btn ${selected === row.id ? 'is-selected' : ''} flex h-full w-full min-w-0 flex-wrap items-center gap-2 rounded-lg border border-border-base border-t-sky-400/40 bg-surface-1 p-3 text-left text-xs transition-colors hover:border-sky-400/50 hover:bg-surface-2 focus-visible:outline-2 focus-visible:outline-sky-400 [&.is-selected]:border-sky-400 [&.is-selected]:bg-sky-400/5`}
                      onClick={() => setSelected(row.id)}
                    >
                      <span className={`gen-badge gen-${Math.min(row.generation ?? 0, 3)} inline-flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 text-xs font-medium bg-surface-3 text-text-secondary`}>
                        G{row.generation ?? 0}
                      </span>
                      <span
                        className="lineage-row-id min-w-0 flex-1 break-words text-sm font-semibold text-text-primary"
                        title={`${treeLabel(row, row.id).full}\n${row.id}`}
                      >
                        {treeLabel(row, row.id).title}
                      </span>

                      <div className="lineage-tags-inline flex w-full flex-wrap items-center gap-1.5 text-xs [&_.tag-h1]:bg-amber-400/10 [&_.tag-h1]:text-amber-300 [&_.tag-m15]:bg-sky-400/10 [&_.tag-m15]:text-sky-300 [&_.tag-exit]:bg-teal-400/10 [&_.tag-exit]:text-teal-300">
                        <span className="lineage-tag tag-h1 inline-flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 text-xs font-medium bg-surface-3 text-text-secondary">H1 {label(row.h1_setup)}</span>
                        <span className="lineage-tag tag-m15 inline-flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 text-xs font-medium bg-surface-3 text-text-secondary">M15 {label(row.m15_trigger)}</span>
                        <span className="lineage-tag tag-exit inline-flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 text-xs font-medium bg-surface-3 text-text-secondary">{label(row.exit_policy)}</span>
                      </div>

                      <span className="lineage-row-meta mt-1 min-w-0 flex-1 text-xs text-text-muted">
                        {row.parents?.length ? (
                          <span className="lineage-parents-brief text-xs text-text-muted">
                            từ {row.parents.map(shortTreeId).join(' × ')}
                          </span>
                        ) : (
                          <span className="muted text-text-muted">hạt giống gốc</span>
                        )}
                        {children.get(row.id)?.length ? (
                          <span className="lineage-child-badge inline-flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 text-xs font-medium bg-bull/10 text-bull">
                            {' '}
                            · {children.get(row.id)!.length} con
                          </span>
                        ) : null}
                      </span>

                      <span className="lineage-row-metric font-mono font-mono tabular-nums">
                        {row.resolved ? (
                          <span
                            className={
                              row.mean_r != null && row.mean_r >= 0 ? "positive text-bull" : "negative text-bear"
                            }
                          >
                            {row.mean_r != null && row.mean_r >= 0 ? '+' : ''}
                            {row.mean_r?.toFixed(2)}R
                          </span>
                        ) : (
                          <span className="muted text-text-muted">{row.signals} tín hiệu</span>
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
  limit: 'Limit sweep M15',
  stop: 'Stop đóng M15',
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
  limit_setup_mid: ['wait_bars'],
  stop_trigger_break: ['stop_buffer_atr', 'wait_bars'],
  limit_or_stop: ['wait_bars'],
};
const entryPolicyLabels: Record<string, string> = {
  market: 'Market',
  limit_setup_mid: 'Limit ảo',
  stop_trigger_break: 'Stop ảo',
  limit_or_stop: 'Hybrid · Limit/Stop ảo',
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

const POLICY_ORDER = ['market', 'limit_setup_mid', 'stop_trigger_break', 'limit_or_stop'] as const;

const NO_R_LABELS: Record<string, string> = {
  expired_0r: 'lỡ lệnh (0R)',
  expired: 'hết hạn (không tính)',
  ambiguous: 'không rõ thứ tự chạm',
  excluded: 'bị loại',
  skipped: 'bỏ qua',
  invalid: 'không hợp lệ',
};

/** Tách mẫu đã kết thúc nhưng không có R (lỡ lệnh, mơ hồ, bị loại). Mẫu đang chạy tách dòng riêng bên dưới. */
function finishedNoR(counts: Record<string, number> = {}): string[] {
  const rows: string[] = [];
  for (const [status, count] of Object.entries(counts)) {
    if (!count || ['resolved', 'expired_0r', 'open', 'pending', 'waiting'].includes(status)) continue;
    rows.push(`${count} ${NO_R_LABELS[status] ?? status}`);
  }
  return rows;
}

function runningCount(counts: Record<string, number> = {}): number {
  return (counts.open ?? 0) + (counts.pending ?? 0) + (counts.waiting ?? 0);
}

export function ForexEntryPilotPanel({
  entryPilot,
  symbol,
}: {
  entryPilot?: ForexEntryPilot;
  symbol: string;
}) {
  const [entrySearch, setEntrySearch] = useState('');
  const [pilotPage, setPilotPage] = useState(0);
  const [treeFilter, setTreeFilter] = useState('all');
  const [treeSort, setTreeSort] = useState('qualified');
  const [policyFilter, setPolicyFilter] = useState('all');
  const [activityFilter, setActivityFilter] = useState('all');
  const [generationFilter, setGenerationFilter] = useState('all');
  const [treeView, setTreeView] = useState<'matrix' | 'list'>('matrix');
  const [pickedTree, setPickedTree] = useState<string | null>(null);
  const digits = priceDigits(symbol);
  const price = (value: number | null) => (value == null ? '—' : value.toFixed(digits));
  const pilot = entryPilot?.pilot;
  const trees = pilot?.trees ?? [];
  const intents = entryPilot?.intents ?? [];
  const waiting = intents.filter((intent) => intent.status === 'waiting').length;
  const samples = trees.reduce((total, tree) => total + tree.samples_count, 0);
  const resolved = trees.reduce((total, tree) => total + tree.resolved_count, 0);
  const comparison = pilot?.comparison;
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
  const treeCode = (id: string) => {
    let length = 6;
    while (length < id.length && trees.some((tree) => tree.id !== id && tree.id.slice(-length) === id.slice(-length))) length += 1;
    return id.slice(-length);
  };
  const policyTone: Record<string, string> = {
    market: 'border-sky-400/25 bg-sky-400/10 text-sky-300',
    limit_setup_mid: 'border-teal-400/25 bg-teal-400/10 text-teal-300',
    stop_trigger_break: 'border-amber-400/25 bg-amber-400/10 text-amber-300',
    limit_or_stop: 'border-indigo-400/25 bg-indigo-400/10 text-indigo-300',
  };
  const populationFilters = [
    { value: 'all', title: 'Tất cả', count: trees.length, tone: 'text-text-primary' },
    { value: 'eligible', title: 'Có thể sinh con', count: trees.filter((tree) => tree.eligible).length, tone: 'text-emerald-300' },
    { value: 'waiting', title: 'Chờ tín hiệu', count: trees.filter((tree) => !tree.ready && !tree.eligible && tree.samples_count === 0).length, tone: 'text-text-secondary' },
    { value: 'collecting', title: 'Đang gom mẫu', count: trees.filter((tree) => !tree.ready && !tree.eligible && tree.samples_count > 0).length, tone: 'text-sky-300' },
    { value: 'not_eligible', title: 'Đủ mẫu · chưa đạt', count: trees.filter((tree) => tree.ready && !tree.eligible).length, tone: 'text-rose-300' },
  ];
  const policyOrder = [...new Set([...Object.keys(entryPolicyLabels), ...policies])];
  const policyFilters = [
    { value: 'all', title: 'Mọi cách vào', count: trees.length },
    ...policyOrder
      .filter((policy) => policies.has(policy))
      .map((policy) => ({ value: policy, title: policyLabel(policy), count: trees.filter((tree) => tree.entry_policy === policy).length })),
  ];
  const treeStatus = (tree: (typeof trees)[number]) => tree.eligible
    ? { label: 'Có thể sinh con', tone: 'text-emerald-300', Icon: CheckCircle2 }
    : tree.ready
      ? { label: 'Đủ mẫu · chưa đạt', tone: 'text-rose-300', Icon: CircleX }
      : tree.samples_count === 0
        ? { label: 'Chờ tín hiệu', tone: 'text-text-muted', Icon: CircleDashed }
        : { label: 'Đang gom mẫu', tone: 'text-sky-300', Icon: Clock3 };
  const minOutcomes = Math.max(1, pilot?.min_outcomes ?? 10);
  const filtersActive = treeFilter !== 'all' || policyFilter !== 'all' || activityFilter !== 'all' || generationFilter !== 'all' || needle !== '';
  const resetFilters = () => { setEntrySearch(''); setTreeFilter('all'); setPolicyFilter('all'); setActivityFilter('all'); setGenerationFilter('all'); setPilotPage(0); setPickedTree(null); };
  const filteredTrees = trees.filter((tree) => {
    const matchesState = treeFilter === 'all'
      || (treeFilter === 'eligible' && tree.eligible)
      || (treeFilter === 'waiting' && !tree.ready && !tree.eligible && tree.samples_count === 0)
      || (treeFilter === 'collecting' && !tree.ready && !tree.eligible && tree.samples_count > 0)
      || (treeFilter === 'not_eligible' && tree.ready && !tree.eligible);
    const matchesActivity = activityFilter === 'all' || (activityFilter === 'active' ? runningCount(tree.status_counts) > 0 : runningCount(tree.status_counts) === 0);
    const matchesGeneration = generationFilter === 'all' || (generationFilter === 'children' ? (tree.generation ?? 0) > 0 : (tree.generation ?? 0) === 0);
    return matchesState && matchesActivity && matchesGeneration && (policyFilter === 'all' || tree.entry_policy === policyFilter) && (!needle || [
          tree.id,
          tree.control_tree_id,
          tree.h1_setup,
          tree.m15_trigger,
          tree.exit_policy,
          tree.entry_policy,
          policyLabel(tree.entry_policy),
          label(tree.h1_setup),
          label(tree.m15_trigger),
          label(tree.exit_policy),
          ...(tree.parents ?? []),
        ].some((value) => value.toLowerCase().includes(needle)));
  }).sort((left, right) => {
    if (treeSort === 'qualified' && Boolean(left.ready) !== Boolean(right.ready)) return left.ready ? -1 : 1;
    if (treeSort === 'fitness' || treeSort === 'qualified') {
      if (left.mean_r == null) return right.mean_r == null ? 0 : 1;
      if (right.mean_r == null) return -1;
      return right.mean_r - left.mean_r || right.resolved_count - left.resolved_count;
    }
    if (treeSort === 'outcomes') return right.resolved_count - left.resolved_count;
    if (treeSort === 'generation') return (right.generation ?? 0) - (left.generation ?? 0);
    return 0;
  });

  const pageSize = 14;
  const totalPages = Math.max(1, Math.ceil(filteredTrees.length / pageSize));
  const pageIndex = Math.min(pilotPage, totalPages - 1);
  const pagedTrees = filteredTrees.slice(pageIndex * pageSize, (pageIndex + 1) * pageSize);
  const treeDetail = (tree: (typeof trees)[number]) => {
    const treeIntents = intents.filter((intent) => intent.strategy === tree.id);
    const filled = treeIntents.filter((intent) => intent.broker_fills?.length).length;
    return (
      <>
                  <dl className="grid min-w-0 gap-x-6 gap-y-4 border-t border-border-subtle bg-surface-2/50 px-3 py-4 leading-relaxed sm:grid-cols-2 lg:grid-cols-3 [&_dt]:text-text-muted">
                    <div><dt>ID cây</dt><dd className="mt-1 break-all font-mono text-text-primary">{tree.id}</dd></div>
                    <div><dt>Thoát lệnh</dt><dd className="mt-1 text-text-primary">{label(tree.exit_policy)}</dd></div>
                    <div><dt>Mẫu shadow</dt><dd className="mt-1 tabular-nums text-text-primary">{tree.samples_count} mẫu · {tree.resolved_count} kết quả hợp lệ</dd></div>
                    <div><dt>Tiến hoá</dt><dd className="mt-1 text-text-primary">{tree.eligible ? 'Đủ điều kiện làm cha/mẹ' : tree.ready ? 'Chưa đủ điều kiện sinh con' : tree.samples_count === 0 ? 'Chờ tín hiệu đầu tiên' : 'Đang gom mẫu'} · {tree.resolved_count} kết quả hợp lệ, tối thiểu {minOutcomes}.</dd></div>
                    <div><dt>Execution demo · dữ liệu trả về</dt><dd className="mt-1 text-text-primary">{filled > 0 ? `${filled} setup có broker xác nhận khớp; không suy ra vị thế còn mở.` : treeIntents.length > 0 ? `${treeIntents.length} setup · chưa có xác nhận khớp broker.` : 'Chưa có dữ liệu execution cho cây này.'}{tree.entry_live_sides?.length === 1 && <span className="block text-amber-300">Limit Short chỉ shadow</span>}</dd></div>
                    <div><dt>Tham số entry</dt><dd className="mt-1 break-words font-mono text-text-primary">{Object.entries(tree.entry_params ?? {}).map(([name, value]) => `${name}=${value}`).join(' · ') || 'Không có tham số entry'}</dd></div>
                    <div><dt>Phả hệ</dt><dd className="mt-1 break-all text-text-primary">Cha/mẹ: {tree.parents?.join(', ') || 'Cây gốc'}</dd></div>
                  </dl>
                  {finishedNoR(tree.status_counts).length > 0 && <p className="px-3 py-2 text-amber-300">Không tính fitness: {finishedNoR(tree.status_counts).join(' · ')}</p>}
      </>
    );
  };
  const matrixGroups = [...filteredTrees.reduce((map, tree) => map.set(tree.h1_setup, [...(map.get(tree.h1_setup) ?? []), tree]), new Map<string, typeof trees>())];
  const pickedTreeData = filteredTrees.find((tree) => tree.id === pickedTree);
  const states = Object.entries(pilot?.counts ?? {});
  return (
    <>
      {entryPilot && pilot && pilot.status !== 'ready' && (
        <div className="learning-section-card entry-pilot is-blocked min-w-0 space-y-3 rounded-md border border-border-subtle bg-surface-1 p-3 space-y-3 [&.is-blocked]:border-amber-400/30" role="status">
          <div className="card-header mb-3 flex min-w-0 flex-wrap items-center justify-between gap-2 [&_p]:text-xs [&_p]:text-text-muted">
            <div>
              <h4>AST V4 · cây độc lập</h4>
              <p className="muted-desc text-xs leading-relaxed text-text-muted">
                {pilot.status === 'not_initialized'
                  ? 'Chờ worker khởi tạo quần thể độc lập. Không dùng dữ liệu Market thay thế.'
                  : 'Quần thể độc lập chưa sẵn sàng. Entry thử nghiệm bị chặn.'}
              </p>
            </div>
            <span className="entry-pilot-state is-off flex min-w-0 flex-wrap items-center gap-2 text-xs">Chưa sẵn sàng</span>
          </div>
          {(entryPilot.error || pilot.error) && (
            <p className="entry-pilot-error rounded border border-bear/30 bg-bear/10 p-3 text-xs text-bear">{entryPilot.error || pilot.error}</p>
          )}
        </div>
      )}
      {entryPilot && pilot?.trees && (
        <div className="learning-section-card entry-pilot min-w-0 space-y-4 rounded-xl border border-border-base bg-surface-1 p-4">
          <div className="card-header flex min-w-0 flex-wrap items-center justify-between gap-2 border-b border-border-subtle pb-3">
            <div>
              <h4 className="flex items-center gap-2 text-base font-semibold text-text-primary">
                <GitBranch size={18} className="shrink-0 text-text-secondary" aria-hidden="true" />
                AST V4 · {pilot.tree_count} cây độc lập{' '}
                · G{pilot.generation ?? 0}
              </h4>
              <p className="muted-desc mt-1 text-xs leading-relaxed text-text-muted">
                {pilot.note ||
                  'Mỗi cây có entry và fitness riêng. Cùng setup chỉ chọn một cây đủ điều kiện để chạy demo; không đổi entry thay thế. Con kế thừa hoặc đột biến entry/tham số, không nhân bốn.'}
              </p>
            </div>
            <span
              className={`inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-semibold ${
                entryPilot.enabled
                  ? 'border border-bull/30 bg-bull/10 text-bull'
                  : 'border border-border-base bg-surface-2 text-text-muted'
              }`}
              title={
                entryPilot.enabled
                  ? 'V4 được bật; vẫn cần quyền execution demo hiện có'
                  : 'Execution V4 đang tắt'
              }
            >
              <span className={`h-2 w-2 rounded-full ${entryPilot.enabled ? 'bg-bull' : 'bg-text-muted'}`} />
              {entryPilot.enabled ? 'Đang bật' : 'Đang tắt'}
            </span>
          </div>

          <div className="entry-pilot-kpis flex flex-wrap gap-x-8 gap-y-3">
            <div>
              <small className="block text-xs text-text-muted">Đang canh giá</small>
              <strong className={`block font-mono text-xl ${waiting ? 'text-amber-300' : 'text-text-primary'}`}>{waiting}</strong>
              <span className="text-[11px] text-text-muted">{intents.length} setup gần nhất</span>
            </div>
            <div>
              <small className="block text-xs text-text-muted">Mẫu shadow từng cây</small>
              <strong className="block font-mono text-xl text-text-primary">{samples}</strong>
              <span className="text-[11px] text-text-muted">{resolved} đã đóng</span>
            </div>
          </div>

          <div className="grid grid-cols-2 gap-x-6 gap-y-3 border-y border-border-subtle py-3 sm:grid-cols-4" aria-label="Phân bố entry của quần thể hiện tại">
            {Object.entries(pilot.policy_stats ?? {}).map(([policy, stats]) => (
              <div key={policy}>
                <small className="block text-xs font-semibold text-text-primary">{policyLabel(policy)}</small>
                <strong className={"block font-mono text-xl " + rTone(stats.mean_r)}>{signedR(stats.mean_r)}</strong>
                <span className="block text-xs text-text-secondary">{stats.tree_count} cây · {stats.resolved_count} mẫu đóng</span>
              </div>
            ))}
          </div>

          <div className="entry-pilot-meta flex min-w-0 flex-wrap items-center justify-between gap-2 text-xs">
            <div className="entry-pilot-states flex min-w-0 flex-wrap items-center gap-2 text-xs" aria-label="Trạng thái mẫu shadow">
              {states.length ? (
                states.map(([state, count]) => (
                  <span key={state} className="inline-flex shrink-0 items-center gap-1.5 rounded-lg border border-border-subtle bg-surface-2 px-2.5 py-1 text-xs text-text-secondary">
                    {entryStateLabels[state] ?? state}: <b className="font-mono text-text-primary">{count}</b>
                  </span>
                ))
              ) : (
                <span className="muted text-text-muted">{samples === 0 ? 'Chưa có mẫu shadow' : `${samples} mẫu shadow · ${resolved} kết quả hợp lệ`}</span>
              )}
            </div>
            <p className="text-xs text-text-muted">
              Shadow, không phải giao dịch broker · R sau chi phí, chưa swap
              {pilot.since
                ? ` · cutoff ${formatTime(pilot.since, true)}, không nhận fitness từ cây nguồn`
                : ''}
            </p>
          </div>

          <details className="group rounded-xl border border-border-subtle bg-surface-2/20 p-3 text-xs leading-relaxed">
            <summary className="flex cursor-pointer items-center justify-between font-semibold text-text-primary">
              <span>Hướng dẫn & Cơ chế entry V4</span>
              <ChevronDown size={16} aria-hidden="true" className="text-text-muted group-open:rotate-180" />
            </summary>
            <p className="mt-2 text-text-muted">
              Mỗi cây dùng cách vào lệnh trong bảng bên dưới. Limit và Stop là mức ảo do bot canh qua tick stream, không phải pending trên sàn. Hybrid chờ nến M15 đóng xác nhận; nhánh xác nhận trước gửi Market lên Exness Demo.
            </p>
            <ol className="mt-3 space-y-2 border-t border-border-subtle pt-3">
              {policies.has('market') ? (
                <li className="flex flex-col gap-0.5">
                  <strong className="text-sky-300">Market · vào trực tiếp</strong>
                  <span className="text-text-muted">
                    Kích hoạt tại quote hợp lệ đầu tiên sau tín hiệu; cửa sổ tối đa 120 giây, vẫn
                    chịu giới hạn tuổi setup H1 và kiểm tra an toàn trước khi gửi lệnh.
                  </span>
                </li>
              ) : null}
              {policies.has('limit_setup_mid') ? (
                <li className="flex flex-col gap-0.5">
                  <strong className="text-teal-300">Limit Long · quét râu xác nhận (Short chỉ shadow)</strong>
                  <span className="text-text-muted">
                    Chiều Mua (Long) chờ nến M15 quét thủng biên dưới vùng cản H1 (level - 0,05 ATR) và đóng nến
                    bật trở lại trên biên trên (level + 0,05 ATR), vào tại Open nến sau. Nếu M15 đóng thủng hẳn dưới
                    biên cản thì huỷ setup ngay. Chiều Bán (Short) chỉ chạy mô phỏng shadow đối chứng, không bao giờ gửi lệnh thật.
                  </span>
                </li>
              ) : null}
              {policies.has('stop_trigger_break') ? (
                <li className="flex flex-col gap-0.5">
                  <strong className="text-amber-300">Stop · chờ M15 đóng xác nhận</strong>
                  <span className="text-text-muted">
                    Nến M15 tiếp theo phải đóng vượt đỉnh (Mua) hoặc thủng đáy (Bán) nến trigger
                    thêm 0,05 ATR H1, cố định tại lúc tạo tín hiệu. Chỉ chạm râu nến không vào.
                    Vào tại quote hợp lệ đầu tiên sau khi nhận nến đóng; quá 120 giây thì bỏ,
                    không vượt thời hạn setup H1.
                  </span>
                </li>
              ) : null}
              {policies.has('limit_or_stop') ? (
                <li className="flex flex-col gap-0.5">
                  <strong className="text-amber-300">Hybrid · Limit sweep + Stop đóng M15</strong>
                  <span className="text-text-muted">
                    Stop cần M15 đóng vượt đỉnh/đáy nến trigger thêm 0,05 ATR H1. Limit chỉ áp dụng cho
                    Mua: quét dưới vùng H1 (level ± 0,05 ATR) rồi đóng trên biên trên. Nến xác nhận trước
                    thắng; cùng nến xác nhận cả hai thì ưu tiên Stop. Bán chỉ vào nếu Stop thắng.
                  </span>
                </li>
              ) : null}
              {Array.from(policies).some((policy) => policy !== 'market') && (
                <li className="flex flex-col gap-0.5">
                  <strong className="text-rose-300">Hết hạn</strong>
                  <span className="text-text-muted">
                    Chờ tối đa{' '}
                    <b className="text-text-primary">
                      {showValue(waitBars, (n) => `${n} nến M15 (${n * 15} phút)`, 'wait_bars')}
                    </b>{' '}
                    và không quá 4h15 kể từ setup H1. Hết hạn mà chưa chạm thì bỏ setup.
                  </span>
                </li>
              )}
            </ol>
            <p className="micro mt-3 text-xs leading-relaxed text-text-muted">
              Mỗi cây sở hữu H1, M15, entry và exit riêng. Sinh con xét kết quả riêng từng cây, không chờ các entry khác.
              {!paramsVary && ' Tất cả cây đang dùng cùng tham số entry.'}
            </p>
          </details>

          <details className="group rounded-xl border border-border-subtle bg-surface-2/20 p-3 text-xs leading-relaxed">
            <summary className="flex cursor-pointer items-center justify-between font-semibold text-text-primary">
              <span className="flex items-center gap-2">
                <span>Canh giá & kết quả broker</span>
                <span className="rounded-full bg-surface-3 px-2 py-0.5 font-mono text-[11px] text-text-secondary">{intents.length} setup</span>
              </span>
              <span className="text-text-muted transition-transform group-open:rotate-180">▾</span>
            </summary>
            {intents.length ? (
              <div className="mt-3 table-wrap min-w-0 max-w-full overflow-x-auto overscroll-x-contain rounded-lg border border-border-subtle bg-surface-1">
                <table className="learner-clean-table w-full border-collapse whitespace-nowrap text-xs">
                  <thead>
                    <tr>
                      <th>Cây / hướng</th>
                      <th>Trạng thái</th>
                      <th className="num font-mono tabular-nums text-right">Limit / Stop ảo</th>
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
                          <span className={`side-chip ${intent.side} inline-flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 text-xs font-medium bg-surface-3 text-text-secondary`}>
                            {intent.side === 'long' ? 'Mua' : 'Bán'}
                          </span>
                        </td>
                        <td>
                          <span
                            className={`entry-state-chip state-${intent.broker_fills?.length ? 'completed' : intent.status} inline-flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 text-xs font-medium bg-surface-3 text-text-secondary`}
                          >
                            {intent.broker_fills?.length
                              ? 'Broker đã xác nhận khớp'
                              : (entryStateLabels[intent.status] ?? intent.status)}
                          </span>
                        </td>
                        <td className="num font-mono font-mono tabular-nums text-right font-mono tabular-nums">
                          {price(intent.limit_price)} / {price(intent.stop_price)}
                        </td>
                        <td>{formatTime(intent.expires_at, true)}</td>
                        <td>{winnerLabels[intent.winner ?? ''] ?? '—'}</td>
                        <td className="font-mono font-mono tabular-nums">
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
                            <span className="muted text-text-muted">Chưa xác nhận</span>
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
              <p className="entry-pilot-empty grid min-h-24 place-content-center gap-2 p-4 text-center text-xs text-text-muted">
                Chưa có setup nào được canh giá. Bot sẽ ghi lại ở đây khi một cây V4 bắt được tín
                hiệu M15.
              </p>
            )}
          </details>

          <div className="space-y-4 border-t border-border-base pt-5">
            <div className="flex flex-wrap items-baseline justify-between gap-2">
              <h5 className="flex items-center gap-2 text-lg font-semibold tracking-tight text-text-primary"><GitBranch size={19} className="text-sky-300" aria-hidden="true" />Quần thể tiến hoá</h5>
              <span aria-live="polite" className="text-xs tabular-nums text-text-secondary">{filteredTrees.length} / {trees.length} cây · entry tiến hóa độc lập</span>
            </div>
            <div className="flex flex-wrap gap-2" role="group" aria-label="Lọc nhanh quần thể">
              {populationFilters.map((filter) => (
                <button key={filter.value} type="button" aria-pressed={treeFilter === filter.value} onClick={() => { setTreeFilter(filter.value); setPilotPage(0); }} className={`inline-flex min-h-10 items-center gap-2 rounded-full border px-3.5 text-xs font-medium transition-colors focus-visible:outline-2 focus-visible:outline-sky-400 ${treeFilter === filter.value ? 'border-sky-400/50 bg-sky-400/15 text-text-primary' : 'border-border-base bg-surface-1 text-text-secondary hover:border-border-active hover:bg-surface-2'}`}>
                  {filter.title}<span className={`font-mono font-semibold tabular-nums ${filter.tone}`}>{filter.count}</span>
                </button>
              ))}
            </div>
            <div className="flex flex-wrap items-center gap-2" role="group" aria-label="Lọc cách vào lệnh">
              <span className="mr-1 text-xs text-text-muted">Cách vào lệnh</span>
              {policyFilters.map((filter) => {
                const on = policyFilter === filter.value;
                return (
                  <button key={filter.value} type="button" aria-pressed={on} onClick={() => { setPolicyFilter(filter.value); setPilotPage(0); }} className={`inline-flex min-h-10 items-center gap-2 rounded-md border px-3 text-xs font-semibold transition-colors focus-visible:outline-2 focus-visible:outline-sky-400 ${on ? (policyTone[filter.value] ?? 'border-border-active bg-surface-3 text-text-primary') : 'border-border-base bg-surface-1 text-text-secondary hover:bg-surface-2'}`}>
                    {filter.title}<span className="font-mono tabular-nums opacity-80">{filter.count}</span>
                  </button>
                );
              })}
            </div>
            <div className="entry-pilot-toolbar grid min-w-0 grid-cols-2 items-end gap-3 text-xs sm:grid-cols-3 lg:grid-cols-[minmax(14rem,1fr)_repeat(3,9.5rem)_auto_auto]">
            <label className="col-span-2 flex min-w-0 flex-col gap-1.5 sm:col-span-3 lg:col-span-1">
              <span className="text-text-secondary">Tìm cây hoặc cha/mẹ</span>
            <span className="relative block">
            <Search size={15} aria-hidden="true" className="pointer-events-none absolute left-3 top-3 text-text-muted" />
            <input
              type="search"
              value={entrySearch}
              onChange={(e) => { setEntrySearch(e.target.value); setPilotPage(0); }}
              placeholder="Tìm cấu trúc, entry, ID hoặc cha/mẹ…"
              aria-label="Lọc cây V4"
              className="min-h-10 w-full min-w-0 rounded-md border border-border-base bg-surface-2 pl-9 pr-3 text-xs text-text-primary placeholder:text-text-secondary focus-visible:outline-2 focus-visible:outline-sky-400"
            />
            </span>
            </label>
            <label className="flex min-w-0 flex-col gap-1.5">
              <span className="text-text-secondary">Shadow</span>
              <select aria-label="Shadow đang chạy" value={activityFilter} onChange={(event) => { setActivityFilter(event.target.value); setPilotPage(0); }} className="min-h-10 w-full min-w-0 rounded-md border border-border-active bg-surface-1 px-2 text-text-primary focus-visible:outline-2 focus-visible:outline-sky-400">
                <option value="all">Mọi trạng thái</option>
                <option value="active">Có shadow mở/chờ</option>
                <option value="idle">Không có shadow</option>
              </select>
            </label>
            <label className="flex min-w-0 flex-col gap-1.5">
              <span className="text-text-secondary">Thế hệ</span>
              <select aria-label="Thế hệ cây" value={generationFilter} onChange={(event) => { setGenerationFilter(event.target.value); setPilotPage(0); }} className="min-h-10 w-full min-w-0 rounded-md border border-border-active bg-surface-1 px-2 text-text-primary focus-visible:outline-2 focus-visible:outline-sky-400">
                <option value="all">Gốc và con</option>
                <option value="roots">Cây gốc</option>
                <option value="children">Thế hệ con</option>
              </select>
            </label>
            <label className="flex min-w-0 flex-col gap-1.5">
              <span className="text-text-secondary">Sắp xếp</span>
              <select aria-label="Sắp xếp cây" value={treeSort} onChange={(event) => { setTreeSort(event.target.value); setPilotPage(0); }} className="min-h-10 w-full min-w-0 rounded-md border border-border-active bg-surface-1 px-2 text-text-primary focus-visible:outline-2 focus-visible:outline-sky-400">
                <option value="qualified">Đủ mẫu trước · Mean R</option>
                <option value="default">Thứ tự quần thể</option>
                <option value="fitness">Mean R cao nhất</option>
                <option value="outcomes">Nhiều kết quả nhất</option>
                <option value="generation">Thế hệ cao nhất</option>
              </select>
            </label>
            <div className="col-span-2 flex justify-self-start rounded-md border border-border-base bg-surface-1 p-0.5 sm:col-span-1" role="group" aria-label="Kiểu hiển thị">
              {([['matrix', 'Ma trận', LayoutGrid], ['list', 'Danh sách', List]] as const).map(([value, title, Icon]) => (
                <button key={value} type="button" aria-pressed={treeView === value} onClick={() => setTreeView(value)} className={`inline-flex min-h-9 items-center gap-1.5 rounded px-3 text-xs font-medium focus-visible:outline-2 focus-visible:outline-sky-400 ${treeView === value ? 'bg-surface-3 text-text-primary' : 'text-text-secondary hover:bg-surface-2'}`}>
                  <Icon size={14} aria-hidden="true" />{title}
                </button>
              ))}
            </div>
            {filtersActive && <button type="button" onClick={resetFilters} className="inline-flex min-h-10 items-center gap-1.5 rounded-md border border-border-active px-3 text-text-secondary hover:bg-surface-2 focus-visible:outline-2 focus-visible:outline-sky-400"><X size={14} aria-hidden="true" />Xoá lọc</button>}
            </div>
            <p className="text-xs leading-relaxed text-text-muted">Màu nhãn phân biệt cách vào lệnh. Mean R là kết quả shadow, không phải lợi nhuận broker; đủ mẫu không đồng nghĩa đã có lợi thế.</p>
          </div>
          {samples === 0 && <p className="max-w-prose text-xs leading-relaxed text-text-secondary"><strong className="text-text-primary">Đang chờ mẫu shadow đầu tiên.</strong> Mean R “—” là chưa có kết quả, không phải 0R. Shadow không phải lịch sử khớp lệnh broker.</p>}
          {treeView === 'matrix' && filteredTrees.length > 0 && (
            <div className="grid min-w-0 gap-3 md:grid-cols-2 2xl:grid-cols-3" aria-label="Ma trận cấu trúc H1 theo cách vào lệnh">
              {matrixGroups.map(([setup, group]) => (
                <section key={setup} className="min-w-0 rounded-xl border border-border-base bg-surface-1 p-3">
                  <h6 className="flex items-baseline justify-between gap-2 text-sm font-semibold text-text-primary"><span>{label(setup)}</span><span className="font-mono text-[11px] font-normal tabular-nums text-text-muted">{group.length} cây</span></h6>
                  <div className="mt-3 grid grid-cols-1 gap-2 min-[360px]:grid-cols-2">
                    {group.map((tree) => {
                      const status = treeStatus(tree);
                      const active = runningCount(tree.status_counts);
                      const percent = Math.min(100, Math.round((tree.resolved_count / minOutcomes) * 100));
                      const on = pickedTree === tree.id;
                      return (
                        <button key={tree.id} type="button" title={tree.id} aria-label={`${label(tree.h1_setup)} · ${policyLabel(tree.entry_policy)} · ${signedR(tree.mean_r)} · ${status.label}`} aria-pressed={on} onClick={() => setPickedTree(on ? null : tree.id)} className={`entry-tree min-w-0 rounded-lg border p-2.5 text-left transition-colors focus-visible:outline-2 focus-visible:outline-sky-400 ${on ? 'ring-2 ring-sky-400/60 ' : ''}${policyTone[tree.entry_policy] ?? 'border-border-base bg-surface-3 text-text-secondary'}`}>
                          <span className="flex items-center justify-between gap-1 text-xs font-semibold"><span className="truncate">{policyLabel(tree.entry_policy)}</span><span className="font-mono text-[11px] font-normal opacity-80">#{treeCode(tree.id)} · G{tree.generation ?? 0}</span></span>
                          <span className="mt-1 block text-[10px] uppercase tracking-wide opacity-70">Mean R</span>
                          <strong className={'block font-mono text-lg font-semibold tabular-nums ' + (tree.mean_r == null ? 'text-text-muted' : rTone(tree.mean_r) === 'positive' ? 'text-bull' : 'text-bear')}>{signedR(tree.mean_r)}</strong>
                          <span className="mt-1 block h-1 overflow-hidden rounded-full bg-surface-3" role="progressbar" aria-label={`Mẫu đóng ${tree.resolved_count} trên ${minOutcomes}`} aria-valuemin={0} aria-valuemax={minOutcomes} aria-valuenow={Math.min(tree.resolved_count, minOutcomes)}><span className="block h-full bg-current" style={{ width: `${percent}%` }} /></span>
                          <span className="mt-1.5 flex items-center justify-between gap-1 text-[11px] tabular-nums text-text-secondary"><span>{tree.resolved_count}/{minOutcomes}</span>{active > 0 && <span className="text-sky-300">{active} mở/chờ</span>}</span>
                          <span className="mt-1 block truncate text-[11px] text-text-secondary">M15: {label(tree.m15_trigger)}</span>
                          <span className={`mt-1 flex items-center gap-1 text-[11px] font-medium ${status.tone}`}><status.Icon size={12} aria-hidden="true" />{status.label}</span>
                        </button>
                      );
                    })}
                  </div>
                  {pickedTreeData && group.some((tree) => tree.id === pickedTreeData.id) && (
                    <div className="mt-3 min-w-0 rounded-xl border border-sky-400/30 bg-surface-1 text-xs leading-relaxed text-text-secondary" role="region" aria-label="Chi tiết cây đã chọn">
                    <div className="flex flex-wrap items-center justify-between gap-2 px-4 py-3">
                    <strong className="text-sm text-text-primary">{label(pickedTreeData.h1_setup)} · {policyLabel(pickedTreeData.entry_policy)} · M15: {label(pickedTreeData.m15_trigger)}</strong>
                    <button type="button" onClick={() => setPickedTree(null)} className="inline-flex min-h-9 items-center gap-1 rounded-md border border-border-base px-2.5 text-text-secondary hover:bg-surface-2 focus-visible:outline-2 focus-visible:outline-sky-400"><X size={14} aria-hidden="true" />Đóng</button>
                    </div>
                    {treeDetail(pickedTreeData)}
                    </div>
                    )}
                </section>
              ))}
            </div>
          )}
          {treeView === 'list' && (<>
          <div aria-hidden="true" className="hidden grid-cols-[minmax(0,1.4fr)_minmax(0,1.1fr)_5rem_5rem_10rem_1rem] gap-3 rounded-t-lg border border-border-base bg-surface-2 px-4 py-3 text-xs font-medium text-text-secondary lg:grid">
            <span>Cây · Cấu trúc H1</span><span>Entry · Xác nhận M15</span><span className="text-right">Mean R</span><span className="text-right">Mẫu đóng</span><span className="text-right">Tiến hoá · Shadow</span><span />
          </div>
          <ul aria-label="Các cây V4 độc lập" className="!mt-0 min-w-0 overflow-hidden rounded-lg border border-border-base lg:rounded-t-none lg:border-t-0">
            {pagedTrees.map((tree) => {
              const code = treeCode(tree.id);
              const active = runningCount(tree.status_counts);
              const evolutionLabel = tree.eligible ? 'Có thể sinh con' : tree.ready ? 'Đủ mẫu · chưa đạt' : tree.samples_count === 0 ? 'Chờ tín hiệu' : 'Đang gom mẫu';
              const evolutionTone = tree.eligible ? 'text-emerald-300' : tree.ready ? 'text-rose-300' : tree.samples_count === 0 ? 'text-text-muted' : 'text-sky-300';
              const EvolutionIcon = tree.eligible ? CheckCircle2 : tree.ready ? CircleX : tree.samples_count === 0 ? CircleDashed : Clock3;
              return (
              <li key={tree.id} className="entry-tree min-w-0 border-b border-border-subtle last:border-b-0 odd:bg-surface-2/30 transition-colors hover:bg-surface-2 focus-within:bg-surface-2">
                <details className="group/tree text-xs text-text-secondary">
                <summary className="grid min-h-20 min-w-0 cursor-pointer list-none grid-cols-[minmax(0,1fr)_auto_0.875rem] items-start gap-x-2 gap-y-2 px-3 py-3 focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-sky-400 sm:px-4 lg:grid-cols-[minmax(0,1.4fr)_minmax(0,1.1fr)_5rem_5rem_10rem_1rem] lg:items-center lg:gap-x-3 group-open/tree:bg-surface-2 [&::-webkit-details-marker]:hidden">
                <span className="min-w-0">
                  <strong className="block text-sm font-semibold leading-snug text-text-primary">{label(tree.h1_setup)}</strong>
                  <span className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-text-muted">
                    <span className="font-mono text-text-secondary" title={tree.id}>#{code}</span>
                    <span className={`inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[11px] font-medium ${(tree.generation ?? 0) > 0 ? 'bg-indigo-400/10 text-indigo-300' : 'bg-surface-3 text-text-secondary'}`}><GitBranch size={11} aria-hidden="true" />G{tree.generation ?? 0}<span className="sr-only">{(tree.generation ?? 0) > 0 ? ' · Cây con' : ' · Cây gốc'}</span></span>
                  </span>
                  <span className="sr-only">Chi tiết cây #{code} · {tree.id}</span>
                </span>
                <span className="col-span-2 col-start-1 row-start-2 flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 lg:col-span-1 lg:col-auto lg:row-auto lg:block">
                  <span className={`inline-flex rounded-md border px-2 py-1 text-xs font-semibold ${policyTone[tree.entry_policy] ?? 'border-border-base bg-surface-3 text-text-secondary'}`}>{policyLabel(tree.entry_policy)}</span>
                  <span className="text-xs leading-snug text-text-secondary lg:mt-1 lg:block">M15: {label(tree.m15_trigger)}</span>
                </span>
                <span className="col-start-2 row-start-1 text-right lg:col-auto lg:row-auto">
                  <span className="block text-xs text-text-muted lg:sr-only">Mean R</span>
                  <strong className={'block font-mono text-base font-semibold tabular-nums ' + (tree.mean_r == null ? 'text-text-muted' : rTone(tree.mean_r))}>{signedR(tree.mean_r)}</strong>
                </span>
                <span className="col-start-1 row-start-3 text-xs tabular-nums lg:col-auto lg:row-auto lg:text-right">
                  <span className="text-text-muted lg:sr-only">Mẫu đóng </span><span className="font-mono font-semibold text-text-primary">{tree.resolved_count}</span><span className="text-text-muted"> / {pilot.min_outcomes ?? 10}</span>
                  <span className="hidden text-[11px] text-text-muted lg:mt-1 lg:block">{tree.ready ? 'Đủ mẫu' : 'Chưa đủ mẫu'}</span>
                </span>
                <span className="col-span-2 col-start-1 row-start-4 flex min-w-0 flex-wrap items-center justify-between gap-2 border-t border-border-subtle pt-2 lg:col-span-1 lg:col-auto lg:row-auto lg:block lg:border-t-0 lg:pt-0 lg:text-right">
                  <span className={`inline-flex items-center gap-1.5 text-xs font-medium ${evolutionTone}`}><EvolutionIcon size={14} className="shrink-0" aria-hidden="true" />{evolutionLabel}</span>
                  <span className="block text-[11px] tabular-nums text-text-muted lg:mt-1">{active > 0 ? <span className="text-sky-300">{active} shadow mở/chờ</span> : 'Không có shadow mở/chờ'}</span>
                </span>
                <ChevronDown size={14} className="col-start-3 row-start-1 mt-1 shrink-0 text-text-muted transition-transform group-open/tree:rotate-180 lg:col-auto lg:row-auto lg:mt-0" aria-hidden="true" />
                </summary>
                  {treeDetail(tree)}
                </details>
              </li>
            ); })}
          </ul>
          </>)}
          {!filteredTrees.length && (
            <p className="min-h-24 p-6 text-center text-xs text-text-muted">
              Không có cây phù hợp. Thử đổi trạng thái hoặc từ khoá tìm kiếm.
              <button type="button" className="ml-2 min-h-10 rounded border border-border-active px-3 py-1 focus-visible:outline-2 focus-visible:outline-sky-400" onClick={resetFilters}>Đặt lại lọc cây</button>
            </p>
          )}
          {treeView === 'list' && totalPages > 1 && (
            <div className="flex flex-wrap items-center justify-between gap-2 border-t border-border-subtle pt-3 text-xs">
              <span className="text-text-muted">Trang {pageIndex + 1} / {totalPages} ({filteredTrees.length} cây)</span>
              <div className="flex items-center gap-1.5">
                <button
                  type="button"
                  disabled={pageIndex === 0}
                  onClick={() => setPilotPage((p) => Math.max(0, p - 1))}
                  className="inline-flex min-h-10 items-center rounded-md border border-border-base bg-surface-2 px-3 text-xs text-text-secondary hover:bg-surface-3 focus-visible:outline-2 focus-visible:outline-sky-400 disabled:cursor-not-allowed disabled:opacity-40"
                >
                  ← Trước
                </button>
                <button
                  type="button"
                  disabled={pageIndex >= totalPages - 1}
                  onClick={() => setPilotPage((p) => Math.min(totalPages - 1, p + 1))}
                  className="inline-flex min-h-10 items-center rounded-md border border-border-base bg-surface-2 px-3 text-xs text-text-secondary hover:bg-surface-3 focus-visible:outline-2 focus-visible:outline-sky-400 disabled:cursor-not-allowed disabled:opacity-40"
                >
                  Sau →
                </button>
              </div>
            </div>
          )}
        </div>
      )}
    </>
  );
}

export function ForexStrategyPage({
  dashboard,
  disconnected,
  executions = [],
  positionGroups = [],
  selectedPosition,
  executionOnly = false,
  chartScope = 'setup',
  includeReplay = true,
  onClosePosition,
  onSelectPosition,
  onQuote,
}: {
  dashboard: ForexDashboard;
  disconnected: boolean;
  executions?: BrokerPosition[];
  positionGroups?: PositionGroup[];
  selectedPosition?: BrokerPosition;
  executionOnly?: boolean;
  chartScope?: 'ticket' | 'setup';
  includeReplay?: boolean;
  onClosePosition?: (position: BrokerPosition) => void;
  onSelectPosition?: (id?: string) => void;
  onQuote?: (quote: { symbol: string; bid: number; ask: number; time: number } | null) => void;
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
  const recordedSignals = useMemo(() => [
    ...Object.entries(analysis?.strategies ?? {}).flatMap(([strategy, row]) => {
      const zone = row.captured_zone;
      return zone?.trigger_at == null ? [] : [{ strategy, side: zone.side,
        available_at: zone.trigger_at, status: zone.sample_status ?? 'recorded' }];
    }),
    ...(entryPilot?.pilot.signals ?? []),
  ], [analysis, entryPilot]);
  const setups = useMemo(() => signalSetups(entryPilot?.intents ?? [], recordedSignals), [entryPilot, recordedSignals]);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [drawerTab, setDrawerTab] = useState<'positions' | 'setups' | 'shadow'>('positions');
  const [shadowSelection, setShadowSelection] = useState<{ symbol: string; id: string } | null>(null);
  const shadowTrades = (entryPilot?.pilot.shadow_trades ?? []).filter((trade) => trade.symbol === symbol);
  const selectedShadow = shadowSelection?.symbol === symbol
    ? shadowTrades.find((trade) => trade.id === shadowSelection.id) : undefined;
  const [drawerFocusId, setDrawerFocusId] = useState<string>();
  useEffect(() => {
    setDrawerOpen(false);
    setDrawerTab('positions');
    setDrawerFocusId(undefined);
    setShadowSelection(null);
  }, [symbol]);
  const [setupSelection, setSetupSelection] = useState<{ symbol: string; id: string } | null>(null);
  const selectedSetup = setupSelection?.symbol === symbol
    ? setups.find((setup) => setup.id === setupSelection.id) : undefined;
  const [v4Selection, setV4Selection] = useState({ setup: '', strategy: '' });
  const v4Signals = selectedSetup?.signals.filter((signal) => signal.strategy.startsWith('hast4_')) ?? [];
  const v4Strategy = v4Signals.find((signal) => selectedSetup?.id === v4Selection.setup &&
    signal.strategy === v4Selection.strategy)?.strategy ?? v4Signals[0]?.strategy;
  const signalKey = v4Strategy ? `${symbol}:${selectedSetup?.time}:${v4Strategy}` : '';
  const [signalSnapshot, setSignalSnapshot] = useState<{ key: string; data?: ForexSignalChart; error?: string }>({ key: '' });
  const v4Chart = signalSnapshot.key === signalKey ? signalSnapshot.data : undefined;
  useEffect(() => {
    if (!v4Strategy || !selectedSetup?.time) return;
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 15_000);
    let active = true;
    setSignalSnapshot({ key: signalKey });
    const query = new URLSearchParams({ symbol, strategy: v4Strategy, available_at: String(selectedSetup.time) });
    fetch(`/api/workspaces/exness_forex/learning/signal-chart?${query}`, { signal: controller.signal })
      .then(async (response) => {
        const body = await response.json();
        if (!response.ok) throw new Error(body.detail ?? 'Không tải được snapshot V4');
        if (body.symbol !== symbol || body.strategy !== v4Strategy || body.available_at !== selectedSetup.time)
          throw new Error('Snapshot không khớp tín hiệu đã chọn');
        if (active) setSignalSnapshot({ key: signalKey, data: body });
      })
      .catch((error) => {
        if (active) setSignalSnapshot({ key: signalKey, error: error.name === 'AbortError'
          ? 'Tải snapshot quá hạn; chọn lại setup để thử lại' : error.message });
      })
      .finally(() => clearTimeout(timer));
    return () => { active = false; controller.abort(); clearTimeout(timer); };
  }, [symbol, v4Strategy, selectedSetup?.time, signalKey]);
  useEffect(() => {
    setAnalysis(null);
    setHtf2Shadow(undefined);
    setEntryPilot(undefined);
    // Setup chỉ cần summary (~0.2s); `analysis` stream mất 5-7s, nên tải summary trước, stream ghi đè khi tới.
    const controller = new AbortController();
    // Cache replay chạy đồng bộ trong subscribe và có thể cũ; chỉ event live sau đó mới chặn kết quả fetch.
    let live = false;
    let streamed = false;
    fetch(`/api/workspaces/exness_forex/learning?${new URLSearchParams({ symbol })}`, { signal: controller.signal })
      .then((response) => (response.ok ? response.json() : undefined))
      .then((body) => {
        if (!body || streamed || body.symbol !== symbol) return;
        setHtf2Shadow(body.htf_ast3_shadow);
        setEntryPilot(body.entry_pilot);
      })
      .catch(() => {}); // ponytail: lỗi/abort bỏ qua, stream vẫn là nguồn chính; thêm báo lỗi khi cần.
    const unsubscribe = subscribeForexStream(
      symbol,
      'analysis',
      (value) => {
        streamed ||= live;
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
    live = true;
    return () => {
      controller.abort();
      unsubscribe();
    };
  }, [symbol]);
  const [loading, setLoading] = useState(false);
  const [frame, setFrame] = useState('15m');
  const chartFrameSeconds = frame === '4h' ? 14400 : frame === '1h' ? 3600 : 900;
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
    () => [...new Set([...picked, ...holdingTrees, ...(v4Chart ? [v4Chart.strategy] : [])])],
    [picked, holdingTrees, v4Chart],
  );
  const chartAnalysis: LearnerAnalysis | null = v4Chart ? {
    features: { trend: '', utc_session: 0, spread: 0, body: 0, range: 0 },
    levels: { m15_donchian: null, m15_ema20: null, h1_ema20: null, h1_ema50: null, barriers: [] },
    status: 'ready', source: 'v4_pilot', issues: [], scores: [], policy_mode: 'v4_pilot',
    policy_updated_at: null, selected_signal: null,
    ...(executionOnly ? analysis : null),
    candle_time: v4Chart.candle_time, available_at: v4Chart.available_at,
    strategies: { ...(executionOnly ? analysis?.strategies : {}), [v4Chart.strategy]: v4Chart.row },
  } : executionOnly ? analysis : null;
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
  useEffect(() => {
    onQuote?.(quoteLive && quoteState.quote ? { ...quoteState.quote, symbol } : null);
    return () => onQuote?.(null);
  }, [quoteLive, quoteState.quote, symbol, onQuote]);
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
    const stream = new EventSource(`/api/workspaces/exness_forex/quotes/stream?symbol=${symbol}&frame=${frame}`);
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
  }, [symbol, frame]);
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

  const pnlAt = (position: BrokerPosition, price: number) => estimatedPnl(position, price, dashboard.instrument?.profile);
  const openGroups = positionGroups.filter((group) => group.status === 'open');
  const chartGroup = chartScope === 'setup'
    ? positionGroups.find((group) => group.legs.some((leg) => leg.position_id === selectedPosition?.position_id))
    : undefined;
  const pickerPositions = executions.filter((position) => position.status === 'open' ||
    position.position_id === selectedPosition?.position_id ||
    openGroups.some((group) => group.legs.some((leg) => leg.position_id === position.position_id)));
  const pnlOf = (position: BrokerPosition) => {
    const quote = quoteState.quote;
    return quoteLive && quote
      ? pnlAt(position, position.side === 'buy' ? quote.bid : quote.ask)
      : null;
  };

  return (
    <section
      className={`panel prose mtf-observation ${executionOnly ? 'broker-chart-terminal' : ''} relative min-w-0 space-y-2 overflow-clip rounded-lg border border-border-base bg-canvas p-0 text-sm leading-relaxed text-text-secondary`}
    >
      {executionOnly ? (
        <header className="broker-market-header flex flex-wrap items-center justify-between gap-3 border-b border-border-base bg-none px-3 py-2">
          <div className="broker-instrument flex min-w-0 items-center gap-2">
            <span className="broker-symbol grid size-8 shrink-0 place-items-center rounded-md bg-surface-3 font-display text-xs font-semibold text-text-primary">{symbol.slice(0, 3)}</span>
            <div>
              <h2>
                {symbol} <span>Exness</span>
              </h2>
              <p>
                Exness Demo <span>·</span> Giá gốc sàn <span>·</span> {timeZoneLabel()}
              </p>
            </div>
          </div>
          <div className="broker-quote-pair order-3 m-0 grid w-full min-w-0 grid-cols-3 gap-2 text-xs text-text-muted md:order-none md:ml-auto md:flex md:w-auto md:flex-wrap md:items-center md:gap-3 [&_strong]:m-0" aria-label="Giá Exness trực tiếp">
            <div className="col-span-3 min-w-0"><ForexSessionStatus session={dashboard.market_session} now={clock} /></div>
            <span className="flex min-w-0 flex-col gap-1 md:flex-row md:items-baseline">
              BID{' '}
              <strong className="broker-bid font-mono text-sm text-text-primary tabular-nums">
                {visibleQuote ? formatPrice(visibleQuote.bid) : '—'}
              </strong>
            </span>
            <span className="flex min-w-0 flex-col gap-1 md:flex-row md:items-baseline">
              ASK{' '}
              <strong className="broker-ask font-mono text-sm text-text-primary tabular-nums">
                {visibleQuote ? formatPrice(visibleQuote.ask) : '—'}
              </strong>
            </span>
            <span className="flex min-w-0 flex-col gap-1 md:flex-row md:items-baseline">
              SPREAD{' '}
              <strong className="font-mono text-sm text-text-primary tabular-nums">
                {visibleQuote ? formatPrice(visibleQuote.ask - visibleQuote.bid) : '—'}
              </strong>
            </span>
          </div>
          <span
            className={`broker-stream-state ${quoteLive ? 'is-live bg-bull/10 text-bull' : closedSession && !streamInterrupted ? 'is-closed bg-surface-3 text-text-muted' : 'bg-tone-uncertain-bg text-tone-uncertain'} shrink-0 px-2 py-1 text-xs`}
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
        <div className="section-head mb-3 flex min-w-0 flex-wrap items-center justify-between gap-2 [&_p]:text-xs [&_p]:text-text-muted">
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
          <span className="pill amber inline-flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 text-xs font-medium bg-surface-3 text-text-secondary text-amber-300">
            <Eye size={12} style={{ marginRight: 4, verticalAlign: -1 }} />
            {executionOnly
              ? 'Lệnh demo trên sàn · Bid/Ask Exness'
              : 'Quan sát · nến bid gốc Exness'}
          </span>
        </div>
      )}
      <div className="mtf-source-strip flex flex-wrap items-center gap-3 border-b border-border-subtle px-3 py-2 text-xs text-text-muted">
        <span>Nến M15/H1/H4 Exness Demo</span>
        {snap?.['available_at'] && <span>Snapshot {formatTime(snap.available_at)}</span>}
        {disconnected && <span className="negative text-bear">Mất kết nối API · dữ liệu lưu gần nhất</span>}
      </div>
      {(!dashboard.market.worker_online || currentStatus === 'data_paused') && (
        <p role="status" className="negative text-bear">
          Worker hoặc dữ liệu chưa sẵn sàng. Chart và setup là dữ liệu đã lưu, không phải tín hiệu
          mới.
        </p>
      )}

      {!executionOnly && (
        <div className="research-metrics grid grid-cols-2 gap-3 lg:grid-cols-4">
          <div>
            <span className="muted text-text-muted">Xu hướng 4h</span>
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
            <span className="muted text-text-muted">Trạng thái MTF</span>
            <strong>{observationStatusLabels[currentStatus] ?? currentStatus}</strong>
          </div>
          <div>
            <span className="muted text-text-muted">Vùng 20 nến 1h</span>
            <strong>
              {snap?.range_low && snap?.range_high
                ? `${formatPrice(snap.range_low)} – ${formatPrice(snap.range_high)}`
                : '—'}
            </strong>
          </div>
          <div>
            <span className="muted text-text-muted">SL / TP tham chiếu</span>
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
              <span className="pill inline-flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 text-xs font-medium bg-surface-3 text-text-secondary" style={{ marginLeft: 8 }}>
                {snap.provenance}
              </span>
            )}
          </p>
        </div>
      )}

      {loading && <p className="micro text-xs leading-relaxed text-text-muted">Đang tải nến {frame.toUpperCase()} Exness...</p>}
      {executionOnly ? (
        <div className="broker-chart-toolbar flex min-w-0 flex-wrap items-center gap-2 border-b border-border-subtle bg-surface-1 px-3 py-2 text-xs sm:gap-3">
          <div className="broker-segments flex items-center gap-0.5 rounded-lg border border-border-subtle bg-canvas p-0.5 [&>button]:min-h-8 [&>button]:rounded-md [&>button]:px-3 [&>button]:text-xs [&>button]:font-semibold [&>button]:text-text-muted [&>button:hover]:text-text-primary [&>button[aria-pressed=true]]:bg-surface-3 [&>button[aria-pressed=true]]:text-text-primary" role="group" aria-label="Khung thời gian chart">
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
          <div className="broker-segments flex items-center gap-0.5 rounded-lg border border-border-subtle bg-canvas p-0.5 [&>button]:min-h-8 [&>button]:rounded-md [&>button]:px-3 [&>button]:text-xs [&>button]:font-medium [&>button]:text-text-muted [&>button:hover]:text-text-primary [&>button[aria-pressed=true]]:bg-bull/10 [&>button[aria-pressed=true]]:text-bull" role="group" aria-label="Phía giá chart">
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
            setupStrategy={v4Chart?.strategy}
            trees={entryPilot?.pilot.trees}
          />
          {(hasMore || chartError) && (
            <button
              className="broker-history-button ml-auto inline-flex min-h-8 items-center justify-center rounded-lg border border-border-base px-3 text-xs text-text-secondary transition-colors hover:bg-surface-3"
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
        <div className="mtf-toolbar flex min-w-0 flex-wrap items-center gap-2 border-b border-border-base px-3 py-2 text-xs">
          <div className="mtf-seg flex flex-wrap items-center gap-1 [&>button]:rounded [&>button]:px-2 [&>button]:py-1 [&>button]:text-xs [&>button]:text-text-secondary [&>button:hover]:bg-surface-3 [&>button[aria-pressed=true]]:bg-surface-3 [&>button[aria-pressed=true]]:text-bull border-b border-border-subtle py-1" role="group" aria-label="Khung nến">
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
          <div className="mtf-seg flex flex-wrap items-center gap-1 [&>button]:rounded [&>button]:px-2 [&>button]:py-1 [&>button]:text-xs [&>button]:text-text-secondary [&>button:hover]:bg-surface-3 [&>button[aria-pressed=true]]:bg-surface-3 [&>button[aria-pressed=true]]:text-bull border-b border-border-subtle py-1" role="group" aria-label="Phía giá">
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
          <span role="status" className="mtf-toolbar-note text-xs text-text-muted">
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
              className="mtf-toolbar-action ml-auto rounded border border-border-active px-2 py-1 text-xs text-text-secondary"
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
        <div className="mtf-quote-strip flex flex-wrap items-center gap-3 border-b border-border-subtle px-3 py-2 text-xs text-text-muted" aria-label="Giá Exness trực tiếp">
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
        <p className="micro text-xs leading-relaxed text-text-muted">
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
      <div id="forex-signals" className="flex min-h-14 min-w-0 flex-wrap items-center justify-between gap-2 border-b border-border-base bg-surface-1 px-3 py-2 text-xs">
        <div className="flex min-w-0 flex-1 flex-wrap items-center gap-2">
          {selectedPosition && (
            <span className="inline-flex items-center gap-1.5 rounded-md border border-sky-400/40 bg-sky-400/10 px-2 py-1 text-xs font-medium text-sky-200">
              <button
                type="button"
                className="hover:underline flex items-center gap-1"
                onClick={() => {
                  setDrawerTab('positions');
                  setDrawerFocusId(selectedPosition.position_id);
                  setDrawerOpen(true);
                }}
              >
                {chartGroup && chartGroup.legs.length > 1 ? (
                  <>
                    <span>Setup {selectedPosition.side === 'buy' ? 'MUA' : 'BÁN'} #{chartGroup.legs[0].position_id}</span>
                    <span className="font-mono">({chartGroup.legs.length} ticket)</span>
                  </>
                ) : (
                  <>
                    <span>Ticket {selectedPosition.side === 'buy' ? 'MUA' : 'BÁN'} #{selectedPosition.position_id}</span>
                    <span className="font-mono">({ticketRole(selectedPosition)})</span>
                  </>
                )}
              </button>
              <button
                type="button"
                aria-label="Bỏ chọn ticket"
                className="ml-1 rounded p-0.5 hover:bg-sky-400/20 text-sky-300"
                onClick={() => onSelectPosition?.(undefined)}
              >
                <X size={12} />
              </button>
            </span>
          )}

          {selectedSetup && (
            <span className="inline-flex max-w-full items-center gap-1.5 rounded-md border border-sky-400/40 bg-sky-400/10 px-2 py-1 text-xs font-medium text-sky-200">
              <button
                type="button"
                className="hover:underline flex items-center gap-1"
                onClick={() => {
                  setDrawerTab('setups');
                  setDrawerFocusId(selectedSetup.id);
                  setDrawerOpen(true);
                }}
              >
                <span>Setup {selectedSetup.side === 'long' ? 'MUA' : 'BÁN'}</span>
                <span className="font-mono text-[11px]">({formatTime(selectedSetup.time)})</span>
              </button>
              <button
                type="button"
                aria-label="Bỏ chọn setup"
                className="ml-1 shrink-0 rounded p-0.5 text-sky-300 hover:bg-sky-400/20"
                onClick={() => setSetupSelection(null)}
              >
                <X size={12} />
              </button>
            </span>
          )}

          {!selectedPosition && !selectedSetup && (
            <span className="text-text-muted text-[11px]">Chưa chọn vị thế hoặc setup nào</span>
          )}
        </div>

        <button
          type="button"
          onClick={() => { setDrawerFocusId(undefined); setDrawerOpen(true); }}
          className="inline-flex items-center gap-1.5 rounded-lg border border-border-base bg-surface-2 px-3 py-1.5 text-xs font-medium text-text-primary transition-colors hover:border-border-active hover:bg-surface-3"
        >
          <span>Danh sách</span>
          <span className="rounded bg-surface-3 px-1.5 py-0.5 font-mono text-[10px] text-text-secondary">
            {pickerPositions.filter((position) => position.status === 'open').length} ticket mở · {setups.length} setup · {shadowTrades.filter((trade) => trade.status === 'open').length} shadow
          </span>
        </button>
      </div>

      <ForexSignalLifecycle
        key={symbol}
        open={drawerOpen}
        initialTab={drawerTab}
        onTabChange={(tab) => { setDrawerTab(tab); setDrawerFocusId(undefined); }}
        focusId={drawerFocusId}
        symbol={symbol}
        shadowTrades={shadowTrades}
        shadowStatus={entryPilot?.pilot.status}
        selectedShadowId={selectedShadow?.id}
        shadowQuote={quoteLive ? quoteState.quote : null}
        onSelectShadow={(id) => {
          setShadowSelection(id ? { symbol, id } : null);
          if (id) {
            setSetupSelection(null);
            onSelectPosition?.(undefined);
            if (window.matchMedia('(max-width: 639px)').matches) setDrawerOpen(false);
          }
        }}
        positions={pickerPositions}
        allPositions={executions}
        selectedPositionId={selectedPosition?.position_id}
        onSelectPosition={(id) => { setShadowSelection(null); onSelectPosition?.(id); }}
        pnlOf={pnlOf}
        setups={setups}
        selectedSetupId={selectedSetup?.id}
        onSelectSetup={(id, strategy) => {
          setShadowSelection(null);
          if (id && strategy) setV4Selection({ setup: id, strategy });
          setSetupSelection(id ? { symbol, id } : null);
        }}
        onClose={() => setDrawerOpen(false)}
        selectedV4Strategy={v4Strategy}
        strategies={analysis?.strategies}
        entryStateLabels={entryStateLabels}
        hasMoreCandles={hasMore}
        loadingOlder={loadingOlder}
        onLoadOlder={() => historyActions.current.older()}
        hasCandle={(time) =>
          [...candles, ...liveCandles].some(
            (c) => c.time === Math.floor(time / chartFrameSeconds) * chartFrameSeconds,
          )
        }
      />

      {selectedShadow && (
        <section aria-label="Shadow đang hiển thị" className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-sky-400/30 bg-sky-400/5 px-3 py-2 text-xs">
          <div className="min-w-0 space-y-1">
            <strong className="text-sky-300">SHADOW — MÔ PHỎNG · {selectedShadow.side.toUpperCase()} · {selectedShadow.policy}</strong>
            <p className="m-0 break-all text-text-muted">{selectedShadow.strategy} · {selectedShadow.entry_time == null ? 'Chưa khớp; mức giá dự kiến' : `Entry ${formatPrice(selectedShadow.entry!)} · ${formatTime(selectedShadow.entry_time, true)}`}</p>
            <p className="m-0 text-text-secondary">{selectedShadow.status === 'resolved' ? 'Đã chốt' : 'Tạm tính'}: {shadowPnlText(selectedShadow, quoteLive ? quoteState.quote : null)} · khối lượng giả lập, chưa swap · không phải lệnh broker</p>
          </div>
          <button type="button" onClick={() => setShadowSelection(null)} className="rounded border border-border-active px-2 py-1">Ẩn khỏi chart</button>
        </section>
      )}
      <MtfObservationChart
        entryIntents={selectedSetup?.intents}
        recordedSignals={selectedSetup?.signals}
        selectedSetupTime={selectedShadow?.entry_time ?? selectedShadow?.available_at ?? selectedSetup?.time}
        selectedShadow={selectedShadow}
        key={`${symbol}-${frame}-${side}-${historyBefore ?? 'live'}`}
        pricePrecision={
          dashboard.instrument?.profile?.point_digits ??
          (symbol === 'XAUUSD' ? 3 : symbol.endsWith('JPY') ? 3 : 5)
        }
        onLoadOlder={hasMore && !chartError ? () => historyActions.current.older() : undefined}
        candles={candles}
        liveCandles={liveCandles}
        snapshot={!executionOnly && frame === '15m' && side === 'bid' ? fullSnapshot : null}
        analysis={chartAnalysis}
        shown={shownStrategies}
        executions={executions}
        positionGroups={chartScope === 'setup' ? positionGroups : []}
        dark
        onClosePosition={onClosePosition}
        onSelectPosition={(id) => { setShadowSelection(null); onSelectPosition?.(id); }}
        onPositionDetails={(id) => {
          setShadowSelection(null);
          setDrawerTab('positions');
          setDrawerFocusId(id);
          setDrawerOpen(true);
        }}
        pnlAt={pnlAt}
        pnlOf={pnlOf}
        selectedPositionId={selectedShadow ? undefined : selectedPosition?.position_id}
        frameSeconds={chartFrameSeconds}
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
        <div className="bot-footer min-w-0 border-t border-border-base bg-surface-1">
          <div className="bot-footer-bar flex min-w-0 flex-wrap items-center justify-between gap-x-4 gap-y-2 px-3.5 py-2.5 text-xs text-text-secondary">
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-mono tabular-nums text-text-primary">
                {loading
                  ? 'Đang tải nến…'
                  : `${candles.length.toLocaleString('vi-VN')} nến ${frame.toUpperCase()}`}
              </span>
              <span className="text-text-muted">·</span>
              <span className="text-text-muted">
                {hasMore ? 'Kéo trái để tải thêm lịch sử' : 'Hết lịch sử đã lưu'}
              </span>
            </div>
            <div className="flex items-center gap-2">
              {selectedPosition?.status === 'closed' && (
                <button
                  type="button"
                  className="rounded-md border border-sky-400/30 bg-sky-400/10 px-2 py-0.5 text-[11px] font-medium text-sky-200 transition-colors hover:border-sky-400/60"
                  onClick={() => onSelectPosition?.(undefined)}
                >
                  Về giá hiện tại
                </button>
              )}
              <span
                className={`inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 font-mono text-[11px] ${
                  quoteLive && selectedPosition?.status !== 'closed'
                    ? 'border border-bull/20 bg-bull/10 text-bull font-medium'
                    : 'border border-border-subtle bg-surface-2 text-text-muted'
                }`}
              >
                <span
                  aria-hidden="true"
                  className={`size-1.5 rounded-full ${
                    quoteLive && selectedPosition?.status !== 'closed'
                      ? 'bg-bull'
                      : 'bg-text-muted'
                  }`}
                />
                {selectedPosition?.status === 'closed'
                  ? 'Xem lịch sử vị thế · không ghép tick'
                  : closedSession && !streamInterrupted
                    ? `${session.label} · ${session.transition}`
                    : quoteLive
                      ? 'OHLC từ tick sàn · REST chốt khi đóng nến'
                      : 'Tick tạm dừng · không dùng giá cũ'}
              </span>
            </div>
          </div>

          {analysis && (
            <details className="bot-analysis group/analysis border-t border-border-subtle bg-surface-1/60 px-3.5 py-2.5 text-xs transition-colors">
              <summary className="flex cursor-pointer list-none flex-wrap items-center gap-x-3 gap-y-2 text-text-muted select-none hover:text-text-primary [&::-webkit-details-marker]:hidden">
                <div className="flex items-center gap-2">
                  <span className="inline-flex size-5 shrink-0 items-center justify-center rounded-md bg-surface-3 text-text-secondary font-mono text-[11px] font-bold">
                    AI
                  </span>
                  <strong className="text-xs font-semibold text-text-primary">Bot quan sát</strong>
                  <span className="font-mono text-text-muted tabular-nums">
                    nến {clockTime(analysis.candle_time)}
                  </span>
                </div>

                <div className="flex flex-wrap items-center gap-2">
                  <span
                    className={`rounded-md border px-2 py-0.5 font-mono text-[11px] ${
                      analysis.selected_signal
                        ? 'border-bull/25 bg-bull/10 font-semibold text-bull'
                        : 'border-border-subtle bg-surface-2 text-text-secondary'
                    }`}
                  >
                    {analysis.selected_signal
                      ? `Chọn ${analysis.selected_signal.strategy} ${analysis.selected_signal.side.toUpperCase()}`
                      : `Chưa chọn tín hiệu · ${analysis.policy_mode}`}
                  </span>

                  <span className="inline-flex items-center gap-1.5 rounded-md border border-border-subtle bg-surface-2 px-2 py-0.5 font-mono text-[11px] text-text-muted tabular-nums">
                    <span className="font-semibold text-text-secondary">
                      {
                        Object.values(analysis.strategies).filter(
                          (row) => row.status === 'confirmed',
                        ).length
                      }
                    </span>{' '}
                    xác nhận ·{' '}
                    <span className="font-semibold text-text-muted">
                      {
                        Object.values(analysis.strategies).filter(
                          (row) => row.status !== 'confirmed',
                        ).length
                      }
                    </span>{' '}
                    loại
                  </span>
                </div>

                <span className="ml-auto inline-flex items-center gap-1 text-xs text-text-muted">
                  <span className="hidden sm:inline">Xem phân tích</span>
                  <span
                    aria-hidden="true"
                    className="transition-transform group-open/analysis:rotate-180"
                  >
                    ⌄
                  </span>
                </span>
              </summary>

              <div className="mt-3 space-y-2 border-t border-border-subtle pt-3">
                <BotAnalysisBreakdown
                  strategies={analysis.strategies}
                  lineage={new Map((htf2Shadow?.trees ?? []).map((tree) => [tree.id, tree]))}
                />
                <p className="m-0 text-[11px] text-text-muted">
                  Đường nét đứt BOT/DỰ KIẾN là kết quả phân tích theo nến, không phải lệnh đã gửi sàn.
                </p>
              </div>
            </details>
          )}

          <div className="border-t border-border-subtle bg-surface-2/20 px-3.5 py-2">
            <p className="m-0 text-[11px] leading-relaxed text-text-muted">
              Tín hiệu và canh giá không đồng nghĩa đã vào lệnh. Mũi tên, SL/TP của vị thế mở theo dữ liệu broker; lịch sử lệnh ở tab Lệnh trên sàn.
            </p>
          </div>
        </div>
      )}
      {!executionOnly && (
        <ForexEntryPilotPanel key={symbol} symbol={symbol} entryPilot={entryPilot} />
      )}
      {!executionOnly && htf2Shadow && (
        <div className="learning-section-card min-w-0 space-y-3 rounded-md border border-border-subtle bg-surface-1 p-3">
          <div className="card-header mb-3 flex min-w-0 flex-wrap items-center justify-between gap-2 [&_p]:text-xs [&_p]:text-text-muted">
            <div>
              <h4>AST HTF V3 · Lưu trữ, chỉ đọc</h4>
              <p className="muted-desc text-xs leading-relaxed text-text-muted">
                {htf2Shadow.note}. Thế hệ {htf2Shadow.generation}, {htf2Shadow.tree_count} cây.
              </p>
            </div>
            <span className="badge-count inline-flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 text-xs font-medium bg-surface-3 text-text-secondary">
              {htf2Shadow.status} · {htf2Shadow.resolved} đã đóng · {htf2Shadow.pending} đang chạy
            </span>
          </div>
          <HtfLineage trees={htf2Shadow.trees} nextEvolutionAt={htf2Shadow.next_evolution_at} />
        </div>
      )}
    </section>
  );
}
