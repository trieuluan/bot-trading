import { useEffect, useMemo, useState } from 'react';
import type { ExnessExecution } from '../types/exness-execution';
import type { ForexDashboard } from '../App';
import { ForexReplayControl, ForexStrategyPage } from './ForexStrategyPage';

const url = '/api/workspaces/exness_forex/execution';
const amount = (value: number | string | null | undefined) =>
  value == null ? '—' : Number(value).toLocaleString('vi-VN', { maximumFractionDigits: 3 });
const date = (stamp: number | null) =>
  stamp == null ? '—' : new Date(stamp * 1000).toLocaleString('vi-VN');

type Panel = 'orders' | 'chart' | 'learning' | 'replay';
const panels: [Panel, string, string][] = [
  ['orders', 'Lệnh trên sàn', 'Vị thế và lịch sử khớp lệnh'],
  ['chart', 'Chart giao dịch', 'Giá khớp, SL và TP trên chart'],
  ['learning', 'Bộ học', 'Giả thuyết, mẫu và kết quả học'],
  ['replay', 'Phòng thử nghiệm', 'So sánh trên nến đã lưu'],
];

type Filter = 'all' | 'open' | 'closed' | 'bot';
const isBot = (source: string) => source === 'learning' || source === 'regime_router';

export function ForexExecutionPage({ dashboard }: { dashboard: ForexDashboard }) {
  const [data, setData] = useState<ExnessExecution | null>(null);
  const [selectedId, setSelectedId] = useState<string>();
  const [offset, setOffset] = useState(0);
  const [revision, setRevision] = useState(0);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [panel, setPanel] = useState<Panel>('orders');
  const [filter, setFilter] = useState<Filter>('all');
  useEffect(() => {
    const controller = new AbortController();
    let inflight = false;
    async function refresh() {
      if (inflight) return;
      inflight = true;
      try {
        const response = await fetch(`${url}?offset=${offset}&limit=50`, {
          signal: AbortSignal.any([controller.signal, AbortSignal.timeout(10000)]),
        });
        if (!response.ok) throw new Error();
        const next: ExnessExecution = await response.json();
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
  }, [offset, revision]);

  async function action(path: string, method: string, body?: object) {
    setBusy(true);
    try {
      const response = await fetch(url + path, {
        method,
        headers: { 'Content-Type': 'application/json' },
        body: body ? JSON.stringify(body) : undefined,
      });
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
  const strategies = Object.entries(data?.learning?.strategy_summary ?? {})
    .filter(([, stats]) => stats.signals > 0)
    .sort((x, y) => (y[1].mean_r ?? -99) - (x[1].mean_r ?? -99));
  const picked = data?.learning?.latest?.selected;
  const state = !data?.worker_online ? 'offline' : data.enabled ? 'live' : 'paused';
  const label = { offline: 'Worker offline', live: 'Đang chạy', paused: 'Tạm dừng mở lệnh' }[state];
  return (
    <div className="execution-desk">
      <section className="panel prose forex-execution">
        <div className="section-head">
          <div>
            <h2>Bot Exness Demo</h2>
            <p>
              Tài khoản {data?.account ?? '—'} · {data?.lot ?? '0.01'} lot · tối đa {data?.max_concurrent ?? 1} vị
              thế cùng lúc
            </p>
          </div>
          <button
            className="secondary"
            disabled={busy || !data?.configured || !!error}
            onClick={() => void action('', 'PATCH', { enabled: !data?.enabled })}
          >
            {data?.enabled ? 'Tạm dừng mở lệnh' : 'Bật bot demo'}
          </button>
        </div>
        {error && (
          <p role="alert" className="feedback">
            {error}
          </p>
        )}
        {!data && !error && <p role="status">Đang tải lệnh thật từ tài khoản demo...</p>}
        {data && (
          <>
            <div className="forex-exec-hero">
              <div className="forex-exec-card">
                <span className="label">Trạng thái bot</span>
                <span className="val">
                  <span className={`badge-status badge-${state}`}>
                    <span className="pulse-dot" />
                    {label}
                  </span>
                </span>
                <span className="sub">Đồng bộ sàn: {date(data.synced_at)}</span>
              </div>
              <div className="forex-exec-card">
                <span className="label">Balance / Equity · USD</span>
                <span className="val">
                  {amount(data.account_state?.balance)} / {amount(data.account_state?.equity)}
                </span>
                <span className="sub">
                  Margin đã dùng: {amount(data.account_state?.used_margin)}
                </span>
              </div>
              <div className="forex-exec-card">
                <span className="label">Profit deal sàn · USD</span>
                <span className={`val ${data.broker_profit >= 0 ? 'positive' : 'negative'}`}>
                  {amount(data.broker_profit)}
                </span>
                <span className="sub">
                  {closedRows.length
                    ? `Thắng ${wins}/${closedRows.length} (${Math.round((wins / closedRows.length) * 100)}%) trên trang này · chưa xác minh phí/swap`
                    : 'Chưa có lệnh đóng'}
                </span>
              </div>
              <div className="forex-exec-card">
                <span className="label">Đang mở / Đã đóng</span>
                <span className="val">
                  {data.open_count} / {data.closed_count}
                </span>
                <span className="sub">Giới hạn mở: {data.max_concurrent}</span>
              </div>
            </div>
            <div className="decision-banner" role="status">
              <span>
                <strong>Bot đang:</strong>{' '}
                {data.decision ?? 'Chờ worker xác minh demo và đồng bộ sàn'}
                {data.error ? ` · Lỗi: ${data.error}` : ''}
              </span>
              <span className="muted">
                {data.learning?.latest?.mode ?? '—'} · {picked ?? 'không có tín hiệu chọn'}
              </span>
            </div>
            <nav className="execution-tabs" role="tablist" aria-label="Không gian bot">
              {panels.map(([key, title, subtitle], index) => (
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
                  <span>
                    {title}
                    {key === 'orders' ? ` · ${data.open_count} mở` : ''}
                  </span>
                  <small>{subtitle}</small>
                </button>
              ))}
            </nav>
            <section
              hidden={panel !== 'learning'}
              id="execution-panel-learning"
              role="tabpanel"
              aria-labelledby="execution-tab-learning"
              tabIndex={0}
              className="execution-tab-content"
              aria-label="Bộ học chiến lược"
            >
              <div className="section-head">
                <h3>Bộ học tự sinh chiến lược</h3>
                <span className="muted">
                  {data.learning?.processed_bars ?? 0} nến · cập nhật{' '}
                  {date(data.learning?.updated_at ?? null)}
                </span>
              </div>
              <p className="micro">
                Trượt giá giả lập {data.learning?.assumptions?.slippage_bps_each_side ?? 0} bps (đo
                từ {data.learning?.assumptions?.slippage_measurement?.fills ?? 0} fill demo). Nhãn
                học từ sàn: {data.learning?.broker_learning?.closed_labels ?? 0} lệnh. Bảng dưới là
                mô phỏng chồng lấn, không phải lợi nhuận ví; lệnh thăm dò chưa có bằng chứng lãi.
              </p>
              <div className="table-wrap">
                <table className="learner-grid-table">
                  <thead>
                    <tr>
                      <th>Chiến lược</th>
                      <th>Tín hiệu</th>
                      <th>Đã đóng</th>
                      <th>R trung bình</th>
                      <th>Loại</th>
                    </tr>
                  </thead>
                  <tbody>
                    {strategies.map(([name, stats]) => (
                      <tr key={name} className={name === picked ? 'active-strat' : ''}>
                        <td>
                          {name}
                          {name === picked ? ' · đang chọn' : ''}
                        </td>
                        <td>{stats.signals}</td>
                        <td>{stats.closed}</td>
                        <td
                          className={
                            stats.mean_r == null ? '' : stats.mean_r >= 0 ? 'positive' : 'negative'
                          }
                        >
                          {amount(stats.mean_r)}
                        </td>
                        <td>{stats.excluded}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <p className="micro">
                Ngoài mẫu: {data.learning?.validation?.selected_closed ?? 0} tín hiệu được chọn · R
                bộ học {amount(data.learning?.validation?.selected_mean_r)} · R router cũ{' '}
                {amount(data.learning?.validation?.baseline_mean_r)}.
              </p>
            </section>
            <section
              hidden={panel !== 'orders'}
              id="execution-panel-orders"
              role="tabpanel"
              aria-labelledby="execution-tab-orders"
              tabIndex={0}
              className="execution-tab-content"
            >
              <div className="section-head">
                <div>
                  <h3>Vị thế & lịch sử giao dịch</h3>
                  <p className="micro">
                    Dữ liệu từ Exness · chọn một vị thế để mở chart khớp lệnh.
                  </p>
                </div>
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
              </div>
              <p className="micro">
                SL/TP gửi kèm lệnh; TP theo 2R từ quote gửi lệnh. Tạm dừng không đóng vị thế đang
                mở. Mac ngủ thì bot không mở/đóng chủ động, SL/TP trên sàn vẫn có hiệu lực; khi mở
                lại sẽ đối soát fill thật.
              </p>
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Position / nguồn</th>
                      <th>Chiều · lot</th>
                      <th>Vào / thời gian</th>
                      <th>SL / TP sàn</th>
                      <th>Thoát / thời gian</th>
                      <th>Profit USD</th>
                      <th>Trạng thái / chart</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((position) => (
                      <tr
                        key={position.position_id}
                        className={selectedId === position.position_id ? 'broker-selected' : ''}
                      >
                        <td>
                          #{position.position_id}
                          <br />
                          <small>
                            {position.source === 'learning'
                              ? 'BOT · learning'
                              : position.source === 'regime_router'
                                ? 'BOT · router cũ'
                                : 'Probe / lệnh khác'}
                          </small>
                        </td>
                        <td>
                          {position.side?.toUpperCase() ?? '—'} · {amount(position.volume)}
                        </td>
                        <td>
                          {amount(position.open_price)}
                          <br />
                          <small>{date(position.open_time)}</small>
                        </td>
                        <td>
                          {amount(position.stop)} / {amount(position.target)}
                        </td>
                        <td>
                          {amount(position.close_price)}
                          <br />
                          <small>{date(position.close_time)}</small>
                        </td>
                        <td
                          className={
                            position.pnl == null ? '' : position.pnl >= 0 ? 'positive' : 'negative'
                          }
                        >
                          {amount(position.pnl)}
                        </td>
                        <td>
                          {position.status === 'open'
                            ? 'Đang mở'
                            : position.status === 'closed'
                              ? 'Đã đóng'
                              : 'Chưa đủ lịch sử'}
                          <br />
                          <button
                            className="secondary"
                            aria-pressed={selectedId === position.position_id}
                            onClick={() => {
                              setSelectedId(position.position_id);
                              setPanel('chart');
                            }}
                          >
                            Xem chart & deal
                          </button>
                          {position.status === 'open' && position.signal_time != null && (
                            <button
                              className="secondary"
                              disabled={
                                busy || !!error || position.close_requested || !data.worker_online
                              }
                              onClick={() => {
                                if (
                                  window.confirm(
                                    `Đóng vị thế demo #${position.position_id} theo giá thị trường?`,
                                  )
                                )
                                  void action(`/trades/${position.signal_time}/close?position_id=${position.position_id}`, 'POST');
                              }}
                            >
                              {position.close_requested ? 'Đã yêu cầu đóng' : 'Đóng vị thế'}
                            </button>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {!rows.length && (
                <p>Không có lệnh khớp bộ lọc. Bot chờ tín hiệu mới, không ép mở lệnh.</p>
              )}
              <div className="forex-chart-controls">
                <button disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 50))}>
                  Trang trước
                </button>
                <span>
                  {data.total ? offset + 1 : 0}–{Math.min(offset + 50, data.total)} / {data.total} ·
                  bộ lọc áp dụng trang hiện tại
                </span>
                <button disabled={!data.has_more} onClick={() => setOffset(offset + 50)}>
                  Trang sau
                </button>
              </div>
              {selected && (
                <details open key={selected.position_id}>
                  <summary>
                    Deal khớp sàn #{selected.position_id} ·{' '}
                    {selected.reason ?? 'Lệnh ngoài executor'}
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
                              {amount(deal.price)} · {amount(deal.volume)}
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
          </>
        )}
      </section>
      {data && (
        <>
          <section
            hidden={panel !== 'chart'}
            id="execution-panel-chart"
            role="tabpanel"
            aria-labelledby="execution-tab-chart"
            tabIndex={0}
            className="execution-tab-content execution-chart-panel"
          >
            <div className="execution-selection">
              <div>
                <span className="execution-eyebrow">GIAO DỊCH / XAUUSD</span>
                <h3>
                  {chartPosition
                    ? `Vị thế #${chartPosition.position_id} · ${chartPosition.side?.toUpperCase() ?? '—'}`
                    : 'Theo dõi thị trường & giá khớp'}
                </h3>
                <p>
                  {chartPosition
                    ? `Vào ${amount(chartPosition.open_price)} · SL ${amount(chartPosition.stop)} · TP ${amount(chartPosition.target)}`
                    : 'Chart chỉ hiển thị vị thế đang mở. Lịch sử lệnh đã đóng nằm ở tab Lệnh trên sàn.'}
                </p>
              </div>
              <button type="button" className="secondary" onClick={() => setPanel('orders')}>
                Xem lệnh & deal
              </button>
            </div>
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
                  data.worker_online &&
                  window.confirm(`Đóng vị thế demo #${position.position_id} theo giá thị trường?`)
                )
                  void action(`/trades/${position.signal_time}/close?position_id=${position.position_id}`, 'POST');
              }}
            />
          </section>
          <section
            hidden={panel !== 'replay'}
            id="execution-panel-replay"
            role="tabpanel"
            aria-labelledby="execution-tab-replay"
            tabIndex={0}
            className="execution-tab-content execution-replay-panel"
          >
            <div className="execution-selection">
              <div>
                <span className="execution-eyebrow">NGHIÊN CỨU / KHÔNG GỬI LỆNH</span>
                <h3>Phòng thử nghiệm chiến lược</h3>
                <p>Replay tách biệt tài khoản demo. Kết quả mô phỏng không thay thế fill từ sàn.</p>
              </div>
            </div>
            <ForexReplayControl />
          </section>
        </>
      )}
    </div>
  );
}
