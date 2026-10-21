import type { ForexDashboard } from '../App';
import { ForexMarkets, feedText, feedTone } from '../components/ForexMarkets';
import { useForexExecution } from '../hooks/useForexExecution';
import { botStatus, executorState } from '../lib/forex-labels';
import { relativeTime, formatTime } from '../lib/time-format';
import { setRoute } from '../lib/router';

const amount = (value: number | string | null | undefined) =>
  value == null ? '—' : Number(value).toLocaleString('vi-VN', { maximumFractionDigits: 2 });
const signClass = (value: number | null) =>
  value == null || value === 0 ? '' : value > 0 ? 'positive' : 'negative';

export function ForexOverviewPage({
  dashboard,
  disconnected,
}: {
  dashboard: ForexDashboard;
  disconnected: boolean;
}) {
  const symbol = dashboard.workspace.symbol;
  const { data: execution, error } = useForexExecution(symbol);
  const verified = !error && !disconnected;
  const data = verified ? execution : null;
  const status = botStatus(data, !verified || !execution);
  const ast = data?.learning?.htf_ast2_shadow;
  const v2 = data?.learning?.execution_v2;
  const treeCount = ast?.tree_count ?? 56;
  const balance = data?.account_state?.balance;
  const equity = data?.account_state?.equity;
  const floating = balance != null && equity != null ? Number(equity) - Number(balance) : null;
  const frames = dashboard.frames ?? [];
  const feedIssue = frames.find((frame) => feedTone(frame) !== 'ok');
  const m15 = frames.find((frame) => frame.timeframe === '15m');
  const lastProcessed = dashboard.observation.catch_up?.last_processed_candle;
  const executor = executorState(data);
  const nextEvolution = ast?.next_evolution_at;

  return (
    <div className="forex-overview-desk">
      <section className={`ov-status tone-${status.tone}`} aria-label="Trạng thái vận hành">
        <div className="ov-status-main">
          <span className="execution-eyebrow">{symbol} · Exness demo</span>
          <h2>
            <span className="ov-status-dot" aria-hidden="true" />
            {status.title}
          </h2>
          <p>{status.reason}</p>
          {status.hint && <p className="ov-status-hint">{status.hint}</p>}
          <div className="ov-health" aria-label="Sức khỏe hệ thống">
            <span className={`dot-label tone-${executor.tone}`}>
              Worker thực thi: {executor.text.toLowerCase()}
            </span>
            <span
              className={`dot-label tone-${disconnected ? 'idle' : dashboard.market.worker_online ? 'ok' : 'bad'}`}
            >
              Worker nến: {dashboard.market.worker_online ? 'online' : 'offline'}
            </span>
            <button
              type="button"
              className={`dot-label link-chip tone-${feedIssue ? feedTone(feedIssue) : 'ok'}`}
              onClick={() => setRoute({ page: 'Kết nối sàn' })}
            >
              Feed {symbol}:{' '}
              {feedIssue
                ? `${feedIssue.timeframe.toUpperCase()} ${feedText(feedIssue)}`
                : 'đã đồng bộ'}
            </button>
          </div>
        </div>
        <div className="ov-status-actions">
          <button type="button" className="primary" onClick={() => setRoute({ page: 'Bot' })}>
            Mở chart & Bot
          </button>
          <button
            type="button"
            className="secondary"
            onClick={() => setRoute({ page: 'Kết nối sàn' })}
          >
            Kiểm tra kết nối
          </button>
        </div>
      </section>

      <div className="ov-kpis" aria-label="Tài khoản Exness demo">
        <div>
          <small>Balance · USD</small>
          <strong>{amount(balance)}</strong>
          <span>Margin đã dùng {amount(data?.account_state?.used_margin)}</span>
        </div>
        <div>
          <small>Equity · USD</small>
          <strong>{amount(equity)}</strong>
          <span className={signClass(floating)}>
            Thả nổi {floating != null && floating > 0 ? '+' : ''}
            {amount(floating)}
          </span>
        </div>
        <div>
          <small>Profit đã chốt · USD</small>
          <strong className={signClass(data?.broker_profit ?? null)}>
            {amount(data?.broker_profit)}
          </strong>
          <span>{amount(data?.closed_count)} lệnh đóng · chưa gồm swap</span>
        </div>
        <div>
          <small>Vị thế đang mở</small>
          <strong>
            {data?.open_count ?? '—'}
            <em> / {data?.max_concurrent ?? '—'}</em>
          </strong>
          <span>
            {symbol}: {data?.symbol_open_count ?? '—'}/{data?.max_concurrent_per_symbol ?? '—'}
          </span>
        </div>
      </div>

      <div className="forex-overview-grid">
        <section className="forex-overview-panel">
          <span className="execution-eyebrow">Quần thể HTF AST V2 · {symbol}</span>
          <h2>
            Thế hệ G{ast?.generation ?? '—'} · {treeCount} cây
          </h2>
          <div className="ov-tree-meters">
            <div>
              <strong>
                {v2?.entry_allowed_count ?? '—'}
                <em>/{treeCount}</em>
              </strong>
              <span>Cây được phép vào lệnh</span>
              <small>Ungated: cây nào có tín hiệu cũng được vào lệnh ngay.</small>
            </div>
            <div>
              <strong>
                {v2?.evolution_ready_count ?? '—'}
                <em>/{treeCount}</em>
              </strong>
              <span>Đủ chuẩn tiến hoá</span>
              <small>Cần ≥10 mẫu forward và R trung bình &gt; 0 để làm bố mẹ.</small>
            </div>
          </div>
          <dl className="forex-summary-list">
            <div>
              <dt>Mẫu forward (đóng / chờ / loại)</dt>
              <dd>{ast ? `${ast.resolved} / ${ast.pending} / ${ast.excluded}` : '—'}</dd>
            </div>
            <div>
              <dt>R trung bình sau chi phí</dt>
              <dd className={signClass(ast?.mean_r ?? null)}>
                {ast?.mean_r == null ? 'Chưa có mẫu đóng' : ast.mean_r.toFixed(3)}
              </dd>
            </div>
            <div>
              <dt>Tiến hoá kế tiếp</dt>
              <dd>
                {!nextEvolution
                  ? 'Chờ đủ 2 cây bố mẹ'
                  : nextEvolution * 1000 > Date.now()
                    ? `${formatTime(nextEvolution)} · ${relativeTime(nextEvolution)}`
                    : `Đến hạn từ ${formatTime(nextEvolution)} · chờ worker chạy`}
              </dd>
            </div>
          </dl>
          <button
            type="button"
            className="secondary"
            onClick={() => setRoute({ page: 'Bot', tab: 'learning' })}
          >
            Xem cây & dòng dõi
          </button>
        </section>
        <section className="forex-overview-panel">
          <span className="execution-eyebrow">Quyết định gần nhất · {symbol}</span>
          <h2>{data?.decision ?? 'Chưa có quyết định'}</h2>
          <dl className="forex-summary-list">
            <div>
              <dt>Cây được chọn</dt>
              <dd>{data?.learning?.latest?.selected ?? v2?.selected ?? 'Chưa có'}</dd>
            </div>
            <div>
              <dt>Nến M15 đã đóng mới nhất</dt>
              <dd>
                {m15?.last_candle
                  ? `${formatTime(m15.last_candle)} · ${relativeTime(m15.last_candle + 900)}`
                  : 'Chưa có nến'}
              </dd>
            </div>
            <div>
              <dt>Đã phân tích đến nến</dt>
              <dd>{lastProcessed != null ? formatTime(lastProcessed) : 'Chưa có'}</dd>
            </div>
          </dl>
          <button
            type="button"
            className="secondary"
            onClick={() => setRoute({ page: 'Bot', tab: 'learning' })}
          >
            Xem nhật ký quyết định
          </button>
        </section>
      </div>

      <ForexMarkets symbol={symbol} variant="table" />
    </div>
  );
}
