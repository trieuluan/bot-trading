import {
  Activity,
  ArrowLeftRight,
  Bot,
  ChevronDown,
  CircleHelp,
  LayoutDashboard,
  Link2,
  PanelLeftClose,
  PanelLeftOpen,
  ShieldCheck,
} from 'lucide-react';
import { useEffect, useState, type ReactNode } from 'react';
import { setRoute } from '../lib/router';

const exnessNavigation = [
  { label: 'Tổng quan', icon: LayoutDashboard },
  { label: 'Bot', icon: Bot },
  { label: 'Kết nối sàn', icon: Link2 },
];

const navigation = [
  { label: 'Giao dịch & Bot', icon: LayoutDashboard },
  { label: 'Cài đặt & Kết nối', icon: Link2 },
];

interface SidebarProps {
  page: string;
  menu: boolean;
  onNavigate: (page: string) => void;
  onOpenHelp: () => void;
  workspacePicker?: ReactNode;
  allowedPages?: string[];
  botCount?: number;
  exness?: boolean;
}

export function Sidebar({
  page,
  menu,
  onNavigate,
  onOpenHelp,
  workspacePicker,
  allowedPages,
  botCount = 1,
  exness = false,
}: SidebarProps) {
  const [collapsed, setCollapsed] = useState(() => {
    try {
      return localStorage.getItem('sidebar') === 'collapsed';
    } catch {
      return false;
    }
  });
  useEffect(() => {
    document.documentElement.dataset.sidebar = collapsed ? 'collapsed' : 'open';
    try {
      localStorage.setItem('sidebar', collapsed ? 'collapsed' : 'open');
    } catch {}
  }, [collapsed]);
  return (
    <aside className={menu ? 'sidebar open' : 'sidebar'}>
      <button
        type="button"
        className="sidebar-toggle"
        aria-label={collapsed ? 'Mở rộng sidebar' : 'Thu gọn sidebar'}
        aria-expanded={!collapsed}
        title={collapsed ? 'Mở rộng sidebar' : 'Thu gọn sidebar'}
        onClick={() => setCollapsed((value) => !value)}
      >
        {collapsed ? <PanelLeftOpen size={15} /> : <PanelLeftClose size={15} />}
      </button>
      <a
        className="brand"
        href="#"
        onClick={(e) => {
          e.preventDefault();
          onNavigate(exness ? 'Tổng quan' : 'Giao dịch & Bot');
        }}
      >
        <div className="brand-mark">
          <Activity size={25} />
        </div>
        <span>
          signal<span className="brand-light">desk</span>
          <small>TRADING WORKSPACE</small>
        </span>
      </a>
      {workspacePicker ?? (
        <div className="workspace">
          <div className="avatar">L</div>
          <div>
            Personal workspace<small>Không gian cá nhân</small>
          </div>
          <ChevronDown size={14} />
        </div>
      )}
      <button
        type="button"
        className="nav workspace-collapse-switch"
        aria-label={exness ? 'Đổi sang MEXC Crypto Futures' : 'Đổi sang Exness Forex'}
        title={
          exness
            ? 'Đang ở Exness Forex · bấm để đổi sang MEXC Crypto Futures'
            : 'Đang ở MEXC Crypto Futures · bấm để đổi sang Exness Forex'
        }
        onClick={() =>
          setRoute({
            workspace: exness ? 'mexc_futures' : 'exness_forex',
            page: undefined,
            tab: undefined,
          })
        }
      >
        <ArrowLeftRight size={15} />
        <span className="workspace-collapse-badge">{exness ? 'FX' : 'MX'}</span>
      </button>
      <p className="nav-label">WORKSPACE</p>
      <nav>
        {(exness ? exnessNavigation : navigation).map(({ label, icon: Icon }) => {
          return (
            <button
              className={page === label ? 'nav active' : 'nav'}
              aria-current={page === label ? 'page' : undefined}
              aria-label={label}
              key={label}
              onClick={() => onNavigate(label)}
              disabled={allowedPages !== undefined && !allowedPages.includes(label)}
              title={
                allowedPages !== undefined && !allowedPages.includes(label)
                  ? 'Chưa hỗ trợ trong workspace này'
                  : collapsed
                    ? label
                    : undefined
              }
            >
              <Icon size={18} />
              <span className="nav-text">{label}</span>
              {(label === 'Bot của bạn' || label === 'Bot') && (
                <span
                  className={`nav-count ${botCount ? 'is-on' : ''}`}
                  title={botCount ? `${botCount} bot đang bật` : 'Bot chưa bật'}
                  aria-label={botCount ? `${botCount} bot đang bật` : 'Bot chưa bật'}
                >
                  {botCount ? botCount : 'Tắt'}
                </span>
              )}
            </button>
          );
        })}
      </nav>
      <div className="sidebar-bottom">
        <div className="safe-card">
          <ShieldCheck size={21} />
          <h3>{exness ? 'Tài khoản demo' : 'Thử nghiệm có kiểm soát'}</h3>
          <p>
            {exness ? 'Lệnh gửi tới Exness demo.' : 'Dữ liệu thật hoặc replay.'}
            <br />
            Không dùng tiền thật.
          </p>
        </div>
        <button
          className="nav"
          onClick={onOpenHelp}
          title="Hướng dẫn sử dụng"
          aria-label="Hướng dẫn sử dụng"
        >
          <CircleHelp size={18} />
          <span className="nav-text">Hướng dẫn sử dụng</span>
        </button>
        <div className="profile">
          <div className="avatar pale">LT</div>
          <div>
            Trader workspace<small>Local development</small>
          </div>
          <span className="status-dot" />
        </div>
      </div>
    </aside>
  );
}
