import { getFuturesSymbol } from '../lib/futures-symbol';
import { useEffect, useState } from 'react';
import { fetchFuturesHistory, startFuturesHistory } from '../lib/api';
import type { FuturesHistoryJob, FuturesHistoryStats } from '../types/paper';
import { formatTime } from '../lib/time-format';

const dateLabel = (stamp: number) => formatTime(stamp, false, false).split(' ')[0];
const resultLabel = (stats: FuturesHistoryStats) =>
  `${stats.resolved} mẫu · ${stats.mean_r?.toFixed(2) ?? '—'}R · PF ${stats.no_losses ? 'Không có lỗ' : (stats.profit_factor?.toFixed(2) ?? '—')}`;

export function FuturesHistoryPanel() {
  const [job, setJob] = useState<FuturesHistoryJob | null>(null);
  const [days, setDays] = useState('30');
  const [busy, setBusy] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => {
    let disposed = false;
    let timer: ReturnType<typeof setTimeout>;
    async function refresh() {
      try {
        const result = await fetchFuturesHistory();
        if (!disposed) {
          setJob(result);
          setLoaded(true);
          setError('');
        }
      } catch (cause) {
        if (!disposed)
          setError(cause instanceof Error ? cause.message : 'Không tải được phiên học.');
      } finally {
        if (!disposed) timer = setTimeout(() => void refresh(), 5000);
      }
    }
    void refresh();
    return () => {
      disposed = true;
      clearTimeout(timer);
    };
  }, []);
  async function start() {
    setBusy(true);
    setError('');
    try {
      setJob(await startFuturesHistory(Number(days)));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Không khởi chạy được phiên học.');
    } finally {
      setBusy(false);
    }
  }
  const running = busy || job?.status === 'running';
  return (
    <section className="futures-history" aria-label="Học từ lịch sử Futures">
      <h3>Học từ lịch sử</h3>
      <p className="micro">
        {getFuturesSymbol()} · M15 · 70% học / 30% kiểm chứng. Replay độc lập, không tạo lệnh hay
        đổi số dư. Phí 8 bps (MEXC API taker) và trượt giá 2 bps mỗi chiều, có funding.
      </p>
      <div className="futures-history-actions">
        <label>
          Khoảng dữ liệu
          <select value={days} disabled={running} onChange={(event) => setDays(event.target.value)}>
            <option value="7">7 ngày</option>
            <option value="30">30 ngày</option>
            <option value="60">60 ngày</option>
            <option value="90">90 ngày</option>
          </select>
        </label>
        <button
          type="button"
          className="primary"
          disabled={running || !loaded}
          onClick={() => void start()}
        >
          {running ? 'Đang học…' : 'Chạy học lịch sử'}
        </button>
      </div>
      {job?.status === 'running' && (
        <div role="status">
          <p className="micro">
            {job.phase === 'candles'
              ? 'Đang tải nến'
              : job.phase === 'fair_price'
                ? 'Đang tải fair price / funding'
                : 'Đang replay'}
            {' · '}
            {job.completed.toLocaleString('vi-VN')} / {job.total.toLocaleString('vi-VN')} nến
          </p>
          <progress max={job.total} value={job.completed} aria-label="Tiến độ học lịch sử" />
        </div>
      )}
      {(error || job?.error) && (
        <p role="alert" className="negative">
          {error || job?.error}
        </p>
      )}
      {job?.report && (
        <details>
          <summary>
            {job.status !== 'completed' ? 'Kết quả phiên trước · ' : ''}
            {job.report.eligible_priors} chiến thuật đủ điều kiện hỗ trợ chọn
          </summary>
          <p className="micro">
            {dateLabel(job.report.start_time)} – {dateLabel(job.report.end_time)} · Chia tập tại{' '}
            {dateLabel(job.report.train_before)}. Mẫu vắt qua ranh giới tập hoặc chưa đóng bị loại.
            R là lợi nhuận theo đơn vị rủi ro.
          </p>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Chiến thuật</th>
                  <th>Tập học</th>
                  <th>Kiểm chứng</th>
                  <th>Sử dụng</th>
                </tr>
              </thead>
              <tbody>
                {job.report.strategies.map((row) => (
                  <tr key={row.strategy}>
                    <td title={row.strategy}>
                      {row.strategy.replace(/_/g, ' ').replace(':', ' · ')}
                    </td>
                    <td>{resultLabel(row.train)}</td>
                    <td>{resultLabel(row.validation)}</td>
                    <td>{row.prior_eligible ? 'Điểm khởi đầu' : 'Chưa đạt'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </details>
      )}
      <p className="micro">
        Cần ≥30 mẫu mỗi tập, R trung bình dương và PF &gt; 1.3 ở cả hai tập. Điểm khởi đầu dùng tối
        đa 30 mẫu tương đương mỗi bối cảnh; mẫu forward tiếp tục cập nhật. Không tự duyệt chiến
        thuật, không đổi chế độ hay bật Auto. Kiểm chứng lịch sử không phải kết quả forward.
      </p>
    </section>
  );
}
