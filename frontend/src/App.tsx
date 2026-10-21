import { useRoute, setRoute } from './lib/router';
import { useFuturesBoard } from './components/FuturesBoard';
import { getFuturesSymbol, setFuturesSymbol, type FuturesSymbol } from './lib/futures-symbol';
import { useEffect, useState } from 'react';
import type { ReactNode } from 'react';
import { Bell, Menu, RefreshCw, X } from 'lucide-react';
import { Sidebar } from './components/Sidebar';
import { ForexMarkets } from './components/ForexMarkets';
import { cachedDashboard, fetchDashboard } from './lib/forex-cache';
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
  instrument?: {
    profile: {
      contract_size: string;
      point_digits: number;
      quote_to_account_loss: string;
      quote_to_account_gain: string;
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
    <div className="workspace workspace-picker">
      <label htmlFor="venue-workspace">Workspace / Sàn</label>
      <select
        id="venue-workspace"
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
    <div className="forex-skeleton" role="status" aria-label="Đang tải workspace Exness">
      {page === 'Bot' ? (
        <>
          <span className="skeleton-block" style={{ height: 56 }} />
          <span
            className="skeleton-block"
            style={{ height: 'clamp(420px, calc(100vh - 260px), 940px)' }}
          />
        </>
      ) : (
        <>
          <span className="skeleton-block" style={{ height: 150 }} />
          <div className="forex-skeleton-row">
            {Array.from({ length: 4 }, (_, index) => (
              <span key={index} className="skeleton-block" style={{ height: 92 }} />
            ))}
          </div>
          <span className="skeleton-block" style={{ height: 260 }} />
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
  useEffect(() => {
    let cancelled = false;
    let inflight = false;
    // Giữ dashboard cũ khi đổi cặp (không gỡ trang); dùng cache nếu cặp đã xem.
    const cached = cachedDashboard(symbol);
    if (cached) setDashboard(cached);
    setError('');
    function refreshForex() {
      if (inflight) return;
      inflight = true;
      fetchDashboard(symbol)
        .then((data) => {
          if (!cancelled) {
            setDashboard(data);
            setError('');
          }
        })
        .catch(() => {
          if (!cancelled)
            setError('Mất kết nối API; giữ dữ liệu Exness cuối. Không dùng dữ liệu MEXC thay thế.');
        })
        .finally(() => {
          inflight = false;
        });
    }
    refreshForex();
    const interval = setInterval(refreshForex, 15000);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [revision, symbol]);
  return (
    <div className="shell forex-shell">
      {menu && <button className="scrim" aria-label="Đóng menu" onClick={() => setMenu(false)} />}
      <Sidebar
        page={page}
        menu={menu}
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
      <div className="main">
        <header className="topbar">
          {dashboard && dashboard.workspace.symbol !== symbol && (
            <span className="route-progress" role="status" aria-label={`Đang tải ${symbol}`} />
          )}
          <div className="breadcrumb">
            <button
              className="icon-button mobile-menu"
              aria-label="Mở menu"
              onClick={() => setMenu(true)}
            >
              <Menu size={20} />
            </button>
            <span>Exness {symbol}</span>
            <span>/</span>
            <strong>{page}</strong>
          </div>
          <div className="header-actions">
            <TimeZoneToggle />
            {page === 'Bot' && (
              <button
                className="icon-button"
                aria-label="Làm mới"
                title="Làm mới"
                onClick={() => setRevision((value) => value + 1)}
              >
                <RefreshCw size={15} />
              </button>
            )}
            <span className="pill amber">
              {dashboard?.execution_enabled ? 'DEMO · LỆNH SÀN' : 'DEMO · READ ONLY'}
            </span>
          </div>
        </header>
        <main className={page === 'Bot' ? 'forex-terminal-main' : undefined}>
          {page !== 'Bot' && (
            <div className="page-heading forex-page-heading">
              <div>
                <h1>{page === 'Kết nối sàn' ? 'Kết nối sàn' : 'Tổng quan'}</h1>
                <p>
                  Exness demo · {symbol} ·{' '}
                  {page === 'Kết nối sàn'
                    ? 'tài khoản, worker và chất lượng dữ liệu'
                    : 'trạng thái bot, tài khoản và các cặp'}
                </p>
              </div>
              <button className="secondary" onClick={() => setRevision((value) => value + 1)}>
                <RefreshCw size={15} /> Làm mới
              </button>
            </div>
          )}
          {page === 'Bot' && <ForexMarkets symbol={symbol} />}
          {error && (
            <div className="feedback" role="alert">
              {error}
            </div>
          )}
          {!dashboard && !error && <ForexSkeleton page={page} />}
          {dashboard && page !== 'Kết nối sàn' && (
            <ForexCatchupStatus dashboard={dashboard} disconnected={!!error} compact />
          )}
          {dashboard && page === 'Bot' && <ForexExecutionPage dashboard={dashboard} />}
          {dashboard && page === 'Tổng quan' && (
            <ForexOverviewPage
              key={dashboard.workspace.symbol}
              dashboard={dashboard}
              disconnected={!!error}
            />
          )}
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
  const [chartTrade, setChartTrade] = useState<number | null>(null);
  const selectSymbol = (next: FuturesSymbol, tradeId: number | null = null) => {
    setFuturesSymbol(next);
    setSymbol(next);
    setChartTrade(tradeId);
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
    <div className="shell">
      {menu && <button className="scrim" aria-label="Đóng menu" onClick={() => setMenu(false)} />}
      <Sidebar
        page={page}
        menu={menu}
        workspacePicker={workspacePicker}
        onNavigate={navigate}
        onOpenHelp={() => setHelp(true)}
      />
      <div className="main">
        <header className="topbar">
          <div className="breadcrumb">
            <button
              className="icon-button mobile-menu"
              onClick={() => setMenu(true)}
              aria-label="Mở menu"
            >
              <Menu size={20} />
            </button>
            <span>MEXC {symbol.replace('_USDT', '/USDT')}</span>
            <span>/</span>
            <strong>{page}</strong>
          </div>
          <div className="header-actions">
            <TimeZoneToggle />
            <span className={`connection ${connected || connecting ? '' : 'offline'}`}>
              <span className="status-dot" />
              {connected
                ? 'API kết nối'
                : connecting
                  ? 'Đang kết nối…'
                  : 'Mất kết nối · dữ liệu xem trước'}
            </span>
            <span className="pill amber">PAPER MODE</span>
            <button
              className="icon-button"
              aria-label="Thông báo"
              onClick={() => setNotice(!notice)}
            >
              <Bell size={18} />
            </button>
          </div>
        </header>
        {notice && (
          <div className="notification">
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
          <div className="page-heading">
            <div>
              <h1>
                {page}{' '}
                <span className="banner-tag">
                  {snapshot.data_source === 'mexc_futures' ? 'MEXC LIVE DATA' : 'DEMO DATA'}
                </span>
              </h1>
              <p>
                {page === 'Giao dịch & Bot'
                  ? `${symbol} perpetual · Lệnh mô phỏng nội bộ, không gửi tới sàn.`
                  : 'Nguồn dữ liệu, trạng thái worker và nhật ký hệ thống.'}
              </p>
            </div>
            <button className="secondary" onClick={() => void refresh()}>
              <RefreshCw size={15} /> Làm mới
            </button>
          </div>
          {snapshot.data_source === 'mexc_futures' &&
            (snapshot.market.stale || snapshot.market.error || !connected) && (
              <div className="feedback" role="status">
                Dữ liệu đang chậm hoặc mất kết nối.{' '}
                {snapshot.market.error ?? 'Đang chờ lần đồng bộ kế tiếp.'} Biểu đồ giữ nến đã lưu;
                không thay bằng dữ liệu mẫu.
              </div>
            )}
          {message && (
            <div role="status" className="feedback">
              {message}
              <button
                className="icon-button"
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
              <span className="footer-logo">✳</span> SIGNAL DESK{' '}
              <span className="footer-divider">/</span> Built for deliberate trading.
            </span>
            <span>
              v0.2.0 <span className="footer-divider">·</span> Local workspace
            </span>
          </footer>
        </main>
      </div>
      {help && <HelpDialog onClose={() => setHelp(false)} />}
    </div>
  );
}
