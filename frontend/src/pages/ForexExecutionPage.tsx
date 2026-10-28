import { useRoute, setRoute } from '../lib/router';
import { useEffect, useRef, useState } from 'react';
import type { BrokerPosition, ExnessExecution } from '../types/exness-execution';
import { closeSetupPath, closeTicketPath, estimatedPnl, type PositionGroup } from '../lib/forex-position-groups';
import { ForexExecutionTabs, type ExecutionPanel, type ExecutionFilter } from '../components/ForexExecutionTabs';
import type { ForexDashboard } from '../App';
import { ForexStrategyPage } from './ForexStrategyPage';
import { formatForexLeverage, formatForexPrice } from '../lib/format';
import { formatTime } from '../lib/time-format';
import { subscribeForexStream } from '../lib/forex-stream';
import { useStickyBar } from '../hooks/useStickyBar';
import { demoEURUSDExecution } from '../lib/forex-demo-fixture';
import { Sparkles } from 'lucide-react';

const url = '/api/workspaces/exness_forex/execution';
const amount = (value: number | string | null | undefined) =>
  value == null ? '—' : Number(value).toLocaleString('vi-VN', { maximumFractionDigits: 2 });
const quotePrice = (value: number | string | null | undefined) =>
  value == null ? '—' : formatForexPrice(Number(value));
const date = (stamp: number | null) => formatTime(stamp, true);

export function ForexExecutionPage({
  dashboard,
  pendingSymbol,
  pendingFailed = false,
  onRetry,
}: {
  dashboard: ForexDashboard;
  /** Cặp vừa chọn nhưng dashboard của nó chưa về; trang đang hiển thị dữ liệu cặp cũ. */
  pendingSymbol?: string;
  /** Tải cặp `pendingSymbol` lỗi/timeout; vẫn giữ khoá thao tác, cho thử lại. */
  pendingFailed?: boolean;
  onRetry?: () => void;
}) {
  const symbol = dashboard.workspace.symbol;
  const stale = !!pendingSymbol;
  // Đọc trong action() để chặn cả thao tác bắt đầu trước khi đổi cặp (vd. sau hộp confirm).
  const staleRef = useRef(stale);
  staleRef.current = stale;
  const [snapshot, setData] = useState<ExnessExecution | null>(null);
  const data = snapshot?.symbol === symbol ? snapshot : null;
  const [selectedId, setSelectedId] = useState<string>();
  // 'setup': nút Xem chart của card (cả TP1 + Runner). 'ticket': chọn từ drawer/#id/chart (đúng ticket đó).
  const [chartScope, setChartScope] = useState<'ticket' | 'setup'>('ticket');
  const [offset, setOffset] = useState(0);
  const [revision, setRevision] = useState(0);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [executionOnline, setExecutionOnline] = useState(false);
  const [liveQuote, setLiveQuote] = useState<{ symbol: string; bid: number; ask: number; time: number } | null>(null);
  const [route] = useRoute();
  const [accountRef, accountStuck] = useStickyBar<HTMLDivElement>('--forex-account-h');
  const [demoMode, setDemoMode] = useState(false);
  // Chart luôn hiển thị; tab cũ `chart` rơi về `orders` để link cũ vẫn chạy.
  const panel: ExecutionPanel = route.tab === 'learning' ? 'learning' : 'orders';
  const setPanel = (next: ExecutionPanel) => setRoute({ tab: next });
  const [filter, setFilter] = useState<ExecutionFilter>('all');
  // Workspace không remount khi đổi cặp: reset phân trang và vị thế đang chọn.
  const [shownSymbol, setShownSymbol] = useState(symbol);
  if (shownSymbol !== symbol) {
    setShownSymbol(symbol);
    setOffset(0);
    setSelectedId(undefined);
  }
  useEffect(() => {
    setData(null);
    return subscribeForexStream(
      symbol,
      'execution',
      (value) => {
        const next = value as ExnessExecution;
        if (next.symbol !== symbol) return;
        setData(next);
      },
      (online, connecting) => {
        setExecutionOnline(online);
        setError(
          online || connecting ? '' : 'Stream lệnh sàn đang kết nối lại; dữ liệu có thể đã cũ.',
        );
      },
      offset,
      'setups',
      filter,
    );
  }, [offset, revision, symbol, filter]);
  async function action(path: string, method: string, body?: object) {
    if (staleRef.current) return;
    setBusy(true);
    try {
      const response = await fetch(
        `${url}${path}${path.includes('?') ? '&' : '?'}symbol=${symbol}`,
        {
          method,
          headers: { 'Content-Type': 'application/json' },
          body: body ? JSON.stringify(body) : undefined,
        },
      );
      if (!response.ok) throw new Error();
      setRevision((value) => value + 1);
    } catch {
      setError('Yêu cầu chưa thành công. Kiểm tra kết nối và trạng thái executor.');
    } finally {
      setBusy(false);
    }
  }
  const rows = data?.groups ?? [];
  const openGroups = data?.open_groups ?? [];
  const effectiveData = demoMode ? demoEURUSDExecution : data;
  const chartGroups = [...openGroups, ...rows.filter((group) => !openGroups.some((opened) => opened.id === group.id))];
  const chartPositions = chartGroups.flatMap((group) => group.legs);
  const chartPosition = chartPositions.find((position) => position.position_id === selectedId);
  const closeDisabled = busy || stale || !!error || !executionOnline || !data?.worker_online;
  function closeTicket(position: BrokerPosition) {
    const path = closeTicketPath(position);
    if (!path || closeDisabled || position.close_requested) return;
    if (window.confirm(`Đóng ticket demo #${position.position_id} (${symbol}) theo giá thị trường? Các ticket khác giữ nguyên.`)) void action(path, 'POST');
  }
  function closeSetup(group: PositionGroup) {
    const path = closeSetupPath(group);
    if (!path || closeDisabled || group.close_requested) return;
    const tickets = group.legs.filter((leg) => leg.status === 'open').map((leg) => `#${leg.position_id}`).join(', ');
    if (window.confirm(`Đóng cả setup demo ${symbol}, các ticket đang mở: ${tickets}, theo giá thị trường?`)) void action(path, 'POST');
  }
  const pnlOf = (position: BrokerPosition) => !error && !stale && liveQuote?.symbol === symbol && Date.now() / 1000 - liveQuote.time <= 15
    ? estimatedPnl(position, position.side === 'buy' ? liveQuote.bid : liveQuote.ask, dashboard.instrument?.profile) : null;
  const picked = data?.learning?.latest?.selected;
  const state = !data?.worker_online ? 'offline' : data.enabled ? 'live' : 'paused';
  const label = { offline: 'Worker offline', live: 'Đang chạy', paused: 'Tạm dừng mở lệnh' }[state];
  const equity = data?.account_state?.equity;
  const balance = data?.account_state?.balance;
  const floating = equity != null && balance != null ? Number(equity) - Number(balance) : null;
  return (
    <div className={`execution-desk trade-terminal${stale ? ' is-stale' : ''}`} aria-busy={stale}>
      <div
        ref={accountRef}
        className={`terminal-account-bar${accountStuck ? ' is-stuck shadow-lg shadow-black/20' : ''} sticky top-[var(--forex-strip-h,56px)] z-20 flex min-w-0 flex-col gap-2.5 border-b border-border-base bg-surface-1/95 px-3 py-2 backdrop-blur-md sm:px-4 lg:flex-row lg:items-center lg:justify-between`}
        aria-label="Tài khoản Exness Demo"
      >
        <dl className="m-0 grid min-w-0 flex-1 grid-cols-2 gap-x-4 gap-y-1 text-xs sm:grid-cols-[repeat(3,minmax(0,1fr))_max-content] lg:max-w-2xl [&_dt]:text-text-muted [&_dd]:m-0 [&_dd]:mt-0.5 [&_dd]:font-mono [&_dd]:font-semibold [&_dd]:tabular-nums">
          <div><dt>Số dư · USD</dt><dd className="text-text-primary">{amount(balance)}</dd></div>
          <div><dt>Vốn thực · USD</dt><dd className="text-text-primary">{amount(equity)}</dd></div>
          <div><dt>P&amp;L thả nổi · USD</dt><dd className={floating == null || floating === 0 ? 'text-text-primary' : floating > 0 ? 'text-bull' : 'text-bear'}>{floating != null && floating > 0 ? '+' : ''}{amount(floating)}</dd></div>
          <div className="col-span-2 sm:col-span-1"><dt>Setup {symbol}</dt><dd className="text-text-primary whitespace-nowrap">{data?.symbol_setup_count ?? '—'} / {data?.max_concurrent_per_symbol ?? '—'} <span className="font-sans font-normal text-text-muted">· TK {data?.account_setup_count ?? '—'}/{data?.max_concurrent ?? '—'} ({data?.open_count ?? '—'} ticket)</span></dd></div>
        </dl>
        <div className="flex min-w-0 flex-wrap items-center justify-end gap-2 lg:shrink-0">
          <div className="flex items-center gap-1.5">
            <span className="text-xs font-semibold text-text-primary">Điều khiển bot</span>
            <span className="rounded border border-sky-400/20 bg-sky-400/10 px-1.5 py-0.5 text-[10px] font-semibold tracking-wide text-sky-300">DEMO</span>
            <span
              className={`badge-status badge-${state} inline-flex items-center gap-1.5 text-xs ${!data || state === 'offline' ? 'text-text-muted' : state === 'live' ? 'text-bull' : 'text-amber-200'}`}
              title={`Đồng bộ sàn: ${date(data?.synced_at ?? null)}`}
            >
              <span aria-hidden="true" className={`size-1.5 rounded-full ${!data || state === 'offline' ? 'bg-text-muted' : state === 'live' ? 'bg-bull' : 'bg-amber-400'}`} />
              {data ? label : 'Đang tải…'}
            </span>
          </div>
          <button
            type="button"
            className={`inline-flex min-h-9 shrink-0 items-center justify-center gap-1.5 rounded-lg border px-3 text-xs font-semibold transition-colors focus-visible:outline-2 focus-visible:outline-bull ${
              demoMode
                ? 'border-sky-400/40 bg-sky-400/15 text-sky-200 shadow-sm'
                : 'border-border-base bg-surface-2 text-text-secondary hover:bg-surface-3 hover:text-text-primary'
            }`}
            onClick={() => setDemoMode((v) => !v)}
            title="Bật/tắt dữ liệu mẫu để xem trực quan đầy đủ 2 tab khi tài khoản sàn chưa có lệnh"
          >
            <Sparkles size={14} className={demoMode ? 'text-sky-300' : 'text-text-muted'} aria-hidden="true" />
            {demoMode ? 'Tắt dữ liệu mẫu' : 'Bật dữ liệu mẫu (UI Demo)'}
          </button>
          <button
            type="button"
            className={`terminal-toggle inline-flex min-h-9 shrink-0 items-center justify-center gap-2 rounded-lg border px-3 text-xs font-semibold transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-bull disabled:cursor-not-allowed disabled:opacity-50 ${data?.enabled ? 'is-on border-amber-400/30 bg-amber-400/10 text-amber-200 hover:bg-amber-400/20' : 'border-bull/30 bg-bull/10 text-bull hover:bg-bull/20'}`}
            disabled={busy || stale || !data?.configured || !!error}
            onClick={() => void action('', 'PATCH', { enabled: !data?.enabled })}
            title={stale ? 'Đang đổi cặp; thao tác tạm khoá' : !data?.configured ? 'Chưa đủ cấu hình kết nối tài khoản demo' : error ? 'Kết nối gián đoạn; thao tác tạm khoá' : `Tài khoản ${data?.account ?? '—'} · ${data?.lot ?? 'Chưa có dữ liệu sizing'}`}
          >
            {busy ? 'Đang xử lý…' : data?.enabled ? 'Tạm dừng mở lệnh' : 'Bật bot demo'}
          </button>
        </div>
      </div>
      <details className="group/account border-b border-border-subtle bg-surface-1 px-3 text-xs sm:px-4">
        <summary className="flex min-h-10 cursor-pointer list-none flex-wrap items-center gap-x-3 gap-y-1 py-2 text-text-muted [&::-webkit-details-marker]:hidden">
          <span className="font-medium text-text-secondary">Rủi ro &amp; tài khoản</span>
          <span className="font-mono tabular-nums text-amber-200">{data?.risk_percent ?? '—'}% / setup</span>
          <span className="ml-auto inline-flex items-center gap-2">Chi tiết <span aria-hidden="true" className="transition-transform group-open/account:rotate-180">⌄</span></span>
        </summary>
        <dl className="m-0 grid grid-cols-2 gap-3 border-t border-border-subtle py-3 sm:grid-cols-4 [&_dt]:text-text-muted [&_dd]:m-0 [&_dd]:mt-1 [&_dd]:font-mono [&_dd]:tabular-nums [&_dd]:text-text-primary">
          <div title="Ngân sách rủi ro tới SL cho cả setup, chia giữa các ticket. Chưa bao gồm phí, swap và trượt giá; không phải tiền ký quỹ."><dt>Rủi ro / setup</dt><dd>{data?.risk_percent ?? '—'}% · ${amount(data?.planned_risk_ceiling_usd)}</dd></div>
          <div title={`Đòn bẩy cấu hình ${symbol} từ thông số broker; chưa xác minh thay đổi HMR theo tin tức/phiên. Không dùng đòn bẩy để nhân ngân sách rủi ro.`}><dt>Đòn bẩy {symbol} · cấu hình</dt><dd>{formatForexLeverage(dashboard.instrument?.profile)}</dd></div>
          <div><dt>Ký quỹ đã dùng · USD</dt><dd>{amount(data?.account_state?.used_margin)}</dd></div>
          <div title="Profit deal sàn · chưa xác minh phí/swap"><dt>Profit đã chốt · USD</dt><dd>{amount(data?.broker_profit)}</dd></div>
        </dl>
        <p className="m-0 pb-3 leading-relaxed text-text-muted">Tạm dừng chỉ ngừng mở lệnh mới, không đóng vị thế đang mở.</p>
      </details>
      {error && (
        <p role="alert" className="feedback my-2 flex flex-wrap items-center justify-between gap-2 rounded-md border border-amber-400/25 bg-amber-400/5 px-3 py-2 text-xs text-amber-200 [&_p]:basis-full">
          {error}
        </p>
      )}
      {stale ? (
        <div
          className={`terminal-decision is-switching${pendingFailed ? ' is-failed' : ''} border-b border-border-base px-3 py-2 text-xs text-amber-200 [&.is-failed]:text-bear`}
          role={pendingFailed ? 'alert' : 'status'}
        >
          {!pendingFailed && <span className="switching-spinner inline-block size-3 animate-spin rounded-full border border-bull border-t-transparent" aria-hidden="true" />}
          <strong>
            {pendingFailed ? `Chưa tải được ${pendingSymbol}` : `Đang tải ${pendingSymbol}…`}
          </strong>
          <span>Số liệu bên dưới vẫn là {symbol}; bật/tắt bot và đóng lệnh tạm khoá.</span>
          {pendingFailed && onRetry && (
            <button type="button" className="switching-retry inline-flex items-center gap-1 text-xs text-bull underline-offset-4 hover:underline" onClick={onRetry}>
              Thử lại
            </button>
          )}
        </div>
      ) : (
        data && (
          <div className="terminal-decision flex flex-wrap items-baseline gap-2 border-b border-border-base px-3 py-2 text-xs text-amber-200 [&.is-failed]:text-bear [&>strong]:text-text-secondary [&>span:first-of-type]:overflow-visible [&>span:first-of-type]:whitespace-normal [&>span:first-of-type]:break-words" role="status">
            <strong>Bot:</strong>
            <span>
              {data.decision ?? 'Chờ worker xác minh demo và đồng bộ sàn'}
              {data.error ? ` · Lỗi: ${data.error}` : ''}
            </span>
            <span className="muted text-text-muted">
              {data.learning?.latest?.mode ?? '—'} · {picked ?? 'không có tín hiệu chọn'}
            </span>
          </div>
        )
      )}
      <section
        id="execution-chart"
        className="execution-chart-panel relative min-w-0 overflow-clip bg-canvas"
        aria-label={`Chart ${symbol}`}
      >
        <ForexStrategyPage
          dashboard={dashboard}
          disconnected={!!error}
          executions={chartPositions}
          positionGroups={chartGroups}
          selectedPosition={chartPosition}
          onQuote={setLiveQuote}
          chartScope={chartScope}
          onSelectPosition={(id) => { setChartScope('ticket'); setSelectedId(id); }}
          executionOnly
          includeReplay={false}
          onClosePosition={closeTicket}
        />
      </section>
      {demoMode && (
        <div className="mx-3 mt-3 flex items-center justify-between gap-3 rounded-lg border border-sky-400/30 bg-sky-400/10 px-3.5 py-2 text-xs text-sky-200">
          <div className="flex items-center gap-2">
            <Sparkles size={15} className="shrink-0 text-sky-300" aria-hidden="true" />
            <span>
              <strong>Chế độ xem trước UI · EURUSD:</strong> Lệnh và nhật ký là dữ liệu giả. Quần thể HTF và pilot dùng snapshot VPS ngày 27/10/2026, không cập nhật trực tiếp. Mọi thao tác giao dịch bị khóa.
            </span>
          </div>
          <button
            type="button"
            className="shrink-0 rounded px-2 py-1 text-[11px] font-semibold text-sky-300 underline underline-offset-2 hover:bg-sky-400/20"
            onClick={() => setDemoMode(false)}
          >
            Quay lại sàn thật
          </button>
        </div>
      )}
      {!effectiveData && !error && (
        <div className="m-3 flex min-h-32 flex-col items-center justify-center gap-2 rounded-xl border border-dashed border-border-base bg-surface-1 p-6 text-center text-xs text-text-muted">
          <p>Đang tải lệnh thật từ tài khoản demo...</p>
          <button
            type="button"
            className="inline-flex items-center gap-1.5 rounded-lg border border-sky-400/30 bg-sky-400/10 px-3 py-1.5 font-medium text-sky-300 hover:bg-sky-400/20"
            onClick={() => setDemoMode(true)}
          >
            <Sparkles size={14} aria-hidden="true" />
            Xem trước với dữ liệu mẫu ngay
          </button>
        </div>
      )}
      {effectiveData && (
        <ForexExecutionTabs data={effectiveData} panel={panel} setPanel={setPanel} filter={filter} setFilter={setFilter}
          offset={offset} setOffset={setOffset} selectedId={chartPosition?.position_id}
          onSelect={(id, scope = 'ticket') => { setChartScope(scope); setSelectedId(id); document.getElementById('execution-chart')?.scrollIntoView({ behavior: 'smooth', block: 'start' }); }}
          closeTicket={demoMode ? () => {} : closeTicket} closeSetup={demoMode ? () => {} : closeSetup} closeDisabled={demoMode || closeDisabled} pnlOf={pnlOf} preview={demoMode} />
      )}
    </div>
  );
}
