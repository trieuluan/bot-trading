import { useRoute, setRoute } from './lib/router';
import { useFuturesBoard } from './components/FuturesBoard';
import { getFuturesSymbol, setFuturesSymbol, type FuturesSymbol } from './lib/futures-symbol';
import { useEffect, useState } from 'react';
import type { ReactNode } from 'react';
import { Bell, Menu, RefreshCw, X } from 'lucide-react';
import { Sidebar } from './components/Sidebar';
import { ForexMarkets } from './components/ForexMarkets';
import { subscribeForexStream } from './lib/forex-stream';
import { useTimeZone } from './lib/time-zone';
import { TimeZoneToggle } from './components/TimeZoneToggle';
import { formatTime } from './lib/time-format';
import { HelpDialog } from './components/HelpDialog';
import { useTradingDashboard } from './hooks/useTradingDashboard';
import { BotControlPanel } from './components/BotControlPanel';
import { OverviewPage } from './pages/OverviewPage';
import { ConnectionsPage } from './pages/ConnectionsPage';
import { PaperTradingPage } from './pages/PaperTradingPage';
import { ForexCatchupStatus } from './pages/ForexStrategyPage';
import type { ObservationChartSnapshot } from './components/MtfObservationChart';
import { ForexExecutionPage } from './pages/ForexExecutionPage';
import { ForexOverviewPage } from './pages/ForexOverviewPage';
import { ForexConnectionPage } from './pages/ForexConnectionPage';
import type { ForexMarketSession } from './lib/forex-labels';
import type { ExnessExecution } from './types/exness-execution';

interface Workspace {
  id: string;
  name: string;
  venue: string;
  symbol: string;
  execution_enabled: boolean;
}

interface FrameSummary {
  timeframe: string;
  bars_per_side: number;
  first_candle: number | null;
  last_candle: number | null;
  last_sync: string | null;
  remaining_intervals: number | null;
  worker_online: boolean;
  error: string | null;
  retry_at?: string | null;
}

export interface ForexDashboard {
  workspace: Workspace;
  execution_enabled?: boolean;
  market_session?: ForexMarketSession;
  instrument?: {
    profile: {
      contract_size: string;
      point_digits: number;
      quote_to_account_loss: string;
      quote_to_account_gain: string;
      account_leverage?: string;
      margin_percent?: string;
      calc_mode?: string;
    } | null;
  };
  market: {
    status: string;
    message: string;
    bars?: number;
    last_sync?: string | null;
    worker_online?: boolean;
  };
  observation: {
    status: string;
    message: string;
    catch_up?: {
      pending_bars: number;
      last_processed_candle: number | null;
      latest_closed_candle: number | null;
    };
    snapshot?:
      | (Partial<ObservationChartSnapshot> & {
          available_at: number;
        })
      | null;
    recent?: (Partial<ObservationChartSnapshot> & {
      candle_time: number;
      status: string;
      reason: string;
    })[];
    quality?: {
      status: string;
      message: string;
      frames: {
        timeframe: string;
        paired_bars: number;
        gap_count: number;
        invalid_bars: number;
        unpaired_bars: number;
        crossed_bars: number;
        close_spread_mean: number | null;
        close_spread_max: number | null;
      }[];
    };
  };
  frames?: FrameSummary[];
}

export default function App() {
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [route] = useRoute();
  const selected = route.workspace;
  const [error, setError] = useState('');
  useEffect(() => {
    let cancelled = false;
    fetch('/api/workspaces')
      .then(async (response) => {
        if (!response.ok) throw new Error('Workspace API chưa sẵn sàng; cần chạy migration.');
        const data = (await response.json()) as { workspaces: Workspace[] };
        if (!cancelled) setWorkspaces(data.workspaces);
      })
      .catch(() => {
        if (!cancelled) setError('Chưa tải được workspace. Kiểm tra API và migration.');
      });
    return () => {
      cancelled = true;
    };
  }, []);
  const picker = (
    <div className="workspace workspace-picker m-0 grid w-full min-w-0 gap-1.5 text-xs text-text-secondary [&_small]:block [&_small]:text-text-muted md:in-[[data-sidebar=collapsed]]:hidden">
      <label htmlFor="venue-workspace" className="px-1 text-[11px] font-medium text-text-muted">Sàn giao dịch</label>
      <select
        id="venue-workspace"
        className="w-full min-w-0 rounded border border-border-base bg-surface-1 p-2 text-xs text-text-primary"
        value={selected}
        disabled={workspaces.length === 0}
        onChange={(event) =>
          setRoute({ workspace: event.target.value, page: undefined, tab: undefined })
        }
      >
        {workspaces.length === 0 && <option value="mexc_futures">MEXC · Crypto Futures</option>}
        {workspaces.map((workspace) => (
          <option key={workspace.id} value={workspace.id}>
            {workspace.name}
          </option>
        ))}
      </select>
      {error && <small role="status">{error}</small>}
    </div>
  );
  return selected === 'exness_forex' ? (
    <ForexWorkspace key={selected} workspacePicker={picker} />
  ) : (
    <MexcWorkspace key={selected} workspacePicker={picker} />
  );
}

/** Khung xương giữ bố cục trang trong lúc tải dashboard lần đầu. */
function ForexSkeleton({ page }: { page: string }) {
  return (
    <div className="forex-skeleton space-y-3 p-4" role="status" aria-label="Đang tải workspace Exness">
      {page === 'Bot' ? (
        <>
          <span className="skeleton-block min-h-5 animate-pulse rounded bg-surface-3 bg-none" style={{ height: 56 }} />
          <span
            className="skeleton-block min-h-5 animate-pulse rounded bg-surface-3 bg-none"
            style={{ height: 'clamp(420px, calc(100vh - 260px), 940px)' }}
          />
        </>
      ) : (
        <>
          <span className="skeleton-block min-h-5 animate-pulse rounded bg-surface-3 bg-none" style={{ height: 150 }} />
          <div className="forex-skeleton-row grid grid-cols-2 gap-3 sm:grid-cols-4">
            {Array.from({ length: 4 }, (_, index) => (
              <span key={index} className="skeleton-block min-h-5 animate-pulse rounded bg-surface-3 bg-none" style={{ height: 92 }} />
            ))}
          </div>
          <span className="skeleton-block min-h-5 animate-pulse rounded bg-surface-3 bg-none" style={{ height: 260 }} />
        </>
      )}
    </div>
  );
}

function ForexWorkspace({ workspacePicker }: { workspacePicker: ReactNode }) {
  const [route] = useRoute();
  // Subscribe để cả workspace render lại khi đổi múi giờ hiển thị.
  useTimeZone();
  const symbol = route.symbol ?? 'XAUUSD';
  const page = route.page;
  const setPage = (next: string) => setRoute({ page: next, tab: undefined });
  const [menu, setMenu] = useState(false);
  const [help, setHelp] = useState(false);
  const [dashboard, setDashboard] = useState<ForexDashboard | null>(null);
  const [error, setError] = useState('');
  const [revision, setRevision] = useState(0);
  useEffect(
    () =>
      subscribeForexStream(
        symbol,
        'dashboard',
        (value) => {
          const next = value as ForexDashboard;
          if (next.workspace?.id !== 'exness_forex' || next.workspace.symbol !== symbol) return;
          setDashboard(next);
          setError('');
        },
        (online, connecting) =>
          setError(online || connecting ? '' : 'Stream Exness đang kết nối lại; giữ dữ liệu cuối.'),
      ),
    [revision, symbol],
  );
  // Dữ liệu đang hiển thị là của cặp cũ: chờ dashboard cặp mới.
  const switching = !!dashboard && dashboard.workspace.symbol !== symbol;
  return (
    <div className="shell forex-shell min-h-dvh bg-canvas text-text-secondary">
      {menu && <button className="scrim fixed inset-0 z-50 bg-black/60 md:hidden" aria-label="Đóng menu" onClick={() => setMenu(false)} />}
      <Sidebar
        page={page}
        menu={menu}
        onCloseMenu={() => setMenu(false)}
        workspacePicker={workspacePicker}
        botCount={dashboard?.execution_enabled ? 1 : 0}
        exness
        allowedPages={['Tổng quan', 'Bot', 'Kết nối sàn']}
        onNavigate={(next) => {
          setPage(next);
          setMenu(false);
        }}
        onOpenHelp={() => setHelp(true)}
      />
      <div className="main min-w-0 flex-1 transition-[margin] duration-150 md:ml-52 md:in-[[data-sidebar=collapsed]]:ml-16 [&>main]:min-w-0 [&>main]:p-3 lg:[&>main]:p-5 [&>main.forex-terminal-main]:p-0 [&>main>footer]:flex [&>main>footer]:flex-wrap [&>main>footer]:gap-3 [&>main>footer]:py-6 [&>main>footer]:text-xs [&>main>footer]:text-text-muted">
        <header className="topbar relative flex min-h-12 flex-wrap items-center justify-between gap-2 border-b border-border-base bg-canvas px-3 py-2 lg:px-5">
          {switching && page !== 'Bot' && (
            <span className="route-progress pointer-events-none absolute inset-x-0 bottom-0 h-0.5 animate-pulse bg-bull" role="status" aria-label={`Đang tải ${symbol}`} />
          )}
          <div className="breadcrumb flex min-w-0 flex-wrap items-center gap-2 text-xs [&>strong]:text-text-primary">
            <button
              className="icon-button mobile-menu inline-flex size-8 shrink-0 items-center justify-center rounded text-text-muted hover:text-text-primary md:hidden"
              aria-label="Mở menu"
              onClick={() => setMenu(true)}
            >
              <Menu size={20} />
            </button>
            <span>Exness {symbol}</span>
            <span>/</span>
            <strong>{page}</strong>
          </div>
          <div className="header-actions flex min-w-0 flex-wrap items-center gap-2 text-xs [&>strong]:text-text-primary">
            <TimeZoneToggle />
            {page === 'Bot' && (
              <button
                className="icon-button inline-flex size-8 shrink-0 items-center justify-center rounded text-text-muted hover:text-text-primary"
                aria-label="Làm mới"
                title="Làm mới"
                onClick={() => setRevision((value) => value + 1)}
              >
                <RefreshCw size={15} />
              </button>
            )}
            <span className="pill amber inline-flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 text-xs font-medium bg-surface-3 text-text-secondary text-amber-300">
              {dashboard?.execution_enabled ? 'DEMO · LỆNH SÀN' : 'DEMO · READ ONLY'}
            </span>
          </div>
        </header>
        <main className={page === 'Bot' ? 'forex-terminal-main' : undefined}>
          {page !== 'Bot' && (
            <div className="page-heading forex-page-heading mb-4 flex flex-wrap items-start justify-between gap-3 [&_p]:mt-1 [&_p]:text-xs [&_p]:text-text-muted">
              <div>
                <h1>{page === 'Kết nối sàn' ? 'Kết nối sàn' : 'Tổng quan'}</h1>
                <p>
                  Exness demo · {symbol} ·{' '}
                  {page === 'Kết nối sàn'
                    ? 'tài khoản, worker và chất lượng dữ liệu'
                    : 'trạng thái bot, tài khoản và các cặp'}
                </p>
              </div>
              <button className="secondary inline-flex items-center justify-center gap-1.5 rounded-md border border-border-active bg-surface-2 px-3 py-1.5 text-xs text-text-secondary hover:bg-surface-3" onClick={() => setRevision((value) => value + 1)}>
                <RefreshCw size={15} /> Làm mới
              </button>
            </div>
          )}
          {page === 'Bot' && <ForexMarkets symbol={symbol} loading={switching && !error} />}
          {error && (
            <div className="feedback my-2 flex flex-wrap items-center justify-between gap-2 rounded-md border border-amber-400/25 bg-amber-400/5 px-3 py-2 text-xs text-amber-200 [&_p]:basis-full" role="alert">
              {error}
            </div>
          )}
          {!dashboard && !error && <ForexSkeleton page={page} />}
          {dashboard && !switching && page !== 'Kết nối sàn' && (
            <ForexCatchupStatus dashboard={dashboard} disconnected={!!error} compact />
          )}
          {dashboard && page === 'Bot' && (
            <ForexExecutionPage
              dashboard={dashboard}
              pendingSymbol={switching ? symbol : undefined}
              pendingFailed={switching && !!error}
              onRetry={() => setRevision((value) => value + 1)}
            />
          )}
          {dashboard && !switching && page === 'Tổng quan' && (
            <ForexOverviewPage
              key={dashboard.workspace.symbol}
              dashboard={dashboard}
              disconnected={!!error}
            />
          )}
          {switching && page === 'Tổng quan' && <ForexSkeleton page={page} />}
          {dashboard && page === 'Kết nối sàn' && (
            <ForexConnectionPage
              key={dashboard.workspace.symbol}
              dashboard={dashboard}
              disconnected={!!error}
            />
          )}
        </main>
      </div>
      {help && <HelpDialog onClose={() => setHelp(false)} />}
    </div>
  );
}

function MexcWorkspace({ workspacePicker }: { workspacePicker: ReactNode }) {
  useTimeZone();
  const [symbol, setSymbol] = useState(getFuturesSymbol);
  // A fresh object per click, so re-selecting the same trade still reopens its drawer.
  const [chartTrade, setChartTrade] = useState<{ id: number } | null>(null);
  const selectSymbol = (next: FuturesSymbol, tradeId: number | null = null) => {
    setFuturesSymbol(next);
    setSymbol(next);
    setChartTrade(tradeId === null ? null : { id: tradeId });
  };
  const [route] = useRoute();
  const page = route.page;
  const setPage = (next: string) => setRoute({ page: next, tab: undefined });
  const [menu, setMenu] = useState(false);
  const [filter, setFilter] = useState('all');
  const [notice, setNotice] = useState(false);
  const [help, setHelp] = useState(false);
  const { snapshot, connected, connecting, busy, message, setMessage, refresh, toggle } =
    useTradingDashboard();
  const board = useFuturesBoard(true);
  const navigate = (nextPage: string) => {
    setPage(nextPage);
    setMenu(false);
  };
  const botPanel = (
    <BotControlPanel snapshot={snapshot} connected={connected} busy={busy} onToggle={toggle} />
  );
  return (
    <div className="shell min-h-dvh bg-canvas text-text-secondary">
      {menu && <button className="scrim fixed inset-0 z-50 bg-black/60 md:hidden" aria-label="Đóng menu" onClick={() => setMenu(false)} />}
      <Sidebar
        page={page}
        menu={menu}
        onCloseMenu={() => setMenu(false)}
        workspacePicker={workspacePicker}
        onNavigate={navigate}
        onOpenHelp={() => setHelp(true)}
      />
      <div className="main min-w-0 flex-1 transition-[margin] duration-150 md:ml-52 md:in-[[data-sidebar=collapsed]]:ml-16 [&>main]:min-w-0 [&>main]:p-3 lg:[&>main]:p-5 [&>main.forex-terminal-main]:p-0 [&>main>footer]:flex [&>main>footer]:flex-wrap [&>main>footer]:gap-3 [&>main>footer]:py-6 [&>main>footer]:text-xs [&>main>footer]:text-text-muted">
        <header className="topbar relative flex min-h-12 flex-wrap items-center justify-between gap-2 border-b border-border-base bg-canvas px-3 py-2 lg:px-5">
          <div className="breadcrumb flex min-w-0 flex-wrap items-center gap-2 text-xs [&>strong]:text-text-primary">
            <button
              className="icon-button mobile-menu inline-flex size-8 shrink-0 items-center justify-center rounded text-text-muted hover:text-text-primary md:hidden"
              onClick={() => setMenu(true)}
              aria-label="Mở menu"
            >
              <Menu size={20} />
            </button>
            <span>MEXC {symbol.replace('_USDT', '/USDT')}</span>
            <span>/</span>
            <strong>{page}</strong>
          </div>
          <div className="header-actions flex min-w-0 flex-wrap items-center gap-2 text-xs [&>strong]:text-text-primary">
            <TimeZoneToggle />
            <span className={`connection ${connected || connecting ? '' : 'offline'} inline-flex items-center gap-1.5 text-xs text-text-muted [&.offline]:text-bear [&.ok]:text-bull [&.bad]:text-bear [&.warn]:text-amber-300`}>
              <span className="status-dot inline-block size-1.5 shrink-0 rounded-full bg-bull [&.stale-dot]:bg-amber-400" />
              {connected
                ? 'API kết nối'
                : connecting
                  ? 'Đang kết nối…'
                  : 'Mất kết nối · dữ liệu xem trước'}
            </span>
            <span className="pill amber inline-flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 text-xs font-medium bg-surface-3 text-text-secondary text-amber-300">PAPER MODE</span>
            <button
              className="icon-button inline-flex size-8 shrink-0 items-center justify-center rounded text-text-muted hover:text-text-primary"
              aria-label="Thông báo"
              onClick={() => setNotice(!notice)}
            >
              <Bell size={18} />
            </button>
          </div>
        </header>
        {notice && (
          <div className="notification my-2 flex flex-wrap items-center justify-between gap-2 rounded-md border border-amber-400/25 bg-amber-400/5 px-3 py-2 text-xs text-amber-200 [&_p]:basis-full">
            <strong>Trạng thái hệ thống</strong>
            <p>
              {connected
                ? 'API đã kết nối.'
                : 'API mất kết nối; hiển thị dữ liệu cuối hoặc preview ban đầu.'}{' '}
              {snapshot.bot.online ? 'Worker đang hoạt động.' : 'Worker chưa gửi heartbeat.'}
            </p>
          </div>
        )}
        <main>
          <div className="page-heading mb-4 flex flex-wrap items-start justify-between gap-3 [&_p]:mt-1 [&_p]:text-xs [&_p]:text-text-muted">
            <div>
              <h1>
                {page}{' '}
                <span className="banner-tag inline-flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 text-xs font-medium bg-surface-3 text-text-secondary">
                  {snapshot.data_source === 'mexc_futures' ? 'MEXC LIVE DATA' : 'DEMO DATA'}
                </span>
              </h1>
              <p>
                {page === 'Giao dịch & Bot'
                  ? `${symbol} perpetual · Lệnh mô phỏng nội bộ, không gửi tới sàn.`
                  : 'Nguồn dữ liệu, trạng thái worker và nhật ký hệ thống.'}
              </p>
            </div>
            <button className="secondary inline-flex items-center justify-center gap-1.5 rounded-md border border-border-active bg-surface-2 px-3 py-1.5 text-xs text-text-secondary hover:bg-surface-3" onClick={() => void refresh()}>
              <RefreshCw size={15} /> Làm mới
            </button>
          </div>
          {snapshot.data_source === 'mexc_futures' &&
            (snapshot.market.stale || snapshot.market.error || !connected) && (
              <div className="feedback my-2 flex flex-wrap items-center justify-between gap-2 rounded-md border border-amber-400/25 bg-amber-400/5 px-3 py-2 text-xs text-amber-200 [&_p]:basis-full" role="status">
                Dữ liệu đang chậm hoặc mất kết nối.{' '}
                {snapshot.market.error ?? 'Đang chờ lần đồng bộ kế tiếp.'} Biểu đồ giữ nến đã lưu;
                không thay bằng dữ liệu mẫu.
              </div>
            )}
          {message && (
            <div role="status" className="feedback my-2 flex flex-wrap items-center justify-between gap-2 rounded-md border border-amber-400/25 bg-amber-400/5 px-3 py-2 text-xs text-amber-200 [&_p]:basis-full">
              {message}
              <button
                className="icon-button inline-flex size-8 shrink-0 items-center justify-center rounded text-text-muted hover:text-text-primary"
                aria-label="Đóng thông báo"
                onClick={() => setMessage('')}
              >
                <X size={15} />
              </button>
            </div>
          )}
          {page === 'Giao dịch & Bot' &&
            (snapshot.data_source === 'mexc_futures' ? (
              <PaperTradingPage
                key={symbol}
                board={board}
                onSelect={selectSymbol}
                chartTrade={chartTrade}
              />
            ) : (
              <OverviewPage
                snapshot={snapshot}
                botPanel={botPanel}
                filter={filter}
                setFilter={setFilter}
              />
            ))}
          {page === 'Cài đặt & Kết nối' && (
            <ConnectionsPage
              snapshot={snapshot}
              connected={connected}
              connecting={connecting}
              board={board}
              onRefresh={refresh}
            />
          )}
          <footer>
            <span>
              <span className="footer-logo font-display font-semibold text-text-secondary">✳</span> SIGNAL DESK{' '}
              <span className="footer-divider hidden sm:inline">/</span> Built for deliberate trading.
            </span>
            <span>
              v0.2.0 <span className="footer-divider hidden sm:inline">·</span> Local workspace
            </span>
          </footer>
        </main>
      </div>
      {help && <HelpDialog onClose={() => setHelp(false)} />}
    </div>
  );
}
