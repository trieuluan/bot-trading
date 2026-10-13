import { useEffect, useState } from 'react';
import { Eye, FlaskConical, AlertTriangle } from 'lucide-react';
import type { ForexDashboard } from '../App';
import {
  MtfObservationChart,
  type ObservationChartSnapshot,
} from '../components/MtfObservationChart';
import { formatPrice, observationStatusLabels } from '../lib/format';

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
  reports: ReplayReportItem[];
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
}: {
  dashboard: ForexDashboard;
  disconnected: boolean;
}) {
  const [candles, setCandles] = useState<ForexCandle[]>([]);
  const [loading, setLoading] = useState(false);
  const [frame, setFrame] = useState('15m');
  const [side, setSide] = useState('bid');
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
  const snap = dashboard.observation.snapshot;
  const fullSnapshot: ObservationChartSnapshot | null =
    snap?.range_high &&
    snap?.range_low &&
    snap.range_start !== undefined &&
    snap.range_end !== undefined
      ? {
          strategy: snap.strategy ?? 'exness_mtf_v1',
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
    setLoading(true);
    setCandles([]);
    setChartError('');
    fetch(`/api/workspaces/exness_forex/candles?timeframe=${frame}&price_type=${side}&limit=240`, {
      signal: AbortSignal.timeout(10000),
    })
      .then(async (res) => {
        if (!res.ok) throw new Error('Cannot load M15 bid candles');
        const data = await res.json();
        if (
          data.workspace?.id !== 'exness_forex' ||
          data.timeframe !== frame ||
          data.price_type !== side
        )
          throw new Error('Wrong candle source');
        if (active) setCandles(data.candles ?? []);
      })
      .catch(() => {
        if (active) setChartError('Không tải được nến Exness. Không thay thế bằng dữ liệu MEXC.');
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [dashboard.market.last_sync, frame, side]);

  return (
    <section className="panel prose mtf-observation">
      <div className="section-head">
        <div>
          <h2>Chiến lược MTF Price Action · XAUUSD</h2>
          <p>4h EMA50 xu hướng → 1h breakout vùng 20 nến → 15m retest và nến xác nhận giữ vùng.</p>
        </div>
        <span className="pill amber">
          <Eye size={12} style={{ marginRight: 4, verticalAlign: -1 }} />
          Chỉ quan sát · nến bid gốc Exness
        </span>
      </div>
      <p className="micro">
        Khung nến M15/H1/H4 lấy trực tiếp từ Exness Demo, không ghép nến MEXC.
        {disconnected && ' · Mất kết nối API; hiển thị trạng thái lưu gần nhất.'}
      </p>
      {snap?.available_at && (
        <p className="micro">
          Snapshot theo nến đóng: {new Date(snap.available_at * 1000).toISOString()} · USD/oz
        </p>
      )}
      {(!dashboard.market.worker_online || dashboard.observation.status === 'data_paused') && (
        <p role="status" className="negative">
          Worker hoặc dữ liệu chưa sẵn sàng. Chart và setup là dữ liệu đã lưu, không phải tín hiệu
          mới.
        </p>
      )}

      <div className="research-metrics">
        <div>
          <span className="muted">Xu hướng 4h</span>
          <strong>
            {snap?.trend === 'up'
              ? 'TĂNG (chỉ Long)'
              : snap?.trend === 'down'
                ? 'GIẢM (chỉ Short)'
                : 'CHƯA RÕ'}
          </strong>
        </div>
        <div>
          <span className="muted">Trạng thái MTF</span>
          <strong>
            {observationStatusLabels[dashboard.observation.status] ?? dashboard.observation.status}
          </strong>
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

      <div style={{ marginTop: 12 }}>
        <p>
          <strong>Chi tiết quan sát:</strong> {dashboard.observation.message}
          {snap?.provenance && (
            <span className="pill" style={{ marginLeft: 8 }}>
              {snap.provenance}
            </span>
          )}
        </p>
      </div>

      {loading && <p className="micro">Đang tải nến {frame.toUpperCase()} Exness...</p>}
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
      </div>
      {chartError && <p role="alert">{chartError}</p>}
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
      {!loading && !chartError && candles.length === 0 && (
        <p role="status">Chưa có nến đã đóng cho lựa chọn này.</p>
      )}
      <MtfObservationChart
        key={`${frame}-${side}`}
        candles={candles}
        liveCandles={
          quoteLive
            ? (quoteState.candles?.[frame] ?? []).map((row) => ({
                time: row.time,
                ...(side === 'bid' ? row.bid : row.ask),
              }))
            : []
        }
        snapshot={frame === '15m' && side === 'bid' ? fullSnapshot : null}
        livePrice={
          quoteLive
            ? {
                price: side === 'bid' ? quoteState.quote!.bid : quoteState.quote!.ask,
                label: `${side.toUpperCase()} LIVE`,
              }
            : null
        }
        title={`Exness XAUUSD · ${side.toUpperCase()} ${frame} · USD/oz · Thời gian UTC · Chỉ quan sát`}
      />
      <p className="micro">
        Vùng và dấu setup hiển thị trên chart Bid 15m. H1/H4 và Ask để đối chiếu nến gốc, không dịch
        chuyển mức giá của setup.
      </p>

      <div style={{ marginTop: 24 }}>
        <h3>Quy tắc vận hành MTF XAUUSD</h3>
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
            <strong>Xác nhận:</strong> Nến M15 đóng xác nhận giữ vùng; SL đặt theo cực trị cấu trúc
            retest, TP tham chiếu 2R.
          </li>
          <li>
            <strong>Khớp lệnh thực tế:</strong> Cần tính spread bid/ask và trượt giá; mức TP/SL trên
            chart chỉ là tham chiếu.
          </li>
        </ul>
      </div>
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

export function ForexResearchPage({ revision }: { revision: number }) {
  const [data, setData] = useState<ResearchApiResponse | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    let active = true;
    setLoading(true);
    fetch('/api/workspaces/exness_forex/research', { signal: AbortSignal.timeout(10000) })
      .then(async (res) => {
        if (!res.ok) throw new Error('Chưa có báo cáo replay');
        const json = await res.json();
        if (active) setData(json);
      })
      .catch(() => {
        if (active) setData(null);
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [revision]);

  const baseline = data?.reports?.find((report) => report.assumptions.slippage_bps_each_side === 0);

  return (
    <section className="panel prose">
      <div className="section-head">
        <div>
          <h2>Nghiên cứu Replay Lịch sử · XAUUSD</h2>
          <p>
            Mô phỏng khớp nến bid/ask, SL retest, khớp open nến kế tiếp. Không dùng portfolio hay ví
            tiền ảo.
          </p>
        </div>
        <span className="pill amber">
          <FlaskConical size={12} style={{ marginRight: 4, verticalAlign: -1 }} />
          Nghiên cứu · Chưa bật giao dịch
        </span>
      </div>

      {loading && <p className="micro">Đang tải báo cáo nghiên cứu...</p>}
      {!loading && (!data || !data.available || !baseline) && (
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
            <h3>Thống kê theo chiều lệnh (Trượt giá 0 bps)</h3>
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
