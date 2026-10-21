import { useRoute, setRoute } from '../lib/router';
import { useEffect, useMemo, useState } from 'react';
import type { ExnessExecution } from '../types/exness-execution';
import type { ForexDashboard } from '../App';
import { ForexStrategyPage, HtfLineage } from './ForexStrategyPage';
import { formatForexPrice } from '../lib/format';
import { blockLabel } from '../lib/forex-labels';
import { DecisionJournal } from '../components/DecisionJournal';

const url = '/api/workspaces/exness_forex/execution';
// Trang đầu của mỗi cặp, để đổi cặp hiển thị ngay số liệu đã có.
const firstPageCache = new Map<string, ExnessExecution>();
const amount = (value: number | string | null | undefined) =>
  value == null ? '—' : Number(value).toLocaleString('vi-VN', { maximumFractionDigits: 2 });
const quotePrice = (value: number | string | null | undefined) =>
  value == null ? '—' : formatForexPrice(Number(value));
const date = (stamp: number | null) =>
  stamp == null ? '—' : new Date(stamp * 1000).toLocaleString('vi-VN');

type Panel = 'orders' | 'learning';
const panels: [Panel, string][] = [
  ['orders', 'Lệnh trên sàn'],
  ['learning', 'Tiến hoá & Cây HTF'],
];

type Filter = 'all' | 'open' | 'closed' | 'bot';
const isBot = (source: string) =>
  source === 'learning' || source === 'hast2' || source === 'regime_router';

export function ForexExecutionPage({ dashboard }: { dashboard: ForexDashboard }) {
  const symbol = dashboard.workspace.symbol;
  const [data, setData] = useState<ExnessExecution | null>(null);
  const [selectedId, setSelectedId] = useState<string>();
  const [offset, setOffset] = useState(0);
  const [revision, setRevision] = useState(0);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [route] = useRoute();
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
    const controller = new AbortController();
    let inflight = false;
    const cached = offset === 0 ? firstPageCache.get(symbol) : undefined;
    if (cached) setData(cached);
    async function refresh() {
      if (inflight) return;
      inflight = true;
      try {
        const response = await fetch(`${url}?symbol=${symbol}&offset=${offset}&limit=50`, {
          signal: AbortSignal.any([controller.signal, AbortSignal.timeout(10000)]),
        });
        if (!response.ok) throw new Error();
        const next: ExnessExecution = await response.json();
        if (offset === 0) firstPageCache.set(symbol, next);
        if (!controller.signal.aborted) {
          setData(next);
          setError('');
        }
      } catch {
        if (!controller.signal.aborted)
          setError('Mất kết nối dữ liệu lệnh sàn. Dữ liệu đang hiển thị có thể đã cũ.');
      } finally {
        inflight = false;
      }
    }
    void refresh();
    const timer = setInterval(() => void refresh(), 5000);
    return () => {
      controller.abort();
      clearInterval(timer);
    };
  }, [offset, revision, symbol]);

  async function action(path: string, method: string, body?: object) {
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
  const selected = data?.positions.find((position) => position.position_id === selectedId);
  const openPositions = (data?.positions ?? []).filter((position) => position.status === 'open');
  const chartPosition =
    openPositions.find((position) => position.position_id === selectedId) ?? openPositions[0];
  const rows = useMemo(
    () =>
      (data?.positions ?? []).filter(
        (position) =>
          filter === 'all' ||
          (filter === 'bot' ? isBot(position.source) : position.status === filter),
      ),
    [data, filter],
  );
  const closedRows = (data?.positions ?? []).filter(
    (position) => position.status === 'closed' && position.pnl != null,
  );
  const wins = closedRows.filter((position) => (position.pnl ?? 0) > 0).length;
  const picked = data?.learning?.latest?.selected;
  const treeCount = data?.learning?.htf_ast2_shadow?.tree_count ?? 56;
  const state = !data?.worker_online ? 'offline' : data.enabled ? 'live' : 'paused';
  const label = { offline: 'Worker offline', live: 'Đang chạy', paused: 'Tạm dừng mở lệnh' }[state];
  const equity = data?.account_state?.equity;
  const balance = data?.account_state?.balance;
  const floating = equity != null && balance != null ? Number(equity) - Number(balance) : null;
  return (
    <div className="execution-desk trade-terminal">
      <div className="terminal-account-bar" aria-label="Tài khoản Exness Demo">
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
          disabled={busy || !data?.configured || !!error}
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
      {data && (
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
      )}
      <section
        id="execution-chart"
        className="execution-chart-panel"
        aria-label={`Chart ${symbol}`}
      >
        <ForexStrategyPage
          dashboard={dashboard}
          disconnected={!!error}
          executions={openPositions}
          selectedPosition={chartPosition}
          onSelectPosition={setSelectedId}
          executionOnly
          includeReplay={false}
          onClosePosition={(position) => {
            if (
              !busy &&
              !error &&
              data?.worker_online &&
              window.confirm(`Đóng vị thế demo #${position.position_id} theo giá thị trường?`)
            )
              void action(
                `/trades/${position.signal_time}/close?position_id=${position.position_id}`,
                'POST',
              );
          }}
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
                      onClick={() => setFilter(key)}
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
                  lệnh
                </div>
              </div>

              <div className="table-wrap">
                <table className="orders-clean-table">
                  <thead>
                    <tr>
                      <th>Vị thế & Nguồn</th>
                      <th>Chiều</th>
                      <th className="num">Lot</th>
                      <th className="num">Vào / Thoát</th>
                      <th className="num">SL / TP</th>
                      <th className="num">P&amp;L (USD)</th>
                      <th>Trạng thái</th>
                      <th className="actions-col">Thao tác</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((position) => {
                      const isSelected = selectedId === position.position_id;
                      return (
                        <tr
                          key={position.position_id}
                          className={`${isSelected ? 'row-active' : ''} ${position.status === 'open' ? 'row-open' : ''}`}
                        >
                          <td>
                            <div className="pos-id-box">
                              <span className="pos-id">#{position.position_id}</span>
                              <span className="pos-source">
                                {position.source === 'learning'
                                  ? 'Bot · HTF V2'
                                  : position.source === 'regime_router'
                                    ? 'Bot · Cũ'
                                    : 'Thủ công / Probe'}
                              </span>
                            </div>
                          </td>
                          <td>
                            <span className={`side-badge side-${position.side?.toLowerCase()}`}>
                              {position.side?.toUpperCase() ?? '—'}
                            </span>
                          </td>
                          <td className="num font-mono">{amount(position.volume)}</td>
                          <td className="num font-mono">
                            <div>{quotePrice(position.open_price)}</div>
                            <small className="muted">{quotePrice(position.close_price)}</small>
                          </td>
                          <td className="num font-mono">
                            <div>{quotePrice(position.stop)}</div>
                            <small className="muted">{quotePrice(position.target)}</small>
                          </td>
                          <td
                            className={`num font-mono font-bold ${
                              position.pnl == null
                                ? ''
                                : position.pnl >= 0
                                  ? 'positive'
                                  : 'negative'
                            }`}
                          >
                            {position.pnl != null && position.pnl > 0 ? '+' : ''}
                            {amount(position.pnl)}
                          </td>
                          <td>
                            <span className={`order-status-pill status-${position.status}`}>
                              {position.status === 'open' ? 'Đang mở' : 'Đã đóng'}
                            </span>
                            <small className="muted order-time-stamp">
                              {date(position.close_time ?? position.open_time)}
                            </small>
                          </td>
                          <td className="actions-col">
                            <div className="order-actions-group">
                              <button
                                type="button"
                                className={`btn-action ${isSelected ? 'btn-action-active' : ''}`}
                                onClick={() => {
                                  setSelectedId(position.position_id);
                                  document
                                    .getElementById('execution-chart')
                                    ?.scrollIntoView({ behavior: 'smooth', block: 'start' });
                                }}
                                title="Xem trên chart & deal khớp"
                              >
                                Xem chart
                              </button>
                              {position.status === 'open' && position.signal_time != null && (
                                <button
                                  type="button"
                                  className="btn-action btn-danger"
                                  disabled={
                                    busy ||
                                    !!error ||
                                    position.close_requested ||
                                    !data.worker_online
                                  }
                                  onClick={() => {
                                    if (
                                      window.confirm(
                                        `Đóng vị thế demo #${position.position_id} theo giá thị trường?`,
                                      )
                                    )
                                      void action(
                                        `/trades/${position.signal_time}/close?position_id=${position.position_id}`,
                                        'POST',
                                      );
                                  }}
                                >
                                  {position.close_requested ? 'Đang đóng...' : 'Đóng'}
                                </button>
                              )}
                            </div>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>

              {!rows.length && (
                <div className="orders-empty-state">
                  Không có lệnh khớp bộ lọc. Bot chờ tín hiệu hợp lệ, không ép mở lệnh.
                </div>
              )}

              <div className="orders-pagination-bar">
                <button
                  className="secondary"
                  disabled={offset === 0}
                  onClick={() => setOffset(Math.max(0, offset - 50))}
                >
                  ← Trang trước
                </button>
                <span className="muted">
                  Trang {Math.floor(offset / 50) + 1} ({data.total} lệnh tổng cộng)
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
            {selected && (
              <details open key={selected.position_id}>
                <summary>
                  Deal khớp sàn #{selected.position_id} · {selected.reason ?? 'Lệnh ngoài executor'}
                </summary>
                <div className="table-wrap">
                  <table>
                    <thead>
                      <tr>
                        <th>Deal / Order ID</th>
                        <th>Loại</th>
                        <th>Giá · lot</th>
                        <th>Thời gian</th>
                        <th>Profit / commission</th>
                      </tr>
                    </thead>
                    <tbody>
                      {selected.deals.map((deal) => (
                        <tr key={deal.deal_id}>
                          <td>
                            {deal.deal_id} / {deal.order_id ?? '—'}
                          </td>
                          <td>
                            {deal.type} · {deal.direction}
                          </td>
                          <td>
                            {quotePrice(deal.price)} · {amount(deal.volume)}
                          </td>
                          <td>{date(deal.event_time)}</td>
                          <td>
                            {amount(deal.profit?.amount)} / {deal.commission ?? 'Chưa có từ API'}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </details>
            )}
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
                <span className="card-label">Execution V2</span>
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
                <span className="card-label">Mẫu forward V2</span>
                <div className="card-value">{data.learning?.htf_ast2_shadow?.resolved ?? 0}</div>
                <div className="card-sub">
                  {data.learning?.htf_ast2_shadow?.pending ?? 0} đang chạy · thế hệ G
                  {data.learning?.htf_ast2_shadow?.generation ?? 0}
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
            {data.learning?.htf_ast2_shadow && (
              <div className="learning-section-card">
                <div className="card-header">
                  <div>
                    <h4>Quần thể HTF V2 · dòng dõi G0 → G1…</h4>
                    <span className="muted-desc">
                      Thế hệ {data.learning.htf_ast2_shadow.generation} ·{' '}
                      {data.learning.htf_ast2_shadow.tree_count} cây ·{' '}
                      {data.learning.htf_ast2_shadow.excluded} mẫu bị loại · R sau chi phí, chưa
                      swap
                    </span>
                  </div>
                  <span className="badge-count">
                    {data.learning.htf_ast2_shadow.resolved} đã đóng ·{' '}
                    {data.learning.htf_ast2_shadow.pending} đang chạy
                  </span>
                </div>
                <HtfLineage
                  trees={data.learning.htf_ast2_shadow.trees}
                  nextEvolutionAt={data.learning.htf_ast2_shadow.next_evolution_at}
                />
              </div>
            )}
          </section>
        </section>
      )}
    </div>
  );
}
