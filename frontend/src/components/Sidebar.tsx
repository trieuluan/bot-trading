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
    <aside className={menu ? "sidebar open fixed inset-y-0 left-0 z-[60] flex w-64 -translate-x-full flex-col border-r border-border-base bg-surface-1 px-3 py-4 transition-transform [&.open]:translate-x-0 md:w-52 md:translate-x-0 md:in-[[data-sidebar=collapsed]]:w-16 md:in-[[data-sidebar=collapsed]]:px-2 [&>nav]:mt-6 [&>nav]:space-y-1" : "sidebar fixed inset-y-0 left-0 z-[60] flex w-64 -translate-x-full flex-col border-r border-border-base bg-surface-1 px-3 py-4 transition-transform [&.open]:translate-x-0 md:w-52 md:translate-x-0 md:in-[[data-sidebar=collapsed]]:w-16 md:in-[[data-sidebar=collapsed]]:px-2 [&>nav]:mt-6 [&>nav]:space-y-1"}>
      <button
        type="button"
        className="sidebar-toggle absolute right-2 top-3 hidden size-7 items-center justify-center rounded text-text-muted hover:text-text-primary md:inline-flex"
        aria-label={collapsed ? 'Mở rộng sidebar' : 'Thu gọn sidebar'}
        aria-expanded={!collapsed}
        title={collapsed ? 'Mở rộng sidebar' : 'Thu gọn sidebar'}
        onClick={() => setCollapsed((value) => !value)}
      >
        {collapsed ? <PanelLeftOpen size={15} /> : <PanelLeftClose size={15} />}
      </button>
      <a
        className="brand mb-5 flex min-h-9 items-center gap-2 pr-5 font-display text-lg font-bold text-text-primary [&>span>small]:block [&>span>small]:text-[10px] [&>span>small]:tracking-widest [&>span>small]:text-text-muted md:in-[[data-sidebar=collapsed]]:[&>span]:hidden"
        href="#"
        onClick={(e) => {
          e.preventDefault();
          onNavigate(exness ? 'Tổng quan' : 'Giao dịch & Bot');
        }}
      >
        <div className="brand-mark flex size-8 shrink-0 items-center justify-center text-bull">
          <Activity size={25} />
        </div>
        <span>
          signal<span className="brand-light font-normal text-text-muted">desk</span>
          <small>TRADING WORKSPACE</small>
        </span>
      </a>
      {workspacePicker ?? (
        <div className="workspace flex min-w-0 items-center gap-2 rounded-md border border-border-base p-2 text-xs [&_small]:block [&_small]:text-text-muted md:in-[[data-sidebar=collapsed]]:hidden">
          <div className="avatar flex size-7 shrink-0 items-center justify-center rounded bg-surface-3 text-xs font-bold text-bull">L</div>
          <div>
            Personal workspace<small>Không gian cá nhân</small>
          </div>
          <ChevronDown size={14} />
        </div>
      )}
      <button
        type="button"
        className="nav workspace-collapse-switch relative flex w-full min-w-0 items-center gap-3 rounded-md px-2 py-2.5 text-left text-xs text-text-muted hover:bg-surface-2 hover:text-text-primary [&.active]:bg-bull/10 [&.active]:text-bull md:in-[[data-sidebar=collapsed]]:justify-center mt-2"
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
        <span className="workspace-collapse-badge md:in-[[data-sidebar=collapsed]]:hidden">{exness ? 'FX' : 'MX'}</span>
      </button>
      <p className="nav-label md:in-[[data-sidebar=collapsed]]:hidden">WORKSPACE</p>
      <nav>
        {(exness ? exnessNavigation : navigation).map(({ label, icon: Icon }) => {
          return (
            <button
              className={page === label ? "nav active relative flex w-full min-w-0 items-center gap-3 rounded-md px-2 py-2.5 text-left text-xs text-text-muted hover:bg-surface-2 hover:text-text-primary [&.active]:bg-bull/10 [&.active]:text-bull md:in-[[data-sidebar=collapsed]]:justify-center bg-bull/10 text-bull" : "nav relative flex w-full min-w-0 items-center gap-3 rounded-md px-2 py-2.5 text-left text-xs text-text-muted hover:bg-surface-2 hover:text-text-primary [&.active]:bg-bull/10 [&.active]:text-bull md:in-[[data-sidebar=collapsed]]:justify-center"}
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
              <span className="nav-text md:in-[[data-sidebar=collapsed]]:hidden">{label}</span>
              {(label === 'Bot của bạn' || label === 'Bot') && (
                <span
                  className={`nav-count ${botCount ? 'is-on' : ''} inline-flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 text-xs font-medium ml-auto bg-surface-3 text-text-muted md:in-[[data-sidebar=collapsed]]:hidden`}
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
      <div className="sidebar-bottom mt-auto space-y-3 pt-6">
        <div className="safe-card border-t border-border-base pt-3 text-xs leading-relaxed text-text-muted [&>svg]:mb-2 [&>svg]:text-bull [&_h3]:text-xs md:in-[[data-sidebar=collapsed]]:hidden">
          <ShieldCheck size={21} />
          <h3>{exness ? 'Tài khoản demo' : 'Thử nghiệm có kiểm soát'}</h3>
          <p>
            {exness ? 'Lệnh gửi tới Exness demo.' : 'Dữ liệu thật hoặc replay.'}
            <br />
            Không dùng tiền thật.
          </p>
        </div>
        <button
          className="nav relative flex w-full min-w-0 items-center gap-3 rounded-md px-2 py-2.5 text-left text-xs text-text-muted hover:bg-surface-2 hover:text-text-primary [&.active]:bg-bull/10 [&.active]:text-bull md:in-[[data-sidebar=collapsed]]:justify-center"
          onClick={onOpenHelp}
          title="Hướng dẫn sử dụng"
          aria-label="Hướng dẫn sử dụng"
        >
          <CircleHelp size={18} />
          <span className="nav-text md:in-[[data-sidebar=collapsed]]:hidden">Hướng dẫn sử dụng</span>
        </button>
        <div className="profile flex items-center gap-2 border-t border-border-base pt-3 text-xs [&_small]:block [&_small]:text-text-muted md:in-[[data-sidebar=collapsed]]:[&>div:not(.avatar)]:hidden">
          <div className="avatar pale flex size-7 shrink-0 items-center justify-center rounded bg-surface-3 text-xs font-bold text-bull">LT</div>
          <div>
            Trader workspace<small>Local development</small>
          </div>
          <span className="status-dot inline-block size-1.5 shrink-0 rounded-full bg-bull [&.stale-dot]:bg-amber-400" />
        </div>
      </div>
    </aside>
  );
}
