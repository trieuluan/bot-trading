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
  X,
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
  onCloseMenu: () => void;
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
  onCloseMenu,
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
    <aside aria-label="Điều hướng chính" className={`sidebar fixed inset-y-0 left-0 z-[60] flex w-64 flex-col overflow-y-auto border-r border-border-base bg-surface-1 px-3 py-4 transition-transform md:w-52 md:translate-x-0 md:in-[[data-sidebar=collapsed]]:w-16 md:in-[[data-sidebar=collapsed]]:px-2 ${menu ? 'open translate-x-0' : '-translate-x-full'}`}>
      <div className="mb-5 flex items-center gap-1 md:in-[[data-sidebar=collapsed]]:flex-col-reverse">
      <button
        type="button"
        className="sidebar-toggle order-2 hidden size-8 shrink-0 items-center justify-center rounded-md text-text-muted hover:bg-surface-2 hover:text-text-primary md:inline-flex"
        aria-label={collapsed ? 'Mở rộng sidebar' : 'Thu gọn sidebar'}
        aria-expanded={!collapsed}
        title={collapsed ? 'Mở rộng sidebar' : 'Thu gọn sidebar'}
        onClick={() => setCollapsed((value) => !value)}
      >
        {collapsed ? <PanelLeftOpen size={15} /> : <PanelLeftClose size={15} />}
      </button>
      <a
        className="brand flex min-h-9 min-w-0 flex-1 items-center gap-1 font-display text-base font-bold text-text-primary [&>span]:leading-tight [&>span]:tracking-tight md:in-[[data-sidebar=collapsed]]:[&>span]:hidden"
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
        </span>
      </a>
      <button type="button" className="order-2 inline-flex size-9 shrink-0 items-center justify-center rounded-md text-text-muted hover:bg-surface-2 hover:text-text-primary md:hidden" aria-label="Đóng menu" onClick={onCloseMenu}>
        <X size={18} />
      </button>
      </div>
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
        className="nav workspace-collapse-switch hidden min-h-10 w-full items-center justify-center rounded-md text-text-muted hover:bg-surface-2 hover:text-text-primary md:in-[[data-sidebar=collapsed]]:flex"
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
      </button>
      <nav aria-label={exness ? 'Exness Forex' : 'MEXC Futures'} className="mt-4 space-y-1">
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
              <Icon size={18} className="shrink-0" />
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
        <div className="safe-card flex items-start gap-2 rounded-md border border-border-subtle bg-surface-2/50 p-2.5 text-xs leading-relaxed text-text-muted md:in-[[data-sidebar=collapsed]]:hidden">
          <ShieldCheck size={16} className="mt-0.5 shrink-0 text-bull" />
          <div>
            <h3 className="text-xs">{exness ? 'Tài khoản demo' : 'Giao dịch mô phỏng'}</h3>
            <p>{exness ? 'Lệnh gửi tới Exness demo.' : 'Lệnh không gửi tới sàn.'} Không dùng tiền thật.</p>
          </div>
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
      </div>
    </aside>
  );
}
