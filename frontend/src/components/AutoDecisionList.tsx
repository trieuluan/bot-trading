import type { AutoDecision } from '../types/paper';
import { tradeStrategy } from '../lib/futures-strategy-info';
import { formatTime } from '../lib/time-format';
import { timeZoneLabel } from '../lib/time-zone';

const actions = { opened: 'Đã mở lệnh', skipped: 'Bỏ qua', no_setup: 'Chưa có setup' };
export function AutoDecisionList({
  decisions,
  onViewTrade,
}: {
  decisions: AutoDecision[];
  onViewTrade: (id: number) => void;
}) {
  return (
    <section className="panel prose">
      <div className="section-head">
        <h2>Nhật ký quyết định Auto Paper</h2>
        <span className="muted">12 nến được đánh giá gần nhất</span>
      </div>
      {!decisions.length ? (
        <p>Chưa có quyết định. Bật Auto Paper để chờ nến 15m mới đóng.</p>
      ) : (
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>Nến tín hiệu ({timeZoneLabel()})</th>
                <th>Kết quả</th>
                <th>Cây chiến thuật</th>
                <th>Lý do</th>
                <th>Lệnh</th>
              </tr>
            </thead>
            <tbody>
              {decisions.map((decision) => (
                <tr key={decision.candle_time}>
                  <td>{formatTime(decision.candle_time, false, false)}</td>
                  <td>
                    <span className={`pill ${decision.action === 'opened' ? 'green' : ''}`}>
                      {actions[decision.action]}
                    </span>
                  </td>
                  <td>
                    {tradeStrategy(decision.parameters.audit)?.title ?? 'Chưa lưu cây được chọn'}
                    <small>{tradeStrategy(decision.parameters.audit)?.generation}</small>
                  </td>
                  <td className="auto-decision-reason">{decision.reason}</td>
                  <td>
                    {decision.trade_id ? (
                      <button
                        className="text-button"
                        onClick={() => onViewTrade(decision.trade_id!)}
                      >
                        #{decision.trade_id} ↗ Chart
                      </button>
                    ) : (
                      '—'
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p className="micro">
        Thời gian là lúc mở nến tín hiệu; nến đóng sau 15 phút. Mỗi nến được quyết định một lần.
        Những nến trong thời gian auto tắt hoặc worker ngừng không được đặt bù.
      </p>
    </section>
  );
}
