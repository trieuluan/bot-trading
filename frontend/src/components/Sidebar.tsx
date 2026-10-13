import {
  Activity,
  ArrowUpRight,
  Bot,
  ChevronDown,
  CircleHelp,
  FlaskConical,
  LayoutDashboard,
  Link2,
  ShieldCheck,
  Terminal,
  Wallet,
} from 'lucide-react';
import type { ReactNode } from 'react';

const navigation = [
  { label: 'Tổng quan', icon: LayoutDashboard },
  { label: 'Bot của bạn', icon: Bot },
  { label: 'Chiến lược', icon: FlaskConical },
  { label: 'Ví mô phỏng', icon: Wallet },
  { label: 'Backtest', icon: FlaskConical },
  { label: 'Kết nối sàn', icon: Link2 },
  { label: 'Nhật ký', icon: Terminal },
];

interface SidebarProps {
  page: string;
  menu: boolean;
  onNavigate: (page: string) => void;
  onOpenHelp: () => void;
  workspacePicker?: ReactNode;
  allowedPages?: string[];
  botCount?: number;
}

export function Sidebar({
  page,
  menu,
  onNavigate,
  onOpenHelp,
  workspacePicker,
  allowedPages,
  botCount = 1,
}: SidebarProps) {
  return (
    <aside className={menu ? 'sidebar open' : 'sidebar'}>
      <a
        className="brand"
        href="#"
        onClick={(e) => {
          e.preventDefault();
          onNavigate('Tổng quan');
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
      <p className="nav-label">WORKSPACE</p>
      <nav>
        {navigation.map(({ label, icon: Icon }) => {
          return (
            <button
              className={page === label ? 'nav active' : 'nav'}
              key={label}
              onClick={() => onNavigate(label)}
              disabled={allowedPages !== undefined && !allowedPages.includes(label)}
              title={
                allowedPages !== undefined && !allowedPages.includes(label)
                  ? 'Chưa hỗ trợ trong workspace này'
                  : undefined
              }
            >
              <Icon size={18} />
              {label}
              {label === 'Bot của bạn' && <span className="nav-count">{botCount}</span>}
            </button>
          );
        })}
      </nav>
      <div className="sidebar-bottom">
        <div className="safe-card">
          <ShieldCheck size={21} />
          <h3>Thử nghiệm có kiểm soát</h3>
          <p>
            Dữ liệu thật hoặc replay.
            <br />
            Không dùng tiền thật.
          </p>
          <span>
            Chế độ mô phỏng <ArrowUpRight size={14} />
          </span>
        </div>
        <button className="nav" onClick={onOpenHelp}>
          <CircleHelp size={18} />
          Hướng dẫn sử dụng
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
