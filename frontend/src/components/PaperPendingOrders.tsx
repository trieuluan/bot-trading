import { useEffect, useState } from 'react';
import { cancelPaperPending } from '../lib/api';
import { formatQuote } from '../lib/format';
import { formatTime } from '../lib/time-format';
import type { PaperPendingOrder } from '../types/paper';

const labels = { pending: 'Đang chờ', filled: 'Đã khớp', cancelled: 'Đã hủy', expired: 'Hết hạn' };

export function PaperPendingOrders({
  orders,
  onView,
}: {
  orders: PaperPendingOrder[];
  onView: (order: PaperPendingOrder) => void;
}) {
  const [clock, setClock] = useState(Date.now());
  const [busy, setBusy] = useState<number | null>(null);
  const [error, setError] = useState('');
  const [cancelled, setCancelled] = useState<number[]>([]);
  useEffect(() => {
    const timer = window.setInterval(() => setClock(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);
  async function cancel(order: PaperPendingOrder) {
    setBusy(order.id);
    setError('');
    try {
      const result = await cancelPaperPending(order.id, order.symbol);
      if (result.status === 'cancelled') setCancelled((ids) => [...ids, order.id]);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Không hủy được lệnh chờ');
    } finally {
      setBusy(null);
    }
  }
  return (
    <>
      <h3>
        Lệnh chờ (
        {
          orders.filter((order) => order.status === 'pending' && !cancelled.includes(order.id))
            .length
        }
        )
      </h3>
      <p className="micro text-xs leading-relaxed text-text-muted">
        Paper · xét nến M15 đóng · tối đa 4 nến (60 phút). Chưa giữ ký quỹ; kiểm tra lại rủi ro khi
        khớp. Tắt Auto sẽ hủy lệnh chờ.
      </p>
      {error && (
        <p role="alert" className="negative text-bear">
          {error}
        </p>
      )}
      {!orders.length ? (
        <p>Chưa có lệnh chờ.</p>
      ) : (
        <div className="table-scroll min-w-0 max-w-full overflow-x-auto overscroll-x-contain">
          <table className="positions-table w-full border-collapse whitespace-nowrap text-xs">
            <thead>
              <tr>
                <th>Lệnh / Cặp</th>
                <th>Chiến thuật</th>
                <th>Giá chờ</th>
                <th>SL / TP</th>
                <th>RR gộp</th>
                <th>Hạn chờ</th>
                <th>Trạng thái</th>
                <th>Thao tác</th>
              </tr>
            </thead>
            <tbody>
              {orders.map((order) => {
                const status = cancelled.includes(order.id) ? 'cancelled' : order.status;
                const seconds = Math.max(0, Math.ceil(order.expires_at - clock / 1000));
                const remaining = `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`;
                const rr =
                  Math.abs(order.target - order.level) / Math.abs(order.level - order.stop);
                return (
                  <tr key={order.id}>
                    <td>
                      {status === 'pending' || order.trade_id ? (
                        <button className="text-button inline-flex items-center gap-1 text-xs text-bull underline-offset-4 hover:underline" onClick={() => onView(order)}>
                          #{order.id} {order.symbol.replace('_', ' / ')} ·{' '}
                          {order.side.toUpperCase()}
                        </button>
                      ) : (
                        `#${order.id} ${order.symbol.replace('_', ' / ')} · ${order.side.toUpperCase()}`
                      )}
                    </td>
                    <td>
                      <strong>{order.entry_policy}</strong>
                      <small>{order.strategy}</small>
                    </td>
                    <td>{formatQuote(order.level)}</td>
                    <td>
                      <span className="negative text-bear">{formatQuote(order.stop)}</span> /{' '}
                      <span className="positive text-bull">{formatQuote(order.target)}</span>
                    </td>
                    <td>{rr.toFixed(2)}R</td>
                    <td>
                      {status === 'pending' ? (seconds ? remaining : 'Chờ đối soát') : '—'}
                      <small>{formatTime(order.expires_at)}</small>
                    </td>
                    <td>
                      {labels[status]}
                      {order.reason && <small>{order.reason}</small>}
                      {order.trade_id && <small>Vị thế #{order.trade_id}</small>}
                    </td>
                    <td>
                      {status === 'pending' && (
                        <button
                          className="secondary inline-flex items-center justify-center gap-1.5 rounded-md border border-border-active bg-surface-2 px-3 py-1.5 text-xs text-text-secondary hover:bg-surface-3"
                          disabled={busy !== null}
                          onClick={() => void cancel(order)}
                          aria-label={`Hủy lệnh chờ ${order.symbol} #${order.id}`}
                        >
                          Hủy
                        </button>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
