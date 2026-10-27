import { ArrowUpRight, SlidersHorizontal } from 'lucide-react';
import type { Signal } from '../types/trading';
import { formatPrice } from '../lib/format';
import { EmptyState } from './EmptyState';
import { formatTime } from '../lib/time-format';
export function SignalsTable({
  entries,
  filter,
  setFilter,
}: {
  entries: Signal[];
  filter: string;
  setFilter: (v: string) => void;
}) {
  return (
    <section className="panel signals-panel min-w-0 rounded-lg border border-border-base bg-surface-1 p-4">
      <div className="section-head mb-3 flex min-w-0 flex-wrap items-center justify-between gap-2 [&_p]:text-xs [&_p]:text-text-muted">
        <div className="heading-with-count">
          <h2>Tín hiệu gần nhất</h2>
          <span className="count">{entries.length}</span>
        </div>
        <label className="filter">
          <SlidersHorizontal size={14} />
          <select
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            aria-label="Lọc tín hiệu"
          >
            <option value="all">Tất cả tín hiệu</option>
            <option value="long">Long</option>
            <option value="short">Short</option>
          </select>
        </label>
      </div>
      <div className="table-scroll min-w-0 max-w-full overflow-x-auto overscroll-x-contain">
        <table>
          <thead>
            <tr>
              <th>THỊ TRƯỜNG</th>
              <th>TÍN HIỆU</th>
              <th>ENTRY</th>
              <th>STOP-LOSS</th>
              <th>MỤC TIÊU</th>
              <th>NGUỒN</th>
            </tr>
          </thead>
          <tbody>
            {entries.map((signal) => (
              <tr key={signal.id} title={signal.reason}>
                <td>
                  <strong>{signal.symbol}</strong>
                  <small>{formatTime(signal.time, true)}</small>
                </td>
                <td>
                  <span className={`pill ${signal.side === 'long' ? 'green' : 'amber'} inline-flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 text-xs font-medium bg-surface-3 text-text-secondary`}>
                    <ArrowUpRight size={12} />
                    {signal.side.toUpperCase()}
                  </span>
                </td>
                <td>{formatPrice(signal.entry)}</td>
                <td>{formatPrice(signal.stop)}</td>
                <td>{formatPrice(signal.target)}</td>
                <td>
                  <span className="pill inline-flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 text-xs font-medium bg-surface-3 text-text-secondary">
                    {signal.source === 'mexc_futures' ? 'MEXC Futures' : 'Demo replay'}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {!entries.length && (
        <EmptyState
          title="Chờ một setup đủ điều kiện"
          description="Tín hiệu xuất hiện khi nến đóng vượt đỉnh hoặc phá đáy 20 nến theo cấu hình nguồn dữ liệu. Không có lệnh nào được gửi."
        />
      )}
    </section>
  );
}
