import { useEffect, useState } from 'react';
import type { ReactNode } from 'react';
import { Bell, FlaskConical, Menu, RefreshCw, X } from 'lucide-react';
import { Sidebar } from './components/Sidebar';
import { HelpDialog } from './components/HelpDialog';
import { useTradingDashboard } from './hooks/useTradingDashboard';
import { BotControlPanel } from './components/BotControlPanel';
import { OverviewPage } from './pages/OverviewPage';
import { BotsPage } from './pages/BotsPage';
import { StrategyPage } from './pages/StrategyPage';
import { ConnectionsPage } from './pages/ConnectionsPage';
import { PaperTradingPage } from './pages/PaperTradingPage';
import { BacktestPage } from './pages/BacktestPage';
import { ActivityPage } from './pages/ActivityPage';

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
}

interface ForexDashboard {
  workspace: Workspace;
  market: {
    status: string;
    message: string;
    bars?: number;
    last_sync?: string | null;
    worker_online?: boolean;
  };
  observation: { status: string; message: string };
  paper: { status: string; message: string };
  frames?: FrameSummary[];
}

export default function App() {
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [selected, setSelected] = useState('mexc_futures');
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
        onChange={(event) => setSelected(event.target.value)}
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

function ForexWorkspace({ workspacePicker }: { workspacePicker: ReactNode }) {
  const [page, setPage] = useState('Tổng quan');
  const [menu, setMenu] = useState(false);
  const [help, setHelp] = useState(false);
  const [dashboard, setDashboard] = useState<ForexDashboard | null>(null);
  const [error, setError] = useState('');
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    let cancelled = false;
    let inflight = false;
    setDashboard(null);
    setError('');
    function refreshForex() {
      if (inflight) return;
      inflight = true;
      void fetch('/api/workspaces/exness_forex/dashboard')
        .then(async (response) => {
          if (!response.ok) throw new Error('Không tải được workspace Exness.');
          const data = (await response.json()) as ForexDashboard;
          if (data.workspace.id !== 'exness_forex') throw new Error('Sai nguồn workspace.');
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
  }, [revision]);
  return (
    <div className="shell">
      {menu && <button className="scrim" aria-label="Đóng menu" onClick={() => setMenu(false)} />}
      <Sidebar
        page={page}
        menu={menu}
        workspacePicker={workspacePicker}
        botCount={0}
        allowedPages={['Tổng quan', 'Kết nối sàn']}
        onNavigate={(next) => {
          setPage(next);
          setMenu(false);
        }}
        onOpenHelp={() => setHelp(true)}
      />
      <div className="main">
        <header className="topbar">
          <div className="breadcrumb">
            <button
              className="icon-button mobile-menu"
              aria-label="Mở menu"
              onClick={() => setMenu(true)}
            >
              <Menu size={20} />
            </button>
            <span>Exness</span>
            <span>/</span>
            <strong>{page}</strong>
          </div>
          <span className="pill amber">DEMO · READ ONLY</span>
        </header>
        <main>
          <div className="page-heading">
            <div>
              <h1>Exness · Forex Demo</h1>
              <p>XAUUSD · Workspace riêng, không dùng ví hoặc tín hiệu MEXC.</p>
            </div>
            <button className="secondary" onClick={() => setRevision((value) => value + 1)}>
              <RefreshCw size={15} /> Làm mới
            </button>
          </div>
          <div className="demo-banner">
            <FlaskConical size={17} />
            <span>
              Chỉ đọc nến Exness. Worker dữ liệu độc lập; chưa chạy MTF hoặc gửi lệnh Exness.
            </span>
          </div>
          {error && (
            <div className="feedback" role="alert">
              {error}
            </div>
          )}
          {!dashboard && !error && <p role="status">Đang tải workspace Exness...</p>}
          {dashboard && (
            <section className="panel forex-status">
              <h2>{page === 'Kết nối sàn' ? 'Kết nối Exness' : 'Trạng thái workspace'}</h2>
              <p>
                <strong>Thị trường:</strong> {dashboard.market.message}
              </p>
              {dashboard.market.last_sync && (
                <p>
                  <strong>Đồng bộ gần nhất:</strong>{' '}
                  {new Date(dashboard.market.last_sync).toLocaleString('vi-VN', {
                    timeZone: 'UTC',
                  })}{' '}
                  UTC
                </p>
              )}
              <p>
                <strong>Worker nến Exness:</strong>{' '}
                <span
                  className={`status-pill ${dashboard.market.worker_online && !error ? 'online' : 'offline'}`}
                >
                  {error
                    ? 'Chưa xác minh kết nối'
                    : dashboard.market.worker_online
                      ? 'Có heartbeat gần đây'
                      : 'Không có heartbeat gần đây'}
                </span>
              </p>
              {dashboard.frames && dashboard.frames.length > 0 && (
                <div className="frames-summary">
                  <h3>Khung nến đã đồng bộ</h3>
                  <ul>
                    {dashboard.frames.map((frame) => (
                      <li key={frame.timeframe}>
                        <strong>{frame.timeframe.toUpperCase()}:</strong>{' '}
                        {frame.bars_per_side.toLocaleString()} nến mỗi phía bid/ask
                        {frame.last_candle
                          ? ` (đến ${new Date(frame.last_candle * 1000).toISOString().slice(0, 16).replace('T', ' ')} UTC)`
                          : ' (chưa có)'}
                        {frame.remaining_intervals
                          ? ` · còn ${frame.remaining_intervals} khoảng nến cần kiểm tra`
                          : ''}
                        {frame.error ? ` · lỗi: ${frame.error}` : ''}
                      </li>
                    ))}
                  </ul>
                </div>
              )}
              <p>
                <strong>Chiến lược:</strong> {dashboard.observation.message}
              </p>
              <p>
                <strong>Ví mô phỏng:</strong> {dashboard.paper.message}
              </p>
              <p>
                Nến bid/ask lưu riêng theo workspace, cặp và khung thời gian. Không ghép nến MEXC
                hoặc tự tạo giá.
              </p>
              <p>
                Không lấp nến trong giờ nghỉ hoặc cuối tuần. Bước tiếp theo: kiểm tra lịch phiên và
                độ đầy đủ dữ liệu trước khi chạy MTF forex.
              </p>
            </section>
          )}
        </main>
      </div>
      {help && <HelpDialog onClose={() => setHelp(false)} />}
    </div>
  );
}

function MexcWorkspace({ workspacePicker }: { workspacePicker: ReactNode }) {
  const [page, setPage] = useState('Tổng quan');
  const [menu, setMenu] = useState(false);
  const [filter, setFilter] = useState('all');
  const [notice, setNotice] = useState(false);
  const [help, setHelp] = useState(false);
  const { snapshot, connected, busy, message, setMessage, refresh, toggle } = useTradingDashboard();
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
            <span>Workspace</span>
            <span>/</span>
            <strong>{page}</strong>
          </div>
          <div className="header-actions">
            <span className={`connection ${connected ? '' : 'offline'}`}>
              <span className="status-dot" />
              {connected ? 'API kết nối' : 'Preview offline'}
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
              <div className="eyebrow">
                <span /> YOUR EDGE, IN FOCUS
              </div>
              <h1>{page === 'Tổng quan' ? 'Không gian giao dịch' : page}</h1>
              <p>Theo dõi thị trường. Kiểm chứng chiến lược. Làm chủ từng quyết định.</p>
            </div>
            <button className="secondary" onClick={() => void refresh()}>
              <RefreshCw size={15} /> Làm mới
            </button>
          </div>
          <div className="demo-banner">
            <FlaskConical size={17} />
            <span>
              <strong>
                {snapshot.data_source === 'mexc_futures'
                  ? 'Dữ liệu MEXC Futures thật.'
                  : 'Dữ liệu tổng hợp.'}
              </strong>{' '}
              {snapshot.data_source === 'mexc_futures'
                ? 'BTC_USDT perpetual · lệnh mô phỏng nội bộ; không gửi lệnh tới sàn.'
                : 'Replay để thử giao diện và pipeline; không phải giá thị trường.'}
            </span>
            <span className="banner-tag">
              {snapshot.data_source === 'mexc_futures' ? 'PUBLIC MARKET DATA' : 'DEMO DATA'}
            </span>
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
          {page === 'Tổng quan' && (
            <OverviewPage
              snapshot={snapshot}
              botPanel={botPanel}
              filter={filter}
              setFilter={setFilter}
              onOpenStrategy={() => navigate('Chiến lược')}
            />
          )}
          {page === 'Bot của bạn' && (
            <BotsPage
              snapshot={snapshot}
              botPanel={botPanel}
              onOpenPaper={() => navigate('Ví mô phỏng')}
            />
          )}
          {page === 'Chiến lược' && <StrategyPage />}
          {page === 'Kết nối sàn' && (
            <ConnectionsPage snapshot={snapshot} connected={connected} onRefresh={refresh} />
          )}
          {page === 'Ví mô phỏng' && <PaperTradingPage />}
          {page === 'Backtest' && <BacktestPage connected={connected} />}
          {page === 'Nhật ký' && <ActivityPage snapshot={snapshot} />}
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
