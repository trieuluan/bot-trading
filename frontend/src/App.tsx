import { useState } from 'react';
import { Bell, FlaskConical, Menu, RefreshCw, X } from 'lucide-react';
import { Sidebar } from './components/Sidebar';
import { HelpDialog } from './components/HelpDialog';
import { useTradingDashboard } from './hooks/useTradingDashboard';
import { BotControlPanel } from './components/BotControlPanel';
import { OverviewPage } from './pages/OverviewPage';
import { BotsPage } from './pages/BotsPage';
import { StrategyPage } from './pages/StrategyPage';
import { ConnectionsPage } from './pages/ConnectionsPage';
import { ActivityPage } from './pages/ActivityPage';

export default function App() {
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
      <Sidebar page={page} menu={menu} onNavigate={navigate} onOpenHelp={() => setHelp(true)} />
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
                : 'Chưa kết nối API; biểu đồ đang dùng dữ liệu preview.'}{' '}
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
              <strong>Đang ở môi trường mô phỏng.</strong> Giá và tín hiệu là dữ liệu tổng hợp; chưa
              kết nối sàn, chưa có lệnh thực.
            </span>
            <span className="banner-tag">DEMO DATA</span>
          </div>
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
          {page === 'Bot của bạn' && <BotsPage snapshot={snapshot} botPanel={botPanel} />}
          {page === 'Chiến lược' && <StrategyPage />}
          {page === 'Kết nối sàn' && <ConnectionsPage />}
          {page === 'Nhật ký' && <ActivityPage snapshot={snapshot} />}
          <footer>
            <span>
              <span className="footer-logo">✳</span> SIGNAL DESK{' '}
              <span className="footer-divider">/</span> Built for deliberate trading.
            </span>
            <span>
              v0.1.0 <span className="footer-divider">·</span> Local workspace
            </span>
          </footer>
        </main>
      </div>
      {help && <HelpDialog onClose={() => setHelp(false)} />}
    </div>
  );
}
