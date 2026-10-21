import { useRoute, setRoute } from './lib/router';
import { useFuturesBoard } from './components/FuturesBoard';
import { getFuturesSymbol, setFuturesSymbol, type FuturesSymbol } from './lib/futures-symbol';
import { useEffect, useState } from 'react';
import type { ReactNode } from 'react';
import { Bell, FlaskConical, Menu, RefreshCw, X } from 'lucide-react';
import { Sidebar } from './components/Sidebar';
import { ForexMarkets } from './components/ForexMarkets';
import { cachedDashboard, fetchDashboard } from './lib/forex-cache';
import { HelpDialog } from './components/HelpDialog';
import { useTradingDashboard } from './hooks/useTradingDashboard';
import { BotControlPanel } from './components/BotControlPanel';
import { OverviewPage } from './pages/OverviewPage';
import { ConnectionsPage } from './pages/ConnectionsPage';
import { PaperTradingPage } from './pages/PaperTradingPage';
import { ActivityPage } from './pages/ActivityPage';
import {
  ForexObservationLog,
  ForexResearchPage,
  ForexCatchupStatus,
} from './pages/ForexStrategyPage';
import type { ObservationChartSnapshot } from './components/MtfObservationChart';
import { ForexPaperPage, type ForexPaperSummary } from './pages/ForexPaperPage';
import { ForexExecutionPage } from './pages/ForexExecutionPage';
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
  paper: ForexPaperSummary;
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

function ForexOverviewPage({
  dashboard,
  disconnected,
  onNavigate,
}: {
  dashboard: ForexDashboard;
  disconnected: boolean;
  onNavigate: (page: string) => void;
}) {
  const [execution, setExecution] = useState<ExnessExecution | null>(null);
  const [error, setError] = useState('');
  useEffect(() => {
    const controller = new AbortController();
    let inflight = false;
    const refresh = async () => {
      if (inflight) return;
      inflight = true;
      try {
        const response = await fetch(
          `/api/workspaces/exness_forex/execution?symbol=${dashboard.workspace.symbol}&limit=1`,
          {
            signal: AbortSignal.any([controller.signal, AbortSignal.timeout(10000)]),
          },
        );
        if (!response.ok) throw new Error();
        const data: ExnessExecution = await response.json();
        if (!controller.signal.aborted) {
          setExecution(data);
          setError('');
        }
      } catch {
        if (!controller.signal.aborted)
          setError('Không tải được trạng thái lệnh sàn. Số liệu có thể đã cũ.');
      } finally {
        inflight = false;
      }
    };
    void refresh();
    const timer = window.setInterval(() => void refresh(), 15000);
    return () => {
      controller.abort();
      window.clearInterval(timer);
    };
  }, []);
  const amount = (value: number | string | null | undefined) =>
    value == null ? '—' : Number(value).toLocaleString('vi-VN', { maximumFractionDigits: 2 });
  const verified = !error && !disconnected;
  const data = verified ? execution : null;
  const workerOnline = verified && dashboard.market.worker_online;
  const selected = data?.learning?.latest?.selected;
  return (
    <div className="forex-overview-desk">
      {error && (
        <p role="status" className="feedback">
          {error}
        </p>
      )}
      <section className="forex-overview-lead" aria-label="Trạng thái vận hành">
        <div>
          <span className="execution-eyebrow">{dashboard.workspace.symbol} / EXNESS DEMO</span>
          <h2>
            {!verified || !data
              ? 'Chưa xác minh trạng thái bot'
              : !data.configured
                ? 'Chưa cấu hình thực thi'
                : data.enabled
                  ? 'Bot đang được bật'
                  : 'Bot đang tạm dừng mở lệnh'}
          </h2>
          <p>
            {data?.decision ??
              'Theo dõi tín hiệu, quản lý vị thế và kiểm chứng trong cùng workspace.'}
          </p>
        </div>
        <button type="button" className="primary" onClick={() => onNavigate('Bot')}>
          Mở không gian Bot
        </button>
      </section>
      <div className="forex-exec-hero" aria-label="Số liệu tài khoản sàn demo">
        <div className="forex-exec-card">
          <span className="label">Vị thế đang mở</span>
          <strong className="val">{amount(data?.open_count)}</strong>
          <span className="sub">Lệnh demo trên sàn · không gồm paper</span>
        </div>
        <div className="forex-exec-card">
          <span className="label">Balance / Equity · USD</span>
          <strong className="val">
            {amount(data?.account_state?.balance)} / {amount(data?.account_state?.equity)}
          </strong>
          <span className="sub">Margin đã dùng: {amount(data?.account_state?.used_margin)}</span>
        </div>
        <div className="forex-exec-card">
          <span className="label">Profit deal sàn · USD</span>
          <strong
            className={`val ${data ? (data.broker_profit >= 0 ? 'positive' : 'negative') : ''}`}
          >
            {amount(data?.broker_profit)}
          </strong>
          <span className="sub">Chưa xác minh toàn bộ phí và swap</span>
        </div>
        <div className="forex-exec-card">
          <span className="label">Worker dữ liệu</span>
          <strong className="val">
            {disconnected ? 'Chưa xác minh' : workerOnline ? 'Có heartbeat' : 'Chưa có heartbeat'}
          </strong>
          <span className="sub">
            {dashboard.market.last_sync
              ? `Đồng bộ ${new Date(dashboard.market.last_sync).toLocaleString('vi-VN', { timeZone: 'UTC' })} UTC`
              : 'Chưa đồng bộ'}
          </span>
        </div>
      </div>
      <div className="forex-overview-grid">
        <section className="forex-overview-panel">
          <span className="execution-eyebrow">01 / VẬN HÀNH</span>
          <h2>Bot & Phân tích</h2>
          <p>
            Chart giá, lệnh sàn, bộ học và chi tiết chiến lược nằm trong một không gian. Chỉ hiện
            lệnh đang mở trên chart.
          </p>
          <dl className="forex-summary-list">
            <div>
              <dt>Tín hiệu được chọn gần nhất</dt>
              <dd>{selected ?? 'Chưa có tín hiệu được chọn'}</dd>
            </div>
            <div>
              <dt>Mẫu SL/TP mô phỏng</dt>
              <dd>{amount(data?.learning?.exit_learning?.sample_count)}</dd>
            </div>
            <div>
              <dt>Xu hướng H4 quan sát</dt>
              <dd>
                {({ up: 'Tăng', down: 'Giảm', sideway: 'Đi ngang' } as Record<string, string>)[
                  dashboard.observation.snapshot?.trend ?? ''
                ] ?? 'Chưa xác định'}
              </dd>
            </div>
          </dl>
          <button type="button" className="secondary" onClick={() => onNavigate('Bot')}>
            Xem chart, lệnh & phân tích
          </button>
        </section>
        <section className="forex-overview-panel">
          <span className="execution-eyebrow">02 / KIỂM CHỨNG</span>
          <h2>Nghiên cứu</h2>
          <p>
            Lịch sử báo cáo, paper và nhật ký của family cũ, chỉ để đọc. Cây HTF V2 xem ở Bot → Tiến
            hoá & Cây HTF.
          </p>
          <dl className="forex-summary-list">
            <div>
              <dt>Báo cáo cũ</dt>
              <dd>Replay family cũ, đã ngừng</dd>
            </div>
            <div>
              <dt>Paper cũ</dt>
              <dd>Lịch sử ví, không sinh lệnh mới</dd>
            </div>
            <div>
              <dt>Nhật ký</dt>
              <dd>Quan sát MTF cũ theo nến đã đóng</dd>
            </div>
          </dl>
          <button type="button" className="secondary" onClick={() => onNavigate('Nghiên cứu')}>
            Mở phòng nghiên cứu
          </button>
        </section>
      </div>
      <section className="panel forex-status">
        <div className="section-head">
          <div>
            <h2>Dữ liệu nến Exness</h2>
            <p>{dashboard.market.message}</p>
          </div>
          <button type="button" className="secondary" onClick={() => onNavigate('Kết nối sàn')}>
            Kiểm tra kết nối
          </button>
        </div>
        <div className="forex-frame-grid">
          {(dashboard.frames ?? []).map((frame) => (
            <div key={frame.timeframe} className="forex-frame-card">
              <strong>{frame.timeframe.toUpperCase()}</strong>
              <span>{frame.bars_per_side.toLocaleString('vi-VN')} nến mỗi phía Bid/Ask</span>
              <small>
                {frame.last_candle
                  ? `${new Date(frame.last_candle * 1000).toISOString().slice(0, 16).replace('T', ' ')} UTC`
                  : 'Chưa có nến'}
              </small>
              {frame.error && <small className="negative">{frame.error}</small>}
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}

function ForexResearchWorkspace({
  dashboard,
  disconnected,
  revision,
}: {
  dashboard: ForexDashboard;
  disconnected: boolean;
  revision: number;
}) {
  const [route] = useRoute();
  type Tab = 'backtest' | 'paper' | 'log';
  const tab: Tab =
    (['backtest', 'paper', 'log'] as const).find((key) => key === route.tab) ?? 'backtest';
  const setTab = (next: Tab) => setRoute({ tab: next });
  const tabs: [typeof tab, string, string][] = [
    ['backtest', 'Báo cáo cũ (đã ngừng)', 'Replay family cũ, chỉ để đọc lịch sử'],
    ['paper', 'Paper cũ (đã ngừng)', 'Lịch sử ví paper family cũ, không sinh lệnh mới'],
    ['log', 'Nhật ký quan sát', 'Lịch sử quan sát MTF cũ, chỉ để đọc'],
  ];
  return (
    <div className="execution-desk">
      <nav className="execution-tabs" role="tablist" aria-label="Nghiên cứu">
        {tabs.map(([key, label, sub]) => (
          <button key={key} role="tab" aria-selected={tab === key} onClick={() => setTab(key)}>
            <span>{label}</span>
            <small>{sub}</small>
          </button>
        ))}
      </nav>
      {tab === 'backtest' && <ForexResearchPage revision={revision} />}
      {tab === 'paper' && (
        <ForexPaperPage
          paper={dashboard.paper}
          paused={
            disconnected ||
            !dashboard.market.worker_online ||
            dashboard.observation.status === 'data_paused'
          }
        />
      )}
      {tab === 'log' && <ForexObservationLog events={dashboard.observation.recent ?? []} />}
    </div>
  );
}

function ForexWorkspace({ workspacePicker }: { workspacePicker: ReactNode }) {
  const [route] = useRoute();
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
        allowedPages={['Tổng quan', 'Bot', 'Nghiên cứu', 'Kết nối sàn']}
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
            <div className="page-heading">
              <div>
                <h1>{page === 'Nghiên cứu' ? 'Nghiên cứu chiến lược' : 'Exness · Forex Demo'}</h1>
                <p>{symbol} · Quần thể riêng, ví và giới hạn vị thế chung tài khoản.</p>
              </div>
              <button className="secondary" onClick={() => setRevision((value) => value + 1)}>
                <RefreshCw size={15} /> Làm mới
              </button>
            </div>
          )}
          <ForexMarkets symbol={symbol} compact={page === 'Bot'} />
          {page !== 'Bot' && (
            <div className="demo-banner">
              <FlaskConical size={17} />
              <span>
                {dashboard?.execution_enabled
                  ? 'Thực thi Demo đã được cấu hình. Trạng thái bật/tạm dừng và lệnh sàn xem tại Bot. Ví mô phỏng nằm riêng trong Nghiên cứu.'
                  : 'HTF AST V2 gom mẫu forward. Dữ liệu riêng Exness, không dùng ví MEXC.'}
              </span>
            </div>
          )}
          {error && (
            <div className="feedback" role="alert">
              {error}
            </div>
          )}
          {!dashboard && !error && <p role="status">Đang tải workspace Exness...</p>}
          {dashboard && (
            <ForexCatchupStatus
              dashboard={dashboard}
              disconnected={!!error}
              compact={page === 'Bot'}
            />
          )}
          {dashboard && page === 'Bot' && <ForexExecutionPage dashboard={dashboard} />}
          {dashboard && page === 'Nghiên cứu' && (
            <ForexResearchWorkspace
              key={dashboard.workspace.symbol}
              dashboard={dashboard}
              disconnected={!!error}
              revision={revision}
            />
          )}
          {dashboard && page === 'Tổng quan' && (
            <ForexOverviewPage
              key={dashboard.workspace.symbol}
              dashboard={dashboard}
              disconnected={!!error}
              onNavigate={setPage}
            />
          )}
          {dashboard && page === 'Kết nối sàn' && (
            <section className="panel forex-status">
              <h2>Kết nối Exness</h2>
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
              {dashboard.observation.snapshot && (
                <div className="frames-summary">
                  <h3>MTF {symbol} · 4h / 1h / 15m · chỉ quan sát</h3>
                  <p>
                    Trạng thái: {dashboard.observation.status} · Xu hướng 4h:{' '}
                    {dashboard.observation.snapshot.trend ?? 'chưa đủ nến'}
                  </p>
                  <p>
                    Cập nhật theo nến đóng:{' '}
                    {new Date(dashboard.observation.snapshot.available_at * 1000).toISOString()} ·{' '}
                    {dashboard.observation.snapshot.provenance}
                  </p>
                  <p>
                    Vùng 20 nến 1h: {dashboard.observation.snapshot.range_low ?? '—'} –{' '}
                    {dashboard.observation.snapshot.range_high ?? '—'} · Mức breakout:{' '}
                    {dashboard.observation.snapshot.level ?? '—'}
                  </p>
                  <p>
                    SL tham chiếu: {dashboard.observation.snapshot.stop ?? '—'} · TP tham chiếu:{' '}
                    {dashboard.observation.snapshot.target ?? '—'} (chưa tính spread/khớp lệnh)
                  </p>
                  <details>
                    <summary>Nhật ký quan sát gần đây</summary>
                    <ul>
                      {dashboard.observation.recent?.map((event) => (
                        <li key={event.candle_time}>
                          {new Date(event.candle_time * 1000).toISOString()} · {event.status}:{' '}
                          {event.reason}
                        </li>
                      ))}
                    </ul>
                  </details>
                </div>
              )}
              {dashboard.observation.quality && (
                <div className="frames-summary">
                  <h3>Kiểm tra dữ liệu: {dashboard.observation.quality.status}</h3>
                  <ul>
                    {dashboard.observation.quality.frames.map((frame) => (
                      <li key={frame.timeframe}>
                        {frame.timeframe}: {frame.paired_bars} cặp nến · {frame.unpaired_bars} thiếu
                        phía · {frame.invalid_bars} lỗi OHLC/thời gian · {frame.crossed_bars} đảo
                        bid/ask · {frame.gap_count} khoảng trống · spread close trung bình/tối đa:{' '}
                        {frame.close_spread_mean != null
                          ? frame.close_spread_mean.toFixed(
                              symbol === 'XAUUSD' ? 3 : symbol.endsWith('JPY') ? 3 : 5,
                            )
                          : '—'}
                        /
                        {frame.close_spread_max != null
                          ? frame.close_spread_max.toFixed(
                              symbol === 'XAUUSD' ? 3 : symbol.endsWith('JPY') ? 3 : 5,
                            )
                          : '—'}{' '}
                        {symbol === 'XAUUSD' ? 'USD/oz' : 'quote'}
                      </li>
                    ))}
                  </ul>
                  <p>{dashboard.observation.quality.message}</p>
                </div>
              )}
              <p>
                <strong>Ví mô phỏng:</strong> {dashboard.paper.message}
              </p>
              <p>
                Nến bid/ask lưu riêng theo workspace, cặp và khung thời gian. Không ghép nến MEXC
                hoặc tự tạo giá.
              </p>
              <p>
                Không lấp khoảng trống. MTF dùng nến gốc Exness, hủy setup khi gặp khoảng trống M15.
                Replay nghiên cứu nằm trong tab Backtest. Ví mô phỏng riêng trong tab Ví mô phỏng;
                không gửi lệnh Exness.
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
  const { snapshot, connected, busy, message, setMessage, refresh, toggle } = useTradingDashboard();
  const board = useFuturesBoard(page !== 'Cài đặt & Kết nối');
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
            <>
              <ConnectionsPage snapshot={snapshot} connected={connected} onRefresh={refresh} />
              <section className="panel prose">
                <h2>Worker & dữ liệu nến</h2>
                <p>
                  API: {connected ? 'Kết nối' : 'Mất kết nối'} · Worker:{' '}
                  {connected && snapshot.bot.online ? 'Có heartbeat' : 'Chưa xác minh heartbeat'}
                </p>
                <p>
                  Nến đóng gần nhất:{' '}
                  {snapshot.market.last_closed_time
                    ? new Date(snapshot.market.last_closed_time * 1000).toLocaleString('vi-VN')
                    : 'Chưa có dữ liệu'}
                </p>
                <p>
                  WebSocket chart chạy khi mở Giao dịch & Bot. Worker Paper vẫn lấy REST khoảng 5
                  giây/lần; đóng trình duyệt không dừng bot.
                </p>
              </section>
              <ActivityPage snapshot={snapshot} />
            </>
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
