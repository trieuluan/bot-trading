import { useEffect, useRef, useState } from 'react';
import { Eye, FlaskConical, AlertTriangle } from 'lucide-react';
import type { ForexDashboard } from '../App';
import {
  MtfObservationChart,
  type ObservationChartSnapshot,
} from '../components/MtfObservationChart';
import { formatPrice, observationStatusLabels } from '../lib/format';
import type { BrokerPosition } from '../types/exness-execution';

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
  const [loading, setLoading] = useState(false);
  const [frame, setFrame] = useState('15m');
  const [side, setSide] = useState('bid');
  const [view, setView] = useState<'mtf' | 'trend' | 'range' | 'router'>('mtf');
  const historyBefore = selectedPosition?.status === 'closed' ? selectedPosition.close_time : null;
  const [loadingOlder, setLoadingOlder] = useState(false);
  const [hasMore, setHasMore] = useState(false);
  const historyActions = useRef({ older: () => {}, refresh: () => {} });
  const [chartError, setChartError] = useState('');
  const [quoteState, setQuoteState] = useState<QuoteState>({ status: 'connecting', quote: null });
  const [quoteHeartbeat, setQuoteHeartbeat] = useState(0);
  const [clock, setClock] = useState(Date.now());
  const quoteLive =
    quoteState.status === 'live' &&
    clock - quoteHeartbeat < 5000 &&
    quoteState.quote !== null &&
    clock / 1000 - quoteState.quote.time <= 15;

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
        for (const [frame, seconds] of Object.entries({ '15m': 900, '1h': 3600, '4h': 14400 })) {
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
          { signal: AbortSignal.any([controller.signal, AbortSignal.timeout(10000)]) },
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
    historyActions.current = { older: () => void load(true), refresh: () => void load() };
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
      <p className="micro">
        Khung nến M15/H1/H4 lấy trực tiếp từ Exness Demo, không ghép nến MEXC.
        {disconnected && ' · Mất kết nối API; hiển thị trạng thái lưu gần nhất.'}
      </p>
      {snap?.['available_at'] && (
        <p className="micro">
          Snapshot theo nến đóng: {new Date(snap?.available_at * 1000).toISOString()} · USD/oz
        </p>
      )}
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
        <div className="forex-chart-controls">
          <label>
            Khung nến{' '}
            <select value={frame} onChange={(event) => setFrame(event.target.value)}>
              <option value="15m">15 phút · Xác nhận</option>
              <option value="1h">1 giờ · Breakout</option>
              <option value="4h">4 giờ · Xu hướng</option>
            </select>
          </label>
          <label>
            Phía giá{' '}
            <select value={side} onChange={(event) => setSide(event.target.value)}>
              <option value="bid">Bid</option>
              <option value="ask">Ask</option>
            </select>
          </label>
          <span role="status" className="micro">
            {candles.length.toLocaleString()} nến ·{' '}
            {loadingOlder
              ? 'Đang tải lịch sử...'
              : hasMore
                ? 'Kéo trái để tải thêm'
                : 'Đã hết lịch sử lưu trong DB'}
          </span>
          {(hasMore || chartError) && (
            <button
              type="button"
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
        <div className="research-metrics" aria-label="Giá Exness trực tiếp">
          <div>
            <span className="muted">Stream XAUUSD</span>
            <strong>
              {quoteStatusLabels[
                quoteState.status === 'live' && !quoteLive ? 'stale' : quoteState.status
              ] ?? 'Stream chưa sẵn sàng'}
            </strong>
          </div>
          <div>
            <span className="muted">Bid · USD/oz</span>
            <strong>{quoteLive ? formatPrice(quoteState.quote!.bid) : '—'}</strong>
          </div>
          <div>
            <span className="muted">Ask · USD/oz</span>
            <strong>{quoteLive ? formatPrice(quoteState.quote!.ask) : '—'}</strong>
          </div>
          <div>
            <span className="muted">Spread · USD/oz</span>
            <strong>
              {quoteLive ? formatPrice(quoteState.quote!.ask - quoteState.quote!.bid) : '—'}
            </strong>
          </div>
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
                <span>{position.volume ?? '—'} lot {position.role === 'tp1' ? '· TP1' : position.role === 'runner' ? '· TP2' : ''}</span>
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
                  ? [selectedPosition.role === 'tp1' ? 'TP1 · 1R' : 'TP2 · 2R',
                      selectedPosition.partial_taken ? 'Đã chốt TP1' : '',
                      selectedPosition.management_status === 'confirmed'
                        ? selectedPosition.management_stage === 'trailing' ? 'Đã dời trailing SL' : 'Đã dời SL về entry'
                        : selectedPosition.management_status === 'submitted' ? 'Đang xác minh dời SL' : '',
                    ].filter(Boolean).join(' · ')
                  : 'Chưa đủ lịch sử'}
            </strong>
          </span>
        </div>
      )}
      <MtfObservationChart
        key={`${frame}-${side}`}
        onLoadOlder={hasMore && !chartError ? () => historyActions.current.older() : undefined}
        candles={candles}
        liveCandles={
          quoteLive && !(executionOnly && selectedPosition?.status === 'closed')
            ? (quoteState.candles?.[frame] ?? []).map((row) => ({
                time: row.time,
                ...(side === 'bid' ? row.bid : row.ask),
              }))
            : []
        }
        snapshot={!executionOnly && frame === '15m' && side === 'bid' ? fullSnapshot : null}
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
        <div className="broker-chart-status">
          <span>
            {loading
              ? 'Đang tải nến…'
              : `${candles.length.toLocaleString()} nến ${frame.toUpperCase()}`}
          </span>
          <span>
            {selectedPosition?.status === 'closed'
              ? 'Đang xem lịch sử vị thế · không ghép tick'
              : quoteLive
                ? 'OHLC tạm thời từ tick · REST xác nhận khi đóng nến'
                : 'Tick tạm dừng · không dùng giá cũ'}
          </span>
          <span>{hasMore ? 'Kéo trái để tải thêm' : 'Hết lịch sử đã lưu'}</span>
        </div>
      )}
      <p className={executionOnly ? 'broker-chart-footnote' : 'micro'}>
        {executionOnly
          ? 'Chart chỉ hiện vị thế đang mở: mũi tên là fill vào, SL/TP theo dữ liệu sàn. Lệnh đã đóng xem ở tab Lệnh trên sàn.'
          : 'Vùng và dấu setup hiển thị trên chart Bid 15m. H1/H4 và Ask để đối chiếu nến gốc, không dịch chuyển mức giá của setup.'}
      </p>

      {!executionOnly && view !== 'mtf' && (
        <div style={{ marginTop: 24 }}>
          <h3>
            Nhật ký paper ·{' '}
            {view === 'trend'
              ? 'trend_pullback'
              : view === 'range'
                ? 'range_rejection'
                : 'regime_router'}
          </h3>
          <p>
            Theo dõi từ khi ví khởi tạo, tự chạy bù nến khi máy mở lại. Kết quả lịch sử ở tab
            Backtest; không cần backtest có lãi mới được chạy paper.
          </p>
          {!shadow?.recent_trades.length ? (
            <p>Chưa có setup xác nhận từ lúc ví khởi tạo.</p>
          ) : (
            <div className="table-scroll">
              <table>
                <caption>Tối đa 20 setup gần nhất · UTC · giá USD/oz</caption>
                <thead>
                  <tr>
                    <th>Xác nhận</th>
                    <th>Chiều</th>
                    <th>Trạng thái</th>
                    <th>Vào / SL / TP</th>
                    <th>P&amp;L USD</th>
                    <th>Lý do</th>
                  </tr>
                </thead>
                <tbody>
                  {shadow.recent_trades.map((trade) => (
                    <tr key={trade.confirm_time}>
                      <td>{new Date(trade.confirm_time * 1000).toISOString()}</td>
                      <td>{trade.side}</td>
                      <td>
                        {{
                          pending: 'Chờ khớp',
                          open: 'Đang mở',
                          resolved: 'Đã đóng',
                          skipped: 'Bỏ qua',
                          censored: 'Chưa xác định',
                          invalid: 'Không hợp lệ',
                        }[trade.status] ?? trade.status}
                      </td>
                      <td>
                        {trade.entry != null ? formatPrice(trade.entry) : '—'} /{' '}
                        {formatPrice(trade.stop)} /{' '}
                        {trade.target != null ? formatPrice(trade.target) : '—'}
                      </td>
                      <td>{trade.pnl?.toFixed(2) ?? '—'}</td>
                      <td>{trade.reason ?? trade.signal?.reason ?? '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
      {!executionOnly && view === 'mtf' && (
        <div style={{ marginTop: 24 }}>
          <h3>Quy tắc Breakout + Retest XAUUSD</h3>
          <ul>
            <li>
              <strong>Vào setup:</strong> Nến H1 đóng vượt đỉnh/đáy của 20 nến H1 trước đó theo xu
              hướng EMA50 khung H4.
            </li>
            <li>
              <strong>Chờ retest:</strong> Giá M15 chạm lại vùng breakout; setup tự hủy nếu nến M15
              đóng phá ngược hoặc quá 12h.
            </li>
            <li>
              <strong>Xác nhận:</strong> Nến M15 đóng xác nhận giữ vùng; SL đặt theo cực trị cấu
              trúc retest, TP tham chiếu 2R.
            </li>
            <li>
              <strong>Khớp lệnh thực tế:</strong> Cần tính spread bid/ask và trượt giá; mức TP/SL
              trên chart chỉ là tham chiếu.
            </li>
          </ul>
        </div>
      )}
      {includeReplay && <ForexReplayControl />}
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
    fetch('/api/workspaces/exness_forex/research', { signal: AbortSignal.timeout(10000) })
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
          <h2>Nghiên cứu Replay Lịch sử · XAUUSD</h2>
          <p>
            So sánh ví lịch sử cho 4 chiến lược; báo cáo mẫu tín hiệu Breakout + Retest giữ riêng.
            Khớp nến Bid/Ask, vào ở open M15 kế tiếp. Không gửi lệnh lên sàn.
          </p>
        </div>
        <span className="pill amber">
          <FlaskConical size={12} style={{ marginRight: 4, verticalAlign: -1 }} />
          Nghiên cứu · Chưa bật giao dịch
        </span>
      </div>

      {loading && <p className="micro">Đang tải báo cáo nghiên cứu...</p>}
      <ForexReplayControl onComplete={setData} />
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
