import { ArrowUpRight, SlidersHorizontal } from 'lucide-react';
import type { Signal } from '../types/trading';
import { formatPrice } from '../lib/format';
import { EmptyState } from './EmptyState';
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
    <section className="panel signals-panel">
      <div className="section-head">
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
      <div className="table-scroll">
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
                  <small>
                    {new Date(signal.time * 1000).toLocaleString('vi-VN', { timeZone: 'UTC' })} UTC
                  </small>
                </td>
                <td>
                  <span className={`pill ${signal.side === 'long' ? 'green' : 'amber'}`}>
                    <ArrowUpRight size={12} />
                    {signal.side.toUpperCase()}
                  </span>
                </td>
                <td>{formatPrice(signal.entry)}</td>
                <td>{formatPrice(signal.stop)}</td>
                <td>{formatPrice(signal.target)}</td>
                <td>
                  <span className="pill">
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
