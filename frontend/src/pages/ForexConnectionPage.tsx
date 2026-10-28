import type { ForexDashboard } from '../App';
import { ForexMarkets, feedText, feedTone } from '../components/ForexMarkets';
import { useForexExecution } from '../hooks/useForexExecution';
import {
  botStatus,
  executorState,
  formatLevel,
  provenanceText,
  qualityText,
  spreadText,
  trendText,
} from '../lib/forex-labels';
import { relativeTime, formatTime } from '../lib/time-format';
import { observationStatusLabels } from '../lib/format';
import { ForexCatchupStatus } from './ForexStrategyPage';

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <dt>{label}</dt>
      <dd>{children}</dd>
    </div>
  );
}

export function ForexConnectionPage({
  dashboard,
  disconnected,
}: {
  dashboard: ForexDashboard;
  disconnected: boolean;
}) {
  const symbol = dashboard.workspace.symbol;
  const { data, error } = useForexExecution(symbol);
  const status = botStatus(data, error || !data);
  const executor = executorState(data);
  const quality = dashboard.observation.quality;
  const snapshot = dashboard.observation.snapshot;
  const candleWorker = !disconnected && dashboard.market.worker_online;

  return (
    <div className="forex-connection flex min-w-0 flex-col gap-4">
      <div className="conn-cards grid min-w-0 gap-3 xl:grid-cols-3">
        <section className={`conn-card flex min-w-0 flex-col gap-3 rounded-lg border border-border-base bg-surface-1 p-3 [&>header]:flex [&>header]:flex-wrap [&>header]:items-start [&>header]:justify-between [&>header]:gap-2 [&_h2]:m-0 [&_h2]:text-sm tone-${status.tone}`}>
          <header>
            <h2>Tài khoản Exness demo</h2>
            <span className={`status-chip tone-${status.tone} inline-flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 text-xs font-medium bg-surface-3 text-text-secondary`}>{status.title}</span>
          </header>
          <dl className="forex-summary-list m-0 grid min-w-0 grid-cols-1 gap-2 p-0 sm:grid-cols-2 [&>div]:flex [&>div]:min-w-0 [&>div]:flex-col [&>div]:items-start [&>div]:gap-1 [&>div]:rounded [&>div]:border-0 [&>div]:bg-surface-2 [&>div]:p-2 [&_dt]:text-xs [&_dt]:text-text-muted [&_dd]:m-0 [&_dd]:max-w-full [&_dd]:text-left [&_dd]:text-sm [&_dd]:text-text-primary [&_dd]:[overflow-wrap:anywhere]">
            <Row label="Tài khoản">{data?.account || '—'}</Row>
            <Row label="Thực thi">
              {!data
                ? '—'
                : !data.configured
                  ? 'Chưa cấu hình'
                  : data.enabled
                    ? 'Đang bật'
                    : 'Tạm dừng'}
            </Row>
            <Row label="Khối lượng mỗi lệnh">{data ? `${data.lot} lot` : '—'}</Row>
            <Row label="Giới hạn setup">
              {data
                ? `${data.max_concurrent} tài khoản · ${data.max_concurrent_per_symbol}/cặp`
                : '—'}
            </Row>
            <Row label="Lịch sử nhập từ">{formatTime(data?.history_from)}</Row>
          </dl>
          {status.hint && <p className="conn-hint m-0 rounded bg-surface-2 p-2 text-xs text-text-secondary">{status.hint}</p>}
        </section>

        <section className={`conn-card flex min-w-0 flex-col gap-3 rounded-lg border border-border-base bg-surface-1 p-3 [&>header]:flex [&>header]:flex-wrap [&>header]:items-start [&>header]:justify-between [&>header]:gap-2 [&_h2]:m-0 [&_h2]:text-sm tone-${executor.tone}`}>
          <header>
            <h2>Worker thực thi</h2>
            <span className={`status-chip tone-${executor.tone} inline-flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 text-xs font-medium bg-surface-3 text-text-secondary`}>{executor.text}</span>
          </header>
          <dl className="forex-summary-list m-0 grid min-w-0 grid-cols-1 gap-2 p-0 sm:grid-cols-2 [&>div]:flex [&>div]:min-w-0 [&>div]:flex-col [&>div]:items-start [&>div]:gap-1 [&>div]:rounded [&>div]:border-0 [&>div]:bg-surface-2 [&>div]:p-2 [&_dt]:text-xs [&_dt]:text-text-muted [&_dd]:m-0 [&_dd]:max-w-full [&_dd]:text-left [&_dd]:text-sm [&_dd]:text-text-primary [&_dd]:[overflow-wrap:anywhere]">
            <Row label="Heartbeat">
              {data?.heartbeat
                ? `${formatTime(data.heartbeat, true)} · ${relativeTime(data.heartbeat)}`
                : 'Chưa có'}
            </Row>
            <Row label="Đồng bộ lệnh sàn">
              {data?.synced_at
                ? `${formatTime(data.synced_at, true)} · ${relativeTime(data.synced_at)}`
                : 'Chưa có'}
            </Row>
            <Row label="Quyết định gần nhất">{data?.decision ?? '—'}</Row>
          </dl>
          {data?.error && <p className="conn-hint m-0 rounded bg-surface-2 p-2 text-xs text-text-secondary negative text-bear">Lỗi: {data.error}</p>}
          <p className="conn-note m-0 mt-auto text-xs text-text-muted">Mở/đóng lệnh, dời SL/TP và đối soát deal với Exness.</p>
        </section>

        <section className={`conn-card flex min-w-0 flex-col gap-3 rounded-lg border border-border-base bg-surface-1 p-3 [&>header]:flex [&>header]:flex-wrap [&>header]:items-start [&>header]:justify-between [&>header]:gap-2 [&_h2]:m-0 [&_h2]:text-sm tone-${candleWorker ? 'ok' : 'bad'}`}>
          <header>
            <h2>Worker nến · {symbol}</h2>
            <span className={`status-chip tone-${candleWorker ? 'ok' : 'bad'} inline-flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 text-xs font-medium bg-surface-3 text-text-secondary`}>
              {disconnected ? 'Mất kết nối API' : candleWorker ? 'Online' : 'Offline'}
            </span>
          </header>
          <dl className="forex-summary-list m-0 grid min-w-0 grid-cols-1 gap-2 p-0 sm:grid-cols-2 [&>div]:flex [&>div]:min-w-0 [&>div]:flex-col [&>div]:items-start [&>div]:gap-1 [&>div]:rounded [&>div]:border-0 [&>div]:bg-surface-2 [&>div]:p-2 [&_dt]:text-xs [&_dt]:text-text-muted [&_dd]:m-0 [&_dd]:max-w-full [&_dd]:text-left [&_dd]:text-sm [&_dd]:text-text-primary [&_dd]:[overflow-wrap:anywhere]">
            <Row label="Đồng bộ gần nhất">
              {dashboard.market.last_sync
                ? `${formatTime(dashboard.market.last_sync, true)} · ${relativeTime(dashboard.market.last_sync)}`
                : 'Chưa có'}
            </Row>
            <Row label="Dữ liệu">{dashboard.market.message}</Row>
          </dl>
          <p className="conn-note m-0 mt-auto text-xs text-text-muted">Tải nến bid/ask M15/H1/H4 và giá tick cho chart.</p>
        </section>
      </div>

      <section className="panel conn-feed space-y-3 min-w-0 rounded-lg border border-border-base bg-surface-1 p-4">
        <div className="forex-pairs-head flex min-w-0 flex-wrap items-center gap-2 mb-3 justify-between">
          <h2>Feed nến {symbol}</h2>
          {quality && (
            <span className={`status-chip tone-${quality.status === 'checked' ? 'ok' : 'warn'} inline-flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 text-xs font-medium bg-surface-3 text-text-secondary`}>
              Chất lượng: {qualityText[quality.status] ?? quality.status}
            </span>
          )}
        </div>
        <div className="conn-frames grid min-w-0 gap-3 lg:grid-cols-3">
          {(dashboard.frames ?? []).map((frame) => {
            const report = quality?.frames.find((row) => row.timeframe === frame.timeframe);
            const tone = feedTone(frame);
            return (
              <div key={frame.timeframe} className={`conn-frame min-w-0 rounded-md border border-border-base bg-surface-2 p-3 [&>header]:flex [&>header]:flex-wrap [&>header]:justify-between [&>header]:gap-2 [&_strong]:text-text-primary [&_dl]:mb-0 [&_dl]:mt-3 [&_dl]:grid [&_dl]:gap-2 [&_dl>div]:flex [&_dl>div]:min-w-0 [&_dl>div]:flex-col [&_dt]:text-xs [&_dt]:text-text-muted [&_dd]:m-0 [&_dd]:text-sm [&_dd]:text-text-primary [&_dd]:[overflow-wrap:anywhere] tone-${tone}`}>
                <header>
                  <strong>{frame.timeframe.toUpperCase()}</strong>
                  <span className={`dot-label tone-${tone} inline-flex items-center gap-1.5 text-xs text-text-muted [&.offline]:text-bear [&.ok]:text-bull [&.bad]:text-bear [&.warn]:text-amber-300`}>{feedText(frame)}</span>
                </header>
                <dl>
                  <Row label="Số nến mỗi phía">{frame.bars_per_side.toLocaleString('vi-VN')}</Row>
                  <Row label="Nến cuối">{formatTime(frame.last_candle)}</Row>
                  {report && (
                    <>
                      <Row label="Khoảng trống">{report.gap_count}</Row>
                      <Row label="Lỗi OHLC · thiếu phía · đảo">
                        {report.invalid_bars} · {report.unpaired_bars} · {report.crossed_bars}
                      </Row>
                      <Row label="Spread TB / tối đa">
                        {spreadText(report.close_spread_mean, symbol)} /{' '}
                        {spreadText(report.close_spread_max, symbol)}
                      </Row>
                    </>
                  )}
                </dl>
              </div>
            );
          })}
        </div>
        {quality && <p className="micro text-xs leading-relaxed text-text-muted">{quality.message}</p>}
        <p className="micro text-xs leading-relaxed text-text-muted">
          Nến bid/ask lưu riêng theo workspace, cặp và khung thời gian. Không ghép nến MEXC hoặc tự
          tạo giá. Không lấp khoảng trống.
        </p>
      </section>

      <ForexCatchupStatus dashboard={dashboard} disconnected={disconnected} />

      <ForexMarkets symbol={symbol} variant="health" />

      {snapshot && (
        <details className="panel conn-legacy [&>summary]:cursor-pointer [&>summary]:text-text-secondary min-w-0 rounded-lg border border-border-base bg-surface-1 p-4">
          <summary>Quan sát MTF Breakout + Retest (chiến lược cũ, chỉ tham khảo)</summary>
          <dl className="forex-summary-list m-0 grid min-w-0 grid-cols-1 gap-2 p-0 sm:grid-cols-2 [&>div]:flex [&>div]:min-w-0 [&>div]:flex-col [&>div]:items-start [&>div]:gap-1 [&>div]:rounded [&>div]:border-0 [&>div]:bg-surface-2 [&>div]:p-2 [&_dt]:text-xs [&_dt]:text-text-muted [&_dd]:m-0 [&_dd]:max-w-full [&_dd]:text-left [&_dd]:text-sm [&_dd]:text-text-primary [&_dd]:[overflow-wrap:anywhere]">
            <Row label="Trạng thái">
              {observationStatusLabels[dashboard.observation.status] ??
                dashboard.observation.status}
            </Row>
            <Row label="Xu hướng 4h">
              {snapshot.trend ? (trendText[snapshot.trend] ?? snapshot.trend) : 'Chưa đủ nến'}
            </Row>
            <Row label="Cập nhật theo nến đóng">
              {formatTime(snapshot.available_at)} ·{' '}
              {provenanceText[snapshot.provenance ?? ''] ?? snapshot.provenance ?? '—'}
            </Row>
            <Row label="Vùng 20 nến 1h">
              {formatLevel(snapshot.range_low, symbol)} – {formatLevel(snapshot.range_high, symbol)}
            </Row>
            <Row label="Mức breakout">{formatLevel(snapshot.level, symbol)}</Row>
            <Row label="SL / TP tham chiếu">
              {formatLevel(snapshot.stop, symbol)} / {formatLevel(snapshot.target, symbol)}
            </Row>
          </dl>
          <p className="micro text-xs leading-relaxed text-text-muted">
            {dashboard.observation.message}. Chỉ HTF AST V2 sinh tín hiệu và lệnh mới; SL/TP ở đây
            chưa tính spread/khớp lệnh.
          </p>
          {!!dashboard.observation.recent?.length && (
            <ul className="conn-log m-0 list-none space-y-2 p-0 [&>li]:grid [&>li]:gap-1 [&>li]:border-t [&>li]:border-border-base [&>li]:py-2 [&_small]:text-text-muted">
              {dashboard.observation.recent.map((event) => (
                <li key={event.candle_time}>
                  <time>{formatTime(event.candle_time)}</time>
                  <span>{observationStatusLabels[event.status] ?? event.status}</span>
                  <small>{event.reason}</small>
                </li>
              ))}
            </ul>
          )}
        </details>
      )}
    </div>
  );
}
