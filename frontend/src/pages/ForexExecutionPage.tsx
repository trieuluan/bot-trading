import { useEffect, useState } from 'react';
import type { ExnessExecution } from '../types/exness-execution';
import type { ForexDashboard } from '../App';
import { ForexStrategyPage } from './ForexStrategyPage';

const url = '/api/workspaces/exness_forex/execution';
const amount = (value: number | string | null | undefined) =>
  value == null ? '—' : Number(value).toLocaleString('vi-VN', { maximumFractionDigits: 3 });
const date = (stamp: number | null) =>
  stamp == null ? '—' : new Date(stamp * 1000).toLocaleString('vi-VN');

export function ForexExecutionPage({ dashboard }: { dashboard: ForexDashboard }) {
  const [data, setData] = useState<ExnessExecution | null>(null);
  const [selectedId, setSelectedId] = useState<string>();
  const [offset, setOffset] = useState(0);
  const [revision, setRevision] = useState(0);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
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
  return (
    <>
      <section className="panel prose forex-execution">
        <div className="section-head">
          <div>
            <h2>Lệnh sàn Exness Demo</h2>
            <p>
              Tài khoản {data?.account ?? '—'} · regime_router · 0.01 lot · tối đa {data?.max_concurrent ?? 1} vị thế mỗi lúc ·
              không giới hạn tổng số lệnh.
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
            <div className="research-metrics">
              <div>
                <span className="muted">Executor</span>
                <strong>
                  {data.worker_online
                    ? data.enabled
                      ? 'Đang chạy'
                      : 'Tạm dừng mở lệnh'
                    : 'Worker offline'}
                </strong>
              </div>
              <div>
                <span className="muted">Balance / Equity USD</span>
                <strong>
                  {amount(data.account_state?.balance)} / {amount(data.account_state?.equity)}
                </strong>
              </div>
              <div>
                <span className="muted">Đang mở / Đã đóng</span>
                <strong>
                  {data.open_count} / {data.closed_count}
                </strong>
              </div>
              <div>
                <span className="muted">Profit từ deal sàn · USD</span>
                <strong className={data.broker_profit >= 0 ? 'positive' : 'negative'}>
                  {amount(data.broker_profit)}
                </strong>
              </div>
            </div>
            <p role="status">
              <strong>Bot đang chờ:</strong>{' '}
              {data.decision ?? 'Chờ worker xác minh demo và đồng bộ sàn'}
              {data.error ? ` · Lỗi: ${data.error}` : ''}
            </p>
            <p className="micro">
              SL/TP gửi kèm lệnh tới Exness. TP đặt theo 2R từ quote gửi lệnh, R thực tế có thể lệch
              khi khớp. Tạm dừng không đóng vị thế đang mở. Profit lấy từ broker, không mặc định bao
              gồm phí/swap. Đồng bộ: {date(data.synced_at)}.
            </p>
            <p className="micro">
              Nếu Mac ngủ, bot không mở hay chủ động đóng lệnh trước kỳ nghỉ; SL/TP đã đặt trên sàn
              vẫn do Exness xử lý. Khi mở máy, đối soát fill sàn; không giả lập giá đóng.
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
                  {data.positions.map((position) => (
                    <tr
                      key={position.position_id}
                      className={selectedId === position.position_id ? 'broker-selected' : ''}
                    >
                      <td>
                        #{position.position_id}
                        <br />
                        <small>
                          {position.source === 'regime_router'
                            ? 'BOT · router'
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
                          onClick={() => setSelectedId(position.position_id)}
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
                                void action(`/trades/${position.signal_time}/close`, 'POST');
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
            {!data.positions.length && (
              <p>
                Chưa có vị thế trong lịch sử đã đồng bộ. Bot chờ tín hiệu mới, không ép mở lệnh.
              </p>
            )}
            <div className="forex-chart-controls">
              <button disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 50))}>
                Trang trước
              </button>
              <span>
                {offset + 1}–{Math.min(offset + 50, data.total)} / {data.total}
              </span>
              <button disabled={!data.has_more} onClick={() => setOffset(offset + 50)}>
                Trang sau
              </button>
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
          </>
        )}
      </section>
      {data && (
        <ForexStrategyPage
          dashboard={dashboard}
          disconnected={!!error}
          executions={data.positions}
          selectedPosition={selected}
          executionOnly
        />
      )}
    </>
  );
}
