import type { PaperTrade } from '../types/paper';
import { formatPrice } from '../lib/format';

export const exitLabels: Record<string, string> = {
  manual: 'Đóng thủ công',
  stop_loss: 'Stop-loss (SL)',
  take_profit: 'Take-profit (TP)',
};
export const utcTime = (time: number) =>
  new Date(time * 1000).toLocaleString('vi-VN', { timeZone: 'UTC' }) + ' UTC';

export function PaperTradeDetails({ trade }: { trade: PaperTrade }) {
  const risk = Math.abs(trade.entry - trade.stop) * trade.quantity;
  const reward = Math.abs(trade.target - trade.entry) * trade.quantity;
  const closed = trade.exit_time !== null;
  const audit = trade.audit;
  return (
    <div className="paper-trade-details">
      <div className="section-head">
        <strong>
          #{trade.id} {trade.side.toUpperCase()} · {trade.origin === 'auto' ? 'AUTO' : 'THỦ CÔNG'}
        </strong>
        <span className={`pill ${closed ? 'amber' : ''}`}>
          {closed ? 'LỊCH SỬ · ĐÃ ĐÓNG' : 'VỊ THẾ ĐANG MỞ'}
        </span>
      </div>
      <p>
        {trade.origin === 'manual'
          ? 'Bạn mở lệnh Market thủ công; lệnh này không có tín hiệu breakout của bot.'
          : (trade.entry_reason ?? 'Lệnh auto cũ chưa lưu lý do vào lệnh.')}
      </p>
      {trade.signal_time !== null && (
        <p className="micro">
          Nến tín hiệu mở {utcTime(trade.signal_time)}, đóng {utcTime(trade.signal_time + 900)}. Bot
          vào sau khi nến đóng.
        </p>
      )}
      {audit ? (
        <p className="micro">
          Breakout 20 nến trước: đáy {formatPrice(audit.range_low)} — đỉnh{' '}
          {formatPrice(audit.range_high)}. Nến tín hiệu đóng tại {formatPrice(audit.signal_close)}.
          Ngân sách SL lúc vào: {formatPrice(audit.risk_budget)} USDT (cấu hình {audit.risk_percent}
          % equity, có giới hạn lỗ ngày).
        </p>
      ) : (
        trade.origin === 'auto' && (
          <p className="micro">
            Lệnh cũ chưa có bản lưu vùng breakout và ngân sách rủi ro tại thời điểm vào.
          </p>
        )
      )}
      <dl className="paper-trade-facts">
        <div>
          <dt>Entry · đã khớp mô phỏng</dt>
          <dd>{formatPrice(trade.entry)}</dd>
          <small>{utcTime(trade.entry_time)}</small>
        </div>
        <div>
          <dt>SL · mức kích hoạt cắt lỗ</dt>
          <dd className="negative">{formatPrice(trade.stop)}</dd>
          <small>Rủi ro theo mức SL: {formatPrice(risk)} USDT</small>
        </div>
        <div>
          <dt>TP · mức kích hoạt chốt lời</dt>
          <dd className="positive">{formatPrice(trade.target)}</dd>
          <small>Mục tiêu / rủi ro: {risk > 0 ? (reward / risk).toFixed(2) : '—'}R</small>
        </div>
        <div>
          <dt>Exit · giá đóng thực tế</dt>
          <dd>{trade.exit === null ? 'Chưa thoát' : formatPrice(trade.exit)}</dd>
          <small>
            {closed
              ? `${exitLabels[trade.exit_reason ?? ''] ?? trade.exit_reason ?? 'Không rõ lý do'} · ${utcTime(trade.exit_time!)}`
              : 'SL/TP tiếp tục được worker theo dõi'}
          </small>
        </div>
      </dl>
      {closed && (
        <p>
          <strong>
            PnL ròng:{' '}
            <span className={(trade.net_pnl ?? 0) >= 0 ? 'positive' : 'negative'}>
              {trade.net_pnl === null ? '—' : formatPrice(trade.net_pnl)} USDT
            </span>
          </strong>{' '}
          · Phí {formatPrice(trade.entry_fee + trade.exit_fee)} · Funding{' '}
          {formatPrice(trade.funding_cost)} USDT
        </p>
      )}
      <p className="micro">
        Entry là giá đã vào, không phải lệnh chờ. Exit là giá đã thoát; chỉ là thoát do SL khi lý do
        ghi Stop-loss. Rủi ro theo mức SL chưa gồm phí, funding và chênh lệch giá khi kích hoạt.{' '}
        {closed && 'Các mức giá trên chart thuộc lệnh lịch sử này, không phải lệnh đang chờ.'}
      </p>
    </div>
  );
}
