import { useRoute, setRoute } from '../lib/router';
import { useEffect, useRef, useState } from 'react';
import type { BrokerPosition, ExnessExecution } from '../types/exness-execution';
import { closeSetupPath, closeTicketPath, estimatedPnl, type PositionGroup } from '../lib/forex-position-groups';
import { ForexPositionGroups } from '../components/ForexPositionGroups';
import type { ForexDashboard } from '../App';
import { ForexEntryPilotPanel, ForexStrategyPage, HtfLineage } from './ForexStrategyPage';
import { formatForexPrice } from '../lib/format';
import { blockLabel } from '../lib/forex-labels';
import { formatTime } from '../lib/time-format';
import { DecisionJournal } from '../components/DecisionJournal';
import { subscribeForexStream } from '../lib/forex-stream';
import { useStickyBar } from '../hooks/useStickyBar';

const url = '/api/workspaces/exness_forex/execution';
const amount = (value: number | string | null | undefined) =>
  value == null ? '—' : Number(value).toLocaleString('vi-VN', { maximumFractionDigits: 2 });
const quotePrice = (value: number | string | null | undefined) =>
  value == null ? '—' : formatForexPrice(Number(value));
const date = (stamp: number | null) => formatTime(stamp, true);

type Panel = 'orders' | 'learning';
const panels: [Panel, string][] = [
  ['orders', 'Lệnh trên sàn'],
  ['learning', 'Tiến hoá & Cây HTF'],
];

type Filter = 'all' | 'open' | 'closed' | 'bot';

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
  const [offset, setOffset] = useState(0);
  const [revision, setRevision] = useState(0);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [executionOnline, setExecutionOnline] = useState(false);
  const [liveQuote, setLiveQuote] = useState<{ symbol: string; bid: number; ask: number; time: number } | null>(null);
  const [route] = useRoute();
  const [accountRef, accountStuck] = useStickyBar<HTMLDivElement>('--forex-account-h');
  // Chart luôn hiển thị; tab cũ `chart` rơi về `orders` để link cũ vẫn chạy.
  const panel: Panel = route.tab === 'learning' ? 'learning' : 'orders';
  const setPanel = (next: Panel) => setRoute({ tab: next });
  const [filter, setFilter] = useState<Filter>('all');
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
  const chartGroups = [...openGroups, ...rows.filter((group) => !openGroups.some((opened) => opened.id === group.id))];
  const chartPositions = chartGroups.flatMap((group) => group.legs);
  const chartPosition = chartPositions.find((position) => position.position_id === selectedId);
  const closedRows = rows.filter((group) => group.status === 'closed' && group.realized_pnl != null);
  const wins = closedRows.filter((group) => (group.realized_pnl ?? 0) > 0).length;
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
  const treeCount = data?.learning?.htf_ast3_shadow?.tree_count ?? 56;
  const state = !data?.worker_online ? 'offline' : data.enabled ? 'live' : 'paused';
  const label = { offline: 'Worker offline', live: 'Đang chạy', paused: 'Tạm dừng mở lệnh' }[state];
  const equity = data?.account_state?.equity;
  const balance = data?.account_state?.balance;
  const floating = equity != null && balance != null ? Number(equity) - Number(balance) : null;
  return (
    <div className={`execution-desk trade-terminal${stale ? ' is-stale' : ''}`} aria-busy={stale}>
      <div
        ref={accountRef}
        className={`terminal-account-bar${accountStuck ? ' is-stuck' : ''}`}
        aria-label="Tài khoản Exness Demo"
      >
        <span
          className={`badge-status badge-${state}`}
          title={`Đồng bộ sàn: ${date(data?.synced_at ?? null)}`}
        >
          <span className="pulse-dot" />
          {data ? label : 'Đang tải…'}
        </span>
        <span className="terminal-stat">
          <small>Balance</small>
          <strong>{amount(balance)}</strong>
        </span>
        <span className="terminal-stat">
          <small>Equity</small>
          <strong>{amount(equity)}</strong>
        </span>
        <span className="terminal-stat">
          <small>P&amp;L thả nổi</small>
          <strong className={floating == null ? '' : floating >= 0 ? 'positive' : 'negative'}>
            {floating != null && floating > 0 ? '+' : ''}
            {amount(floating)}
          </strong>
        </span>
        <span className="terminal-stat">
          <small>Margin</small>
          <strong>{amount(data?.account_state?.used_margin)}</strong>
        </span>
        <span className="terminal-stat" title="Profit deal sàn · chưa xác minh phí/swap">
          <small>Profit đã chốt</small>
          <strong className={(data?.broker_profit ?? 0) >= 0 ? 'positive' : 'negative'}>
            {amount(data?.broker_profit)}
          </strong>
        </span>
        <span className="terminal-stat">
          <small>Vị thế {symbol}</small>
          <strong>
            {data?.symbol_open_count ?? '—'}/{data?.max_concurrent_per_symbol ?? '—'}
            <em>
              {' '}
              · TK {data?.open_count ?? '—'}/{data?.max_concurrent ?? '—'}
            </em>
          </strong>
        </span>
        <button
          className={`terminal-toggle ${data?.enabled ? 'is-on' : ''}`}
          disabled={busy || stale || !data?.configured || !!error}
          onClick={() => void action('', 'PATCH', { enabled: !data?.enabled })}
          title={`Tài khoản ${data?.account ?? '—'} · ${data?.lot ?? '0.01'} lot`}
        >
          {data?.enabled ? 'Tạm dừng mở lệnh' : 'Bật bot demo'}
        </button>
      </div>
      {error && (
        <p role="alert" className="feedback">
          {error}
        </p>
      )}
      {stale ? (
        <div
          className={`terminal-decision is-switching${pendingFailed ? ' is-failed' : ''}`}
          role={pendingFailed ? 'alert' : 'status'}
        >
          {!pendingFailed && <span className="switching-spinner" aria-hidden="true" />}
          <strong>
            {pendingFailed ? `Chưa tải được ${pendingSymbol}` : `Đang tải ${pendingSymbol}…`}
          </strong>
          <span>Số liệu bên dưới vẫn là {symbol}; bật/tắt bot và đóng lệnh tạm khoá.</span>
          {pendingFailed && onRetry && (
            <button type="button" className="switching-retry" onClick={onRetry}>
              Thử lại
            </button>
          )}
        </div>
      ) : (
        data && (
          <div className="terminal-decision" role="status">
            <strong>Bot:</strong>
            <span>
              {data.decision ?? 'Chờ worker xác minh demo và đồng bộ sàn'}
              {data.error ? ` · Lỗi: ${data.error}` : ''}
            </span>
            <span className="muted">
              {data.learning?.latest?.mode ?? '—'} · {picked ?? 'không có tín hiệu chọn'}
            </span>
          </div>
        )
      )}
      <section
        id="execution-chart"
        className="execution-chart-panel"
        aria-label={`Chart ${symbol}`}
      >
        <ForexStrategyPage
          dashboard={dashboard}
          disconnected={!!error}
          executions={chartPositions}
          positionGroups={chartGroups}
          selectedPosition={chartPosition}
          onQuote={setLiveQuote}
          onSelectPosition={setSelectedId}
          executionOnly
          includeReplay={false}
          onClosePosition={closeTicket}
          onCloseSetup={closeSetup}
          closeDisabled={closeDisabled}
        />
      </section>
      {!data && !error && <p role="status">Đang tải lệnh thật từ tài khoản demo...</p>}
      {data && (
        <section className="panel terminal-bottom">
          <nav className="terminal-tabs" role="tablist" aria-label="Không gian bot">
            {panels.map(([key, title], index) => (
              <button
                key={key}
                id={`execution-tab-${key}`}
                type="button"
                role="tab"
                aria-selected={panel === key}
                aria-controls={`execution-panel-${key}`}
                tabIndex={panel === key ? 0 : -1}
                onClick={() => setPanel(key)}
                onKeyDown={(event) => {
                  let target = index;
                  if (event.key === 'ArrowRight') target = (index + 1) % panels.length;
                  else if (event.key === 'ArrowLeft')
                    target = (index + panels.length - 1) % panels.length;
                  else if (event.key === 'Home') target = 0;
                  else if (event.key === 'End') target = panels.length - 1;
                  else return;
                  event.preventDefault();
                  setPanel(panels[target][0]);
                  document.getElementById(`execution-tab-${panels[target][0]}`)?.focus();
                }}
              >
                {title}
                {key === 'orders' && (
                  <span className="terminal-tab-count">{data.open_count} mở</span>
                )}
              </button>
            ))}
            <span className="terminal-tabs-note">
              {closedRows.length
                ? `Thắng ${wins}/${closedRows.length} (${Math.round((wins / closedRows.length) * 100)}%) trang này`
                : 'Chưa có lệnh đóng'}{' '}
              · {data.closed_count} đã đóng
            </span>
          </nav>
          <section
            hidden={panel !== 'orders'}
            id="execution-panel-orders"
            role="tabpanel"
            aria-labelledby="execution-tab-orders"
            tabIndex={0}
            className="execution-tab-content orders-tab-view"
          >
            <div className="orders-section-card">
              <div className="orders-card-header">
                <div className="filter-pills" role="group" aria-label="Lọc lệnh">
                  {(
                    [
                      ['all', 'Tất cả'],
                      ['open', 'Đang mở'],
                      ['closed', 'Đã đóng'],
                      ['bot', 'Của bot'],
                    ] as [Filter, string][]
                  ).map(([key, text]) => (
                    <button
                      key={key}
                      className={filter === key ? 'selected' : ''}
                      aria-pressed={filter === key}
                      onClick={() => { setFilter(key); setOffset(0); }}
                    >
                      {text}
                    </button>
                  ))}
                </div>
                <div className="orders-header-note">
                  <a href={`${url}/trades.csv?symbol=${symbol}`} download>
                    Xuất CSV {symbol}
                  </a>
                  {' · '}
                  {data.total ? offset + 1 : 0}–{Math.min(offset + 50, data.total)} / {data.total}{' '}
                  setup
                </div>
              </div>

              <ForexPositionGroups groups={rows} selectedId={chartPosition?.position_id}
                onSelect={(id) => { setSelectedId(id); document.getElementById('execution-chart')?.scrollIntoView({ behavior: 'smooth', block: 'start' }); }}
                onCloseTicket={closeTicket} onCloseSetup={closeSetup} closeDisabled={closeDisabled} pnlOf={pnlOf} />

              <div className="orders-pagination-bar">
                <button
                  className="secondary"
                  disabled={offset === 0}
                  onClick={() => setOffset(Math.max(0, offset - 50))}
                >
                  ← Trang trước
                </button>
                <span className="muted">
                  Trang {Math.floor(offset / 50) + 1} ({data.total} setup tổng cộng)
                </span>
                <button
                  className="secondary"
                  disabled={!data.has_more}
                  onClick={() => setOffset(offset + 50)}
                >
                  Trang sau →
                </button>
              </div>
            </div>
            <p className="micro">
              SL theo vô hiệu H1 + ATR buffer; TP theo barrier cấu trúc H1/H4 (tối thiểu 1.5R). Tạm
              dừng không đóng vị thế đang mở. Mac ngủ thì bot không mở/đóng chủ động, SL/TP trên sàn
              vẫn có hiệu lực; khi mở lại sẽ đối soát fill thật.
            </p>
            <details>
              <summary>Lệnh chờ sàn & nhật ký gửi lệnh</summary>
              <p>
                {data.orders.length} lệnh chờ/SLTP trên sàn. Lệnh chưa xác minh kết quả sẽ chặn mở
                thêm, không gửi lại.
              </p>
              {data.orders.map((order) => (
                <p key={order.order_id}>
                  #{order.order_id} · {order.instrument} · {order.order_type} · {order.volume}
                </p>
              ))}
              {data.intents.map((intent) => (
                <p key={intent.signal_time}>
                  {date(intent.signal_time)} · {intent.status} ·{' '}
                  {intent.ack?.operation_id ?? 'Chưa có ACK'} · {intent.error ?? intent.reason}
                </p>
              ))}
            </details>
            <p className="micro">
              Lịch sử nhập từ {date(data.history_from)}, sau đó đồng bộ tăng dần; không trộn ví
              paper/replay.
            </p>
          </section>
          <section
            hidden={panel !== 'learning'}
            id="execution-panel-learning"
            role="tabpanel"
            aria-labelledby="execution-tab-learning"
            tabIndex={0}
            className="execution-tab-content learning-tab-view"
            aria-label="Bộ học chiến lược"
          >
            <div className="learning-overview-grid">
              <div className="learning-stat-card">
                <span className="card-label">Execution V3</span>
                <div className="card-value" style={{ fontSize: '18px' }}>
                  {data.learning?.execution_v2?.enabled ? 'Đang gửi lệnh' : 'Chỉ shadow'}
                </div>
                <div className="card-sub">
                  {blockLabel(data.learning?.execution_v2?.blocked_reason) ?? 'Sẵn sàng'}
                </div>
              </div>
              <div className="learning-stat-card">
                <span className="card-label">Cây được phép vào lệnh</span>
                <div className="card-value">
                  {data.learning?.execution_v2?.entry_allowed_count ?? 0}
                  <small className="muted"> / {treeCount}</small>
                </div>
                <div className="card-sub">
                  Ungated · chọn: {data.learning?.execution_v2?.selected ?? 'không có'}
                </div>
              </div>
              <div className="learning-stat-card">
                <span className="card-label">Đủ chuẩn tiến hoá</span>
                <div className="card-value">
                  {data.learning?.execution_v2?.evolution_ready_count ?? 0}
                  <small className="muted"> / {treeCount}</small>
                </div>
                <div className="card-sub">Cần ≥10 mẫu forward và R trung bình &gt; 0</div>
              </div>
              <div className="learning-stat-card">
                <span className="card-label">Mẫu forward V3</span>
                <div className="card-value">{data.learning?.htf_ast3_shadow?.resolved ?? 0}</div>
                <div className="card-sub">
                  {data.learning?.htf_ast3_shadow?.pending ?? 0} đang chạy · thế hệ G
                  {data.learning?.htf_ast3_shadow?.generation ?? 0}
                </div>
              </div>
            </div>

            <div className="learning-section-card">
              <div className="card-header">
                <div>
                  <h4>Vì sao bot vào / không vào lệnh</h4>
                  <span className="muted-desc">
                    Mỗi nến M15 đóng: bot thấy gì và dừng ở bước nào
                    {data.funnel ? ` · ${data.funnel.candles} nến gần nhất` : ''}
                  </span>
                </div>
              </div>
              <DecisionJournal journal={data.journal} funnel={data.funnel} />
            </div>
            <ForexEntryPilotPanel
              key={symbol}
              symbol={symbol}
              entryPilot={data.learning?.entry_pilot}
            />
            {data.learning?.htf_ast3_shadow && (
              <div className="learning-section-card">
                <div className="card-header">
                  <div>
                    <h4>Quần thể HTF V3 · dòng dõi G0 → G1…</h4>
                    <span className="muted-desc">
                      Thế hệ {data.learning.htf_ast3_shadow.generation} ·{' '}
                      {data.learning.htf_ast3_shadow.tree_count} cây ·{' '}
                      {data.learning.htf_ast3_shadow.excluded} mẫu bị loại · R sau chi phí, chưa
                      swap
                    </span>
                  </div>
                  <span className="badge-count">
                    {data.learning.htf_ast3_shadow.resolved} đã đóng ·{' '}
                    {data.learning.htf_ast3_shadow.pending} đang chạy
                  </span>
                </div>
                <HtfLineage
                  trees={data.learning.htf_ast3_shadow.trees}
                  nextEvolutionAt={data.learning.htf_ast3_shadow.next_evolution_at}
                />
              </div>
            )}
          </section>
        </section>
      )}
    </div>
  );
}
