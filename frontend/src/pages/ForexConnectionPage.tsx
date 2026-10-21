import type { ForexDashboard } from '../App';
import { ForexMarkets, feedText, feedTone } from '../components/ForexMarkets';
import { useForexExecution } from '../hooks/useForexExecution';
import {
  botStatus,
  executorState,
  formatLevel,
  provenanceText,
  qualityText,
  relativeTime,
  spreadText,
  trendText,
  utcTime,
} from '../lib/forex-labels';
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
    <div className="forex-connection">
      <div className="conn-cards">
        <section className={`conn-card tone-${status.tone}`}>
          <header>
            <h2>Tài khoản Exness demo</h2>
            <span className={`status-chip tone-${status.tone}`}>{status.title}</span>
          </header>
          <dl className="forex-summary-list">
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
            <Row label="Giới hạn vị thế">
              {data
                ? `${data.max_concurrent} tài khoản · ${data.max_concurrent_per_symbol}/cặp`
                : '—'}
            </Row>
            <Row label="Lịch sử nhập từ">{utcTime(data?.history_from)}</Row>
          </dl>
          {status.hint && <p className="conn-hint">{status.hint}</p>}
        </section>

        <section className={`conn-card tone-${executor.tone}`}>
          <header>
            <h2>Worker thực thi</h2>
            <span className={`status-chip tone-${executor.tone}`}>{executor.text}</span>
          </header>
          <dl className="forex-summary-list">
            <Row label="Heartbeat">
              {data?.heartbeat
                ? `${utcTime(data.heartbeat, true)} · ${relativeTime(data.heartbeat)}`
                : 'Chưa có'}
            </Row>
            <Row label="Đồng bộ lệnh sàn">
              {data?.synced_at
                ? `${utcTime(data.synced_at, true)} · ${relativeTime(data.synced_at)}`
                : 'Chưa có'}
            </Row>
            <Row label="Quyết định gần nhất">{data?.decision ?? '—'}</Row>
          </dl>
          {data?.error && <p className="conn-hint negative">Lỗi: {data.error}</p>}
          <p className="conn-note">Mở/đóng lệnh, dời SL/TP và đối soát deal với Exness.</p>
        </section>

        <section className={`conn-card tone-${candleWorker ? 'ok' : 'bad'}`}>
          <header>
            <h2>Worker nến · {symbol}</h2>
            <span className={`status-chip tone-${candleWorker ? 'ok' : 'bad'}`}>
              {disconnected ? 'Mất kết nối API' : candleWorker ? 'Online' : 'Offline'}
            </span>
          </header>
          <dl className="forex-summary-list">
            <Row label="Đồng bộ gần nhất">
              {dashboard.market.last_sync
                ? `${utcTime(dashboard.market.last_sync, true)} · ${relativeTime(dashboard.market.last_sync)}`
                : 'Chưa có'}
            </Row>
            <Row label="Dữ liệu">{dashboard.market.message}</Row>
          </dl>
          <p className="conn-note">Tải nến bid/ask M15/H1/H4 và giá tick cho chart.</p>
        </section>
      </div>

      <section className="panel conn-feed">
        <div className="forex-pairs-head">
          <h2>Feed nến {symbol}</h2>
          {quality && (
            <span className={`status-chip tone-${quality.status === 'checked' ? 'ok' : 'warn'}`}>
              Chất lượng: {qualityText[quality.status] ?? quality.status}
            </span>
          )}
        </div>
        <div className="conn-frames">
          {(dashboard.frames ?? []).map((frame) => {
            const report = quality?.frames.find((row) => row.timeframe === frame.timeframe);
            const tone = feedTone(frame);
            return (
              <div key={frame.timeframe} className={`conn-frame tone-${tone}`}>
                <header>
                  <strong>{frame.timeframe.toUpperCase()}</strong>
                  <span className={`dot-label tone-${tone}`}>{feedText(frame)}</span>
                </header>
                <dl>
                  <Row label="Số nến mỗi phía">{frame.bars_per_side.toLocaleString('vi-VN')}</Row>
                  <Row label="Nến cuối">{utcTime(frame.last_candle)}</Row>
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
        {quality && <p className="micro">{quality.message}</p>}
        <p className="micro">
          Nến bid/ask lưu riêng theo workspace, cặp và khung thời gian. Không ghép nến MEXC hoặc tự
          tạo giá. Không lấp khoảng trống.
        </p>
      </section>

      <ForexCatchupStatus dashboard={dashboard} disconnected={disconnected} />

      <ForexMarkets symbol={symbol} variant="health" />

      {snapshot && (
        <details className="panel conn-legacy">
          <summary>Quan sát MTF Breakout + Retest (chiến lược cũ, chỉ tham khảo)</summary>
          <dl className="forex-summary-list">
            <Row label="Trạng thái">
              {observationStatusLabels[dashboard.observation.status] ??
                dashboard.observation.status}
            </Row>
            <Row label="Xu hướng 4h">
              {snapshot.trend ? (trendText[snapshot.trend] ?? snapshot.trend) : 'Chưa đủ nến'}
            </Row>
            <Row label="Cập nhật theo nến đóng">
              {utcTime(snapshot.available_at)} ·{' '}
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
          <p className="micro">
            {dashboard.observation.message}. Chỉ HTF AST V2 sinh tín hiệu và lệnh mới; SL/TP ở đây
            chưa tính spread/khớp lệnh.
          </p>
          {!!dashboard.observation.recent?.length && (
            <ul className="conn-log">
              {dashboard.observation.recent.map((event) => (
                <li key={event.candle_time}>
                  <time>{utcTime(event.candle_time)}</time>
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
