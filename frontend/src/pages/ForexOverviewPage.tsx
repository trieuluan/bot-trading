import { Activity, ArrowUpRight, ChartNoAxesCombined, GitBranch, History, Layers, Radio, Server, TrendingUp, Wallet } from 'lucide-react';
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
  value == null || value === 0 ? 'text-text-primary' : value > 0 ? 'text-bull' : 'text-bear';

const toneClass = {
  ok: 'border-bull/20 bg-bull/5 text-bull',
  warn: 'border-amber-400/20 bg-amber-400/5 text-amber-200',
  bad: 'border-bear/20 bg-bear/5 text-bear',
  idle: 'border-border-base bg-surface-2/60 text-text-muted',
};

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
  const treeCount = ast?.tree_count ?? '—';
  const balance = data?.account_state?.balance;
  const equity = data?.account_state?.equity;
  const floating = balance != null && equity != null ? Number(equity) - Number(balance) : null;
  const frames = dashboard.frames ?? [];
  const feedIssue = frames.find((frame) => feedTone(frame) !== 'ok');
  const m15 = frames.find((frame) => frame.timeframe === '15m');
  const lastProcessed = dashboard.observation.catch_up?.last_processed_candle;
  const executor = executorState(data);
  const nextEvolution = ast?.next_evolution_at;
  const workerTone = disconnected ? 'idle' : dashboard.market.worker_online ? 'ok' : 'bad';
  const feedState = disconnected || !frames.length ? 'idle' : feedIssue ? feedTone(feedIssue) : 'ok';

  return (
    <div className="forex-overview-desk min-w-0 space-y-5">
      <section className="overflow-hidden rounded-2xl border border-border-base bg-linear-to-br from-surface-2 via-surface-1 to-canvas" aria-label="Trạng thái vận hành">
        <div className="flex flex-wrap items-start justify-between gap-5 p-4 sm:p-5">
          <div className="min-w-0 flex-1 basis-80 space-y-3">
            <div className="flex flex-wrap items-center gap-2">
              <span className="rounded-md border border-amber-400/20 bg-amber-400/10 px-2 py-1 font-mono text-xs font-semibold text-amber-200">{symbol}</span>
              <span className="text-[10px] font-semibold uppercase tracking-[0.18em] text-text-muted">Exness · Tài khoản demo</span>
            </div>
            <h2 className="flex items-center gap-2.5 text-lg font-bold sm:text-xl">
              <span className={`flex size-7 shrink-0 items-center justify-center rounded-lg border ${toneClass[status.tone]}`}><Activity size={16} aria-hidden="true" /></span>
              {status.title}
            </h2>
            <p className="max-w-2xl text-sm leading-relaxed text-text-secondary">{status.reason}</p>
            {status.hint && <details className="max-w-2xl text-xs text-text-muted"><summary className="w-fit py-1 underline decoration-border-active underline-offset-4 hover:text-text-primary">Hướng dẫn xử lý</summary><p className="mt-2 break-words rounded-lg border border-border-base bg-canvas/60 p-3 leading-relaxed">{status.hint}</p></details>}
          </div>
          <div className="flex w-full flex-wrap gap-2 sm:w-auto sm:flex-col sm:items-stretch">
            <button type="button" className="inline-flex min-h-10 items-center justify-center gap-2 rounded-lg bg-bull px-4 py-2 text-xs font-semibold text-canvas transition-colors hover:bg-emerald-300" onClick={() => setRoute({ page: 'Bot' })}>
              <ChartNoAxesCombined size={16} aria-hidden="true" /> Mở chart & Bot <ArrowUpRight size={14} aria-hidden="true" />
            </button>
            <button type="button" className="inline-flex min-h-10 items-center justify-center gap-2 rounded-lg border border-border-active bg-surface-2 px-4 py-2 text-xs font-medium text-text-secondary transition-colors hover:bg-surface-3" onClick={() => setRoute({ page: 'Kết nối sàn' })}>
              <Radio size={14} aria-hidden="true" /> Kiểm tra kết nối
            </button>
          </div>
        </div>
        <div className="grid gap-2 border-t border-border-base bg-canvas/30 p-3 text-xs sm:grid-cols-3 sm:p-4" aria-label="Sức khỏe hệ thống">
          <div className={`flex items-start gap-2.5 rounded-xl border p-3 ${toneClass[executor.tone]}`}>
            <Server size={16} className="mt-0.5 shrink-0" aria-hidden="true" /><div><span className="block text-[11px] text-text-muted">Worker thực thi</span><strong className="mt-1 block font-medium">{executor.text}</strong></div>
          </div>
          <div className={`flex items-start gap-2.5 rounded-xl border p-3 ${toneClass[workerTone]}`}>
            <Layers size={16} className="mt-0.5 shrink-0" aria-hidden="true" /><div><span className="block text-[11px] text-text-muted">Worker nến</span><strong className="mt-1 block font-medium">{disconnected ? 'Chưa xác minh' : dashboard.market.worker_online ? 'Đang kết nối' : 'Offline'}</strong></div>
          </div>
          <button type="button" className={`flex min-w-0 items-start gap-2.5 rounded-xl border p-3 text-left transition-colors hover:border-border-active ${toneClass[feedState]}`} onClick={() => setRoute({ page: 'Kết nối sàn' })}>
            <Radio size={16} className="mt-0.5 shrink-0" aria-hidden="true" /><span className="min-w-0"><span className="block text-[11px] text-text-muted">Feed {symbol} · Xem kết nối</span><strong className="mt-1 block break-words font-medium">{feedState === 'idle' ? 'Chưa xác minh' : feedIssue ? `${feedIssue.timeframe.toUpperCase()} · ${feedText(feedIssue)}` : 'Đã đồng bộ'}</strong></span>
          </button>
        </div>
        <div className="border-t border-border-subtle px-4 py-3 text-text-muted sm:px-5"><ForexSessionStatus session={dashboard.market_session} /></div>
      </section>

      <div className="grid grid-cols-1 gap-3 min-[380px]:grid-cols-2 xl:grid-cols-4 [&>div]:flex [&>div]:min-w-0 [&>div]:flex-col [&>div]:gap-2 [&>div]:rounded-xl [&>div]:border [&>div]:border-border-base [&>div]:bg-surface-1 [&>div]:p-4 [&_small]:flex [&_small]:items-center [&_small]:justify-between [&_small]:gap-2 [&_small]:text-xs [&_small]:text-text-muted [&_strong]:break-words [&_strong]:font-mono [&_strong]:text-2xl [&_strong]:font-semibold [&_strong]:tabular-nums [&_span]:text-xs [&_span]:text-text-muted [&_em]:text-sm [&_em]:not-italic [&_em]:text-text-muted" aria-label="Tài khoản Exness demo">
        <div>
          <small>Số dư · USD <Wallet size={15} className="text-sky-300" aria-hidden="true" /></small>
          <strong>{amount(balance)}</strong>
          <span>Margin đã dùng {amount(data?.account_state?.used_margin)}</span>
        </div>
        <div>
          <small>Equity · USD <Activity size={15} className="text-sky-300" aria-hidden="true" /></small>
          <strong>{amount(equity)}</strong>
          <p className={`text-xs ${signClass(floating)}`}>
            Thả nổi {floating != null && floating > 0 ? '+' : ''}
            {amount(floating)}
          </p>
        </div>
        <div>
          <small>Lợi nhuận đã chốt · USD <TrendingUp size={15} className="text-amber-200" aria-hidden="true" /></small>
          <strong className={signClass(data?.broker_profit ?? null)}>
            {amount(data?.broker_profit)}
          </strong>
          <span>{amount(data?.closed_count)} lệnh đóng · chưa gồm swap</span>
        </div>
        <div>
          <small>Setup đang mở <Layers size={15} className="text-bull" aria-hidden="true" /></small>
          <strong>
            {data?.account_setup_count ?? '—'}
            <em> / {data?.max_concurrent ?? '—'}</em>
          </strong>
          <span>
            {symbol}: {data?.symbol_setup_count ?? '—'}/{data?.max_concurrent_per_symbol ?? '—'} setup · {data?.open_count ?? '—'} ticket toàn tài khoản
          </span>
        </div>
      </div>

      <div className="grid min-w-0 items-start gap-4 xl:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
        <section className="min-w-0 space-y-4 rounded-2xl border border-border-base bg-surface-1 p-4 sm:p-5" aria-label="Quần thể chiến thuật">
          <header className="flex flex-wrap items-center justify-between gap-3"><div className="flex items-center gap-3"><span className="flex size-9 items-center justify-center rounded-xl border border-sky-400/20 bg-sky-400/10 text-sky-300"><GitBranch size={18} aria-hidden="true" /></span><div><h2 className="text-sm font-semibold">Quần thể chiến thuật</h2><p className="mt-0.5 text-xs text-text-muted">HTF AST V3 · {symbol}</p></div></div><span className="rounded-md border border-border-base bg-surface-2 px-2 py-1 font-mono text-xs text-sky-300">G{ast?.generation ?? '—'} · {treeCount} cây</span></header>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 [&>div]:flex [&>div]:flex-col [&>div]:gap-2 [&>div]:rounded-xl [&>div]:border [&>div]:border-border-subtle [&>div]:bg-surface-2/60 [&>div]:p-4 [&_strong]:font-mono [&_strong]:text-2xl [&_strong]:tabular-nums [&_span]:text-xs [&_span]:font-medium [&_span]:text-text-secondary [&_small]:text-[11px] [&_small]:leading-relaxed [&_small]:text-text-muted [&_em]:text-sm [&_em]:not-italic [&_em]:text-text-muted">
            <div>
              <strong className="text-bull">
                {v2?.entry_allowed_count ?? '—'}
                <em>/{treeCount}</em>
              </strong>
              <span>Cây được phép vào lệnh</span>
              <small>Cây có tín hiệu vẫn phải qua kiểm tra thực thi và giới hạn rủi ro.</small>
            </div>
            <div>
              <strong className="text-sky-300">
                {v2?.evolution_ready_count ?? '—'}
                <em>/{treeCount}</em>
              </strong>
              <span>Đủ chuẩn tiến hoá</span>
              <small>Cần ≥10 mẫu forward và R trung bình &gt; 0 để làm bố mẹ.</small>
            </div>
          </div>
          <dl className="grid grid-cols-1 gap-x-5 sm:grid-cols-2 [&>div]:min-w-0 [&>div]:border-t [&>div]:border-border-subtle [&>div]:py-3 [&>div:last-child]:col-span-full [&_dt]:mb-1.5 [&_dt]:text-[11px] [&_dt]:text-text-muted [&_dd]:break-words [&_dd]:text-xs [&_dd]:leading-relaxed">
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
            className="inline-flex min-h-10 w-full items-center justify-center gap-2 rounded-lg border border-sky-400/20 bg-sky-400/5 px-3 py-2 text-xs font-medium text-sky-300 transition-colors hover:bg-sky-400/10"
            onClick={() => setRoute({ page: 'Bot', tab: 'learning' })}
          >
            Xem cây & dòng dõi <ArrowUpRight size={14} aria-hidden="true" />
          </button>
        </section>
        <section className="min-w-0 space-y-4 rounded-2xl border border-border-base bg-surface-1 p-4 sm:p-5" aria-label="Quyết định gần nhất">
          <header className="flex items-center gap-3"><span className="flex size-9 items-center justify-center rounded-xl border border-amber-400/20 bg-amber-400/10 text-amber-200"><History size={18} aria-hidden="true" /></span><div><h2 className="text-sm font-semibold">Quyết định gần nhất</h2><p className="mt-0.5 text-xs text-text-muted">Nhật ký bot · {symbol}</p></div></header>
          <p className="break-words rounded-xl border border-border-base bg-surface-2/60 p-3 text-sm font-medium leading-relaxed text-text-primary">{data?.decision ?? 'Chưa có quyết định'}</p>
          <dl className="space-y-3 border-l border-border-active pl-4 [&>div]:min-w-0 [&_dt]:mb-1 [&_dt]:text-[11px] [&_dt]:text-text-muted [&_dd]:break-words [&_dd]:text-xs [&_dd]:leading-relaxed [&_dd]:text-text-secondary">
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
            className="inline-flex min-h-10 w-full items-center justify-center gap-2 rounded-lg border border-border-active bg-surface-2 px-3 py-2 text-xs font-medium text-text-secondary transition-colors hover:bg-surface-3"
            onClick={() => setRoute({ page: 'Bot', tab: 'learning' })}
          >
            Xem nhật ký quyết định <ArrowUpRight size={14} aria-hidden="true" />
          </button>
        </section>
      </div>

      <ForexMarkets symbol={symbol} variant="table" />
    </div>
  );
}
