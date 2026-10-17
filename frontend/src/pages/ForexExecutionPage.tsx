import { useEffect, useMemo, useState } from 'react';
import type { ExnessExecution } from '../types/exness-execution';
import type { ForexDashboard } from '../App';
import { ForexStrategyPage } from './ForexStrategyPage';

const url = '/api/workspaces/exness_forex/execution';
const amount = (value: number | string | null | undefined) =>
  value == null ? '—' : Number(value).toLocaleString('vi-VN', { maximumFractionDigits: 3 });
const date = (stamp: number | null) =>
  stamp == null ? '—' : new Date(stamp * 1000).toLocaleString('vi-VN');

const outcomeText = (outcome: string) => {
  const key = outcome.replace(/^(blocked|unavailable):/, '');
  const labels: Record<string, string> = {
    no_signal: 'Không có tín hiệu',
    shadow_only: 'Chỉ có gen shadow',
    not_selected: 'Có tín hiệu, bot không chọn',
    signal: 'Có tín hiệu chọn',
    order_sent: 'Đã gửi lệnh',
    order_planned: 'Đã lập lệnh',
    execution_error: 'Lỗi khi gửi lệnh sàn',
    api_rate_limited: 'Tạm dừng: chạm giới hạn tần suất Exness',
    worker_error: 'Lỗi vòng xử lý bot; chưa xác nhận gửi lệnh',
    old_or_duplicate_signal: 'Tín hiệu cũ/trùng',
    position_capacity_for_split: 'Không đủ chỗ chia lệnh',
    reconciling: 'Đang đối soát lệnh',
    position_active: 'Đang có vị thế',
    session_close_near: 'Gần đóng phiên',
    history_catching_up: 'Đang đồng bộ lịch sử',
    paused: 'Bot tạm dừng',
    feed_not_ready: 'Dữ liệu nến chưa sẵn sàng',
    outside_signal_window: 'Quá cửa sổ 300s sau nến đóng',
    ask_candle_missing: 'Thiếu nến ask',
    policy_missing: 'Chưa có policy học',
    preflight_failed: 'Kiểm tra trước lệnh thất bại',
  };
  return labels[key] ?? key;
};
const outcomeClass = (outcome: string) =>
  outcome === 'order_sent'
    ? 'positive'
    : outcome === 'api_rate_limited'
      ? 'negative'
    : outcome.startsWith('blocked') ||
        outcome.startsWith('unavailable') ||
        outcome === 'worker_error' ||
        outcome === 'execution_error'
      ? 'negative'
      : 'muted';

type Panel = 'chart' | 'orders' | 'learning' | 'strategies';
const panels: [Panel, string, string][] = [
  ['chart', 'Chart & Quan sát', 'Biểu đồ, cản H1/H4 và phân tích bot'],
  ['orders', 'Lệnh trên sàn', 'Vị thế mở và lịch sử deal khớp'],
  ['learning', 'Bộ học & Thích ứng', 'Giả thuyết, bối cảnh SL/TP và R thực tế'],
  ['strategies', 'Chiến lược MTF', 'Chi tiết điều kiện 4h/1h/15m từng chiến lược'],
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
  const [panel, setPanel] = useState<Panel>('chart');
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
              Tài khoản {data?.account ?? '—'} · {data?.lot ?? '0.01'} lot · tối đa{' '}
              {data?.max_concurrent ?? 1} vị thế cùng lúc
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
              className="execution-tab-content learning-tab-view"
              aria-label="Bộ học chiến lược"
            >
              <div className="learning-overview-grid">
                <div className="learning-stat-card">
                  <span className="card-label">Quần thể gen</span>
                  <div className="card-value">
                    {data.learning?.evolution
                      ? Object.values(data.learning.evolution.families).reduce((a, b) => a + b, 0)
                      : 0}
                    <small className="muted"> / {data.learning?.evolution?.cap ?? 40}</small>
                  </div>
                  <div className="card-sub">
                    {data.learning?.evolution?.shadow ?? 0} shadow ·{' '}
                    <span className={data.learning?.promoted?.length ? 'positive font-bold' : ''}>
                      {data.learning?.promoted?.length ?? 0} duyệt demo
                    </span>
                  </div>
                </div>

                <div className="learning-stat-card">
                  <span className="card-label">Kiểm định ngoài mẫu (Val)</span>
                  <div className="card-value">
                    {amount(data.learning?.validation?.selected_mean_r)}
                    <small className="muted"> R TB</small>
                  </div>
                  <div className="card-sub">
                    {data.learning?.validation?.selected_closed ?? 0} mẫu · router cũ{' '}
                    {amount(data.learning?.validation?.baseline_mean_r)}
                  </div>
                </div>

                <div className="learning-stat-card">
                  <span className="card-label">SL/TP học thích ứng</span>
                  <div className="card-value">
                    {
                      Object.values(data.learning?.exit_learning?.contexts ?? {}).filter(
                        (row) => row.eligible,
                      ).length
                    }
                    <small className="muted">
                      {' '}
                      / {Object.keys(data.learning?.exit_learning?.contexts ?? {}).length || '—'} đủ
                      chuẩn
                    </small>
                  </div>
                  <div className="card-sub">
                    Mặc định: <code>{data.learning?.exit_learning?.default_variant ?? '—'}</code>
                  </div>
                </div>

                <div className="learning-stat-card">
                  <span className="card-label">Dữ liệu huấn luyện</span>
                  <div className="card-value">
                    {data.learning?.processed_bars ?? 0}
                    <small className="muted"> nến M15</small>
                  </div>
                  <div className="card-sub">
                    {data.learning?.broker_learning?.closed_labels ?? 0} lệnh sàn · trượt{' '}
                    {data.learning?.assumptions?.slippage_bps_each_side ?? 0} bps
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
                {data.funnel && (
                  <div className="funnel-row">
                    {(
                      [
                        ['Đã đánh giá', data.funnel.evaluated],
                        ['Có tín hiệu', data.funnel.with_signal],
                        ['Bot chọn', data.funnel.selected],
                        ['Đã gửi lệnh', data.funnel.orders],
                      ] as [string, number][]
                    ).map(([name, value]) => (
                      <div key={name} className="funnel-step">
                        <strong>{value}</strong>
                        <span>{name}</span>
                      </div>
                    ))}
                  </div>
                )}
                {data.journal?.length ? (
                  <div className="table-wrap">
                    <table className="learner-clean-table">
                      <thead>
                        <tr>
                          <th>Nến đóng</th>
                          <th>Kết quả</th>
                          <th>Bot chọn</th>
                          <th>Tín hiệu thấy được</th>
                        </tr>
                      </thead>
                      <tbody>
                        {data.journal.slice(0, 12).map((row) => (
                          <tr key={row.time}>
                            <td>{date(row.time + 900)}</td>
                            <td className={outcomeClass(row.outcome)}>
                              {outcomeText(row.outcome)}
                              {row.reason && <small className="muted"> · {row.reason}</small>}
                            </td>
                            <td>{row.selected ?? '—'}</td>
                            <td className="muted">
                              {row.confirmed.length
                                ? row.confirmed
                                    .map((c) => `${c.strategy}${c.shadow ? ' (shadow)' : ''}`)
                                    .join(', ')
                                : '—'}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ) : (
                  <p className="muted-desc">
                    Chưa có nến nào được ghi nhật ký. Chờ nến M15 kế tiếp.
                  </p>
                )}
              </div>

              <div className="learning-section-card">
                <div className="card-header">
                  <div>
                    <h4>Shadow thời gian thực (forward)</h4>
                    <span className="muted-desc">
                      Chỉ tín hiệu phát sinh sau{' '}
                      {date(data.learning?.forward_shadow?.since ?? null)}; khóa phiên bản gen,
                      không ghi đè kết quả đã đóng. Mô phỏng, không phải lệnh sàn.
                    </span>
                  </div>
                  <span className="badge-count">
                    {data.learning?.forward_shadow?.signals ?? 0} tín hiệu
                  </span>
                </div>
                {data.learning?.forward_shadow?.strategies.length ? (
                  <div className="table-wrap">
                    <table className="learner-clean-table">
                      <thead>
                        <tr>
                          <th>Chiến lược</th>
                          <th className="num">Tín hiệu</th>
                          <th className="num">Đang chạy</th>
                          <th className="num">Đã đóng</th>
                          <th className="num">R TB</th>
                          <th className="num">R thấp nhất</th>
                        </tr>
                      </thead>
                      <tbody>
                        {data.learning.forward_shadow.strategies.slice(0, 15).map((row) => (
                          <tr key={row.strategy + row.version}>
                            <td>
                              <span className="strategy-name">{row.strategy}</span>
                              <small className="muted"> {row.version.slice(0, 6)}</small>
                              {!row.costs_verified && (
                                <small className="muted"> · chi phí chưa xác minh</small>
                              )}
                            </td>
                            <td className="num">{row.signals}</td>
                            <td className="num">{row.open}</td>
                            <td className="num">{row.resolved}</td>
                            <td className="num font-mono">{amount(row.mean_r)}</td>
                            <td className="num font-mono">{amount(row.lower_r)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ) : (
                  <p className="muted-desc">
                    Chưa có tín hiệu forward. Nến M15 kế tiếp sẽ được ghi nhận.
                  </p>
                )}
              </div>

              <div className="learning-section-card">
                <div className="card-header">
                  <div>
                    <h4>Scalping M1</h4>
                    <span className="muted-desc">
                      {data.learning?.scalping?.rule ?? 'Donchian20 + EMA20/50 trên nến M1'}
                    </span>
                  </div>
                  <span className="badge-count">Mô phỏng · chưa gửi lệnh sàn</span>
                </div>
                <div className="scalp-body">
                  <div className="scalp-meta">
                    <span>
                      {data.learning?.scalping?.status === 'waiting_m1_feed'
                        ? 'Đang tải lịch sử M1'
                        : 'M1 đã đồng bộ'}
                    </span>
                    {data.learning?.scalping?.replay && (
                      <span>
                        {amount(data.learning.scalping.replay.candles)} nến ·{' '}
                        {date(data.learning.scalping.replay.from)} –{' '}
                        {date(data.learning.scalping.replay.to)}
                      </span>
                    )}
                  </div>
                  <div className="scalp-tiles">
                    {(
                      [
                        ['Train 70%', data.learning?.scalping?.replay?.train, 30],
                        ['Validation 30%', data.learning?.scalping?.replay?.validation, 30],
                        ['Shadow live', data.learning?.scalping?.forward, 30],
                      ] as const
                    ).map(([label, stats, need]) => (
                      <div key={label} className="scalp-tile">
                        <span className="scalp-tile-label">{label}</span>
                        <strong
                          className={
                            stats?.lower_r == null
                              ? 'muted'
                              : stats.lower_r > 0
                                ? 'positive'
                                : 'negative'
                          }
                        >
                          {stats?.mean_r == null
                            ? '—'
                            : `${stats.mean_r > 0 ? '+' : ''}${amount(stats.mean_r)} R`}
                        </strong>
                        <small>
                          {stats?.resolved ?? 0}/{need} lệnh · cận dưới {amount(stats?.lower_r)} ·
                          thắng{' '}
                          {stats?.win_rate == null ? '—' : `${Math.round(stats.win_rate * 100)}%`}
                        </small>
                      </div>
                    ))}
                  </div>
                  <div className="table-wrap">
                    <table className="learner-clean-table">
                      <thead>
                        <tr>
                          <th>Biến thể</th>
                          <th className="num">Train</th>
                          <th className="num">Validation</th>
                          <th className="num">Shadow live</th>
                          <th>Lý do chưa duyệt</th>
                        </tr>
                      </thead>
                      <tbody>
                        {data.learning?.scalping?.variants?.map((row) => (
                          <tr
                            key={row.id}
                            className={row.id === data.learning?.scalping?.train_candidate ? 'active-strat' : ''}
                          >
                            <td>
                              <span className="strategy-name">{row.label}</span>
                              {row.id === data.learning?.scalping?.train_candidate && (
                                <span className="active-pill">ứng viên train</span>
                              )}
                            </td>
                            {[row.train, row.validation, row.forward].map((stats, index) => (
                              <td key={index} className="num">
                                <span
                                  className={
                                    stats.lower_r == null ? '' : stats.lower_r > 0 ? 'positive' : 'negative'
                                  }
                                >
                                  {stats.mean_r == null ? '—' : `${amount(stats.mean_r)} R`}
                                </span>
                                <small className="muted"> n={stats.resolved}</small>
                              </td>
                            ))}
                            <td className="scalp-reasons">{row.gate.join(' · ') || 'Đạt'}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  <p className="scalp-note">
                    Bid/ask, vào ở giá mở nến kế tiếp; nến chạm cả SL và TP tính SL trước. Slippage
                    chưa hiệu chuẩn cho M1, chưa mô phỏng tick và độ trễ khớp lệnh.
                  </p>
                </div>
              </div>

              <div className="learning-rule-banner">
                <div className="rule-badge">Điều kiện duyệt</div>
                <div className="rule-text">
                  Chiến lược gen cần <strong>≥30 mẫu train</strong> &{' '}
                  <strong>≥30 validation</strong>, <strong>lower-bound R &gt; 0</strong> cả hai tập,
                  và chi phí sàn đã xác minh để được vào lệnh demo.
                </div>
              </div>

              <div className="learning-section-card">
                <div className="card-header">
                  <div>
                    <h4>Hiệu suất chiến lược</h4>
                    <span className="muted-desc">
                      Tập hợp các chiến lược gốc và gen đã sinh tín hiệu
                    </span>
                  </div>
                  <span className="badge-count">{strategies.length} chiến lược</span>
                </div>
                <div className="table-wrap">
                  <table className="learner-clean-table">
                    <thead>
                      <tr>
                        <th>Chiến lược</th>
                        <th className="num">Tín hiệu</th>
                        <th className="num">Đã đóng</th>
                        <th className="num">R trung bình</th>
                        <th className="num">Loại</th>
                      </tr>
                    </thead>
                    <tbody>
                      {strategies.map(([name, stats]) => (
                        <tr key={name} className={name === picked ? 'active-strat' : ''}>
                          <td>
                            <span className="strategy-name">{name}</span>
                            {name === picked && <span className="active-pill">Đang chọn</span>}
                          </td>
                          <td className="num">{stats.signals}</td>
                          <td className="num">{stats.closed}</td>
                          <td
                            className={`num font-mono ${
                              stats.mean_r == null
                                ? ''
                                : stats.mean_r >= 0
                                  ? 'positive'
                                  : 'negative'
                            }`}
                          >
                            {amount(stats.mean_r)}
                          </td>
                          <td className="num muted">{stats.excluded}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>

              {!!data.learning?.shadow_details?.length && (
                <div className="learning-section-card">
                  <div className="card-header">
                    <div>
                      <h4>Danh sách Shadow & Lý do chưa duyệt</h4>
                      <span className="muted-desc">
                        Theo dõi tiến độ tích lũy mẫu và kiểm định thống kê của từng gen
                      </span>
                    </div>
                    <span className="badge-count">
                      {data.learning.shadow_details.filter((g) => g.status !== 'promoted').length}{' '}
                      gen đang shadow
                    </span>
                  </div>
                  <div className="table-wrap">
                    <table className="learner-clean-table">
                      <thead>
                        <tr>
                          <th>Gen</th>
                          <th>Trạng thái</th>
                          <th>Tiến độ Train</th>
                          <th>Tiến độ Val</th>
                          <th>R thấp nhất (Train / Val)</th>
                          <th>Lý do còn shadow</th>
                        </tr>
                      </thead>
                      <tbody>
                        {data.learning.shadow_details.map((g) => (
                          <tr key={g.name} className={g.status === 'retired' ? 'retired-row' : ''}>
                            <td>
                              <code className="gen-code">{g.name}</code>
                            </td>
                            <td>
                              <span className={`status-pill status-${g.status}`}>{g.status}</span>
                            </td>
                            <td>
                              <div className="progress-cell">
                                <span>{g.train}/30</span>
                                <div className="mini-progress-bar">
                                  <div
                                    className="bar-fill"
                                    style={{ width: `${Math.min(100, (g.train / 30) * 100)}%` }}
                                  />
                                </div>
                              </div>
                            </td>
                            <td>
                              <div className="progress-cell">
                                <span>{g.validation}/30</span>
                                <div className="mini-progress-bar">
                                  <div
                                    className="bar-fill"
                                    style={{
                                      width: `${Math.min(100, (g.validation / 30) * 100)}%`,
                                    }}
                                  />
                                </div>
                              </div>
                            </td>
                            <td className="font-mono">
                              <span
                                className={
                                  g.lower_r_train != null && g.lower_r_train > 0 ? 'positive' : ''
                                }
                              >
                                {amount(g.lower_r_train)}
                              </span>{' '}
                              /{' '}
                              <span
                                className={
                                  g.lower_r_validation != null && g.lower_r_validation > 0
                                    ? 'positive'
                                    : ''
                                }
                              >
                                {amount(g.lower_r_validation)}
                              </span>
                            </td>
                            <td>
                              {g.reasons.length > 0 ? (
                                <ul className="reason-tags">
                                  {g.reasons.map((r, i) => (
                                    <li key={i}>{r}</li>
                                  ))}
                                </ul>
                              ) : (
                                <span className="positive">Đủ điều kiện</span>
                              )}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}
            </section>
            <section
              hidden={panel !== 'orders'}
              id="execution-panel-orders"
              role="tabpanel"
              aria-labelledby="execution-tab-orders"
              tabIndex={0}
              className="execution-tab-content orders-tab-view"
            >
              <div className="orders-overview-grid">
                <div className="learning-stat-card">
                  <span className="card-label">Vị thế đang mở</span>
                  <div className="card-value">
                    {data.open_count}
                    <small className="muted"> / {data.max_concurrent} max</small>
                  </div>
                  <div className="card-sub">
                    {data.open_count > 0 ? 'Vị thế ghi nhận từ Exness' : 'Chưa có vị thế mở'}
                  </div>
                </div>

                <div className="learning-stat-card">
                  <span className="card-label">Tổng lệnh đã đóng</span>
                  <div className="card-value">
                    {data.closed_count}
                    <small className="muted"> lệnh</small>
                  </div>
                  <div className="card-sub">
                    {wins}/{closedRows.length} thắng trong trang này (
                    {closedRows.length ? Math.round((wins / closedRows.length) * 100) : 0}%)
                  </div>
                </div>

                <div className="learning-stat-card">
                  <span className="card-label">P&amp;L đã chốt · trang này (USD)</span>
                  {(() => {
                    const totalPnl = closedRows.reduce((acc, p) => acc + (p.pnl ?? 0), 0);
                    return (
                      <>
                        <div
                          className={`card-value font-mono ${totalPnl >= 0 ? 'positive' : 'negative'}`}
                        >
                          {totalPnl >= 0 ? '+' : ''}
                          {amount(totalPnl)}
                        </div>
                        <div className="card-sub">
                          {closedRows.length} lệnh ghi nhận P&amp;L trên sàn
                        </div>
                      </>
                    );
                  })()}
                </div>

                <div className="learning-stat-card">
                  <span className="card-label">Trạng thái sàn</span>
                  <div className="card-value" style={{ fontSize: '18px', paddingTop: '6px' }}>
                    <span
                      className={`status-pill status-${data.worker_online ? (data.enabled ? 'promoted' : 'shadow') : 'retired'}`}
                    >
                      {label}
                    </span>
                  </div>
                  <div className="card-sub">
                    {data.orders.length} lệnh chờ · {data.intents.length} nhật ký
                  </div>
                </div>
              </div>

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
                                    ? 'Bot · Learning'
                                    : position.source === 'regime_router'
                                      ? 'Bot · Router cũ'
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
                              <div>{amount(position.open_price)}</div>
                              <small className="muted">{amount(position.close_price)}</small>
                            </td>
                            <td className="num font-mono">
                              <div>{amount(position.stop)}</div>
                              <small className="muted">{amount(position.target)}</small>
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
                                    setPanel('chart');
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
              <p className="micro">
                SL/TP gửi kèm lệnh; TP theo 2R từ quote gửi lệnh. Tạm dừng không đóng vị thế đang
                mở. Mac ngủ thì bot không mở/đóng chủ động, SL/TP trên sàn vẫn có hiệu lực; khi mở
                lại sẽ đối soát fill thật.
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
                    ? `Vào ${amount(chartPosition.open_price)} · SL ${amount(chartPosition.stop)} · TP ${amount(chartPosition.target)}${chartPosition.tp_reason ? ` · ${chartPosition.tp_reason}` : ''}${chartPosition.exit_variant ? ` · SL/TP ${chartPosition.exit_variant} (${chartPosition.exit_variant_mode === 'validated' ? 'đã kiểm chứng mô phỏng' : 'mặc định'})` : ''}`
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
                  void action(
                    `/trades/${position.signal_time}/close?position_id=${position.position_id}`,
                    'POST',
                  );
              }}
            />
          </section>
          <section
            hidden={panel !== 'strategies'}
            id="execution-panel-strategies"
            role="tabpanel"
            aria-labelledby="execution-tab-strategies"
            tabIndex={0}
            className="execution-tab-content"
          >
            <div className="execution-selection">
              <div>
                <span className="execution-eyebrow">QUAN SÁT PRICE ACTION</span>
                <h3>Phân tích đa khung 4h · 1h · 15m</h3>
                <p>
                  Theo dõi trực tiếp điều kiện kích hoạt Breakout + Retest, Trend Pullback, Range
                  Rejection.
                </p>
              </div>
            </div>
            {panel === 'strategies' && (
              <ForexStrategyPage
                dashboard={dashboard}
                disconnected={!!error}
                includeReplay={false}
              />
            )}
          </section>
        </>
      )}
    </div>
  );
}
