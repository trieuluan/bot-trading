import type { ForexDashboard } from '../App';
import { ForexSessionStatus } from '../components/ForexSessionStatus';
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
  const ast = data?.learning?.htf_ast3_shadow;
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
      <section className={`ov-status tone-${status.tone} min-w-0 rounded-lg border border-border-base bg-surface-1 flex flex-wrap items-center justify-between gap-3 p-3`} aria-label="Trạng thái vận hành">
        <div className="ov-status-main [&>p]:m-0 [&>p]:text-text-secondary flex flex-wrap items-center gap-2 text-xs">
          <span className="execution-eyebrow text-xs font-semibold uppercase tracking-widest text-bull">{symbol} · Exness demo</span>
          <h2>
            <span className="ov-status-dot inline-block size-1.5 shrink-0 rounded-full bg-bull [&.stale-dot]:bg-amber-400" aria-hidden="true" />
            {status.title}
          </h2>
          <p>{status.reason}</p>
          {status.hint && <p className="ov-status-hint basis-full text-xs text-text-muted">{status.hint}</p>}
          <ForexSessionStatus session={dashboard.market_session} />
          <div className="ov-health flex flex-wrap items-center gap-2 text-xs" aria-label="Sức khỏe hệ thống">
            <span className={`dot-label tone-${executor.tone} inline-flex items-center gap-1.5 text-xs text-text-muted [&.offline]:text-bear [&.ok]:text-bull [&.bad]:text-bear [&.warn]:text-amber-300`}>
              Worker thực thi: {executor.text.toLowerCase()}
            </span>
            <span
              className={`dot-label tone-${disconnected ? 'idle' : dashboard.market.worker_online ? 'ok' : 'bad'} inline-flex items-center gap-1.5 text-xs text-text-muted [&.offline]:text-bear [&.ok]:text-bull [&.bad]:text-bear [&.warn]:text-amber-300`}
            >
              Worker nến: {dashboard.market.worker_online ? 'online' : 'offline'}
            </span>
            <button
              type="button"
              className={`dot-label link-chip tone-${feedIssue ? feedTone(feedIssue) : 'ok'} inline-flex items-center gap-1.5 text-xs text-text-muted [&.offline]:text-bear [&.ok]:text-bull [&.bad]:text-bear [&.warn]:text-amber-300 inline-flex items-center gap-1 text-xs text-bull underline-offset-4 hover:underline`}
              onClick={() => setRoute({ page: 'Kết nối sàn' })}
            >
              Feed {symbol}:{' '}
              {feedIssue
                ? `${feedIssue.timeframe.toUpperCase()} ${feedText(feedIssue)}`
                : 'đã đồng bộ'}
            </button>
          </div>
        </div>
        <div className="ov-status-actions flex flex-wrap items-center gap-2 text-xs">
          <button type="button" className="primary inline-flex items-center justify-center gap-2 rounded-md bg-bull px-3 py-2 text-xs font-semibold text-canvas hover:bg-emerald-300" onClick={() => setRoute({ page: 'Bot' })}>
            Mở chart & Bot
          </button>
          <button
            type="button"
            className="secondary inline-flex items-center justify-center gap-1.5 rounded-md border border-border-active bg-surface-2 px-3 py-1.5 text-xs text-text-secondary hover:bg-surface-3"
            onClick={() => setRoute({ page: 'Kết nối sàn' })}
          >
            Kiểm tra kết nối
          </button>
        </div>
      </section>

      <div className="ov-kpis grid grid-cols-2 gap-3 lg:grid-cols-4 [&>div]:flex [&>div]:min-w-0 [&>div]:flex-col [&>div]:gap-1 [&>div]:rounded-lg [&>div]:border [&>div]:border-border-base [&>div]:bg-surface-1 [&>div]:p-3 [&_small]:text-xs [&_small]:text-text-muted [&_strong]:text-xl [&_strong]:tabular-nums [&_strong]:text-text-primary [&_span]:text-xs [&_span]:text-text-muted [&_em]:text-sm [&_em]:not-italic [&_em]:text-text-muted" aria-label="Tài khoản Exness demo">
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

      <div className="forex-overview-grid mt-4 grid min-w-0 gap-4 xl:grid-cols-[minmax(0,2fr)_minmax(260px,1fr)]">
        <section className="forex-overview-panel [&>h2]:m-0 [&>h2]:text-base min-w-0 space-y-3 rounded-md border border-border-subtle bg-surface-1 p-3">
          <span className="execution-eyebrow text-xs font-semibold uppercase tracking-widest text-bull">Quần thể HTF AST V3 · {symbol}</span>
          <h2>
            Thế hệ G{ast?.generation ?? '—'} · {treeCount} cây
          </h2>
          <div className="ov-tree-meters [&>div]:flex [&>div]:flex-col [&>div]:gap-1 [&_strong]:text-xl [&_strong]:tabular-nums [&_strong]:text-text-primary [&_span]:text-sm [&_span]:text-text-secondary [&_small]:text-xs [&_small]:text-text-muted [&_em]:text-sm [&_em]:not-italic [&_em]:text-text-muted grid grid-cols-1 gap-2 sm:grid-cols-2 [&>div]:rounded [&>div]:bg-surface-2 [&>div]:p-2">
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
          <dl className="forex-summary-list m-0 grid min-w-0 grid-cols-1 gap-2 p-0 sm:grid-cols-2 [&>div]:flex [&>div]:min-w-0 [&>div]:flex-col [&>div]:items-start [&>div]:gap-1 [&>div]:rounded [&>div]:border-0 [&>div]:bg-surface-2 [&>div]:p-2 [&_dt]:text-xs [&_dt]:text-text-muted [&_dd]:m-0 [&_dd]:max-w-full [&_dd]:text-left [&_dd]:text-sm [&_dd]:text-text-primary [&_dd]:[overflow-wrap:anywhere]">
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
            className="secondary inline-flex items-center justify-center gap-1.5 rounded-md border border-border-active bg-surface-2 px-3 py-1.5 text-xs text-text-secondary hover:bg-surface-3"
            onClick={() => setRoute({ page: 'Bot', tab: 'learning' })}
          >
            Xem cây & dòng dõi
          </button>
        </section>
        <section className="forex-overview-panel min-w-0 space-y-3 rounded-md border border-border-subtle bg-surface-1 p-3">
          <span className="execution-eyebrow text-xs font-semibold uppercase tracking-widest text-bull">Quyết định gần nhất · {symbol}</span>
          <h2>{data?.decision ?? 'Chưa có quyết định'}</h2>
          <dl className="forex-summary-list m-0 grid min-w-0 grid-cols-1 gap-2 p-0 sm:grid-cols-2 [&>div]:flex [&>div]:min-w-0 [&>div]:flex-col [&>div]:items-start [&>div]:gap-1 [&>div]:rounded [&>div]:border-0 [&>div]:bg-surface-2 [&>div]:p-2 [&_dt]:text-xs [&_dt]:text-text-muted [&_dd]:m-0 [&_dd]:max-w-full [&_dd]:text-left [&_dd]:text-sm [&_dd]:text-text-primary [&_dd]:[overflow-wrap:anywhere]">
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
            className="secondary inline-flex items-center justify-center gap-1.5 rounded-md border border-border-active bg-surface-2 px-3 py-1.5 text-xs text-text-secondary hover:bg-surface-3"
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
