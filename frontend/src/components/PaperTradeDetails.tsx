import type { PaperTrade } from '../types/paper';
import { formatPrice } from '../lib/format';
import { exitLabel, tradeStrategy } from '../lib/futures-strategy-info';

export const exitLabels: Record<string, string> = {
  manual: 'Đóng thủ công',
  stop_loss: 'Stop-loss (SL)',
  take_profit: 'Take-profit (TP)',
  time_stop: 'Time-stop (lệnh chết)',
  ladder_complete: 'Hoàn tất bậc chốt',
  signal_invalidation: 'Tín hiệu HTF bị phá',
  funding_exit: 'Funding quá cao',
  recovered_stop_loss: 'SL lịch sử (đối soát)',
  recovered_take_profit: 'TP lịch sử (đối soát)',
};
export const utcTime = (time: number) =>
  new Date(time * 1000).toLocaleString('vi-VN', { timeZone: 'UTC' }) + ' UTC';

export function PaperTradeDetails({ trade }: { trade: PaperTrade }) {
  const risk = Math.abs(trade.entry - trade.stop) * trade.quantity;
  const reward = Math.abs(trade.target - trade.entry) * trade.quantity;
  const closed = trade.exit_time !== null;
  const audit = trade.audit;
  const strategy = tradeStrategy(audit);
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
      {trade.monitoring?.exit_policy && (
        <p className="micro">Quản lý lệnh: {exitLabel(trade.monitoring.exit_policy)}</p>
      )}
      {strategy && (
        <p className="micro">
          {strategy.source} · <code>{audit?.strategy}</code>
          {audit?.parents?.length ? ` · Cha mẹ: ${audit.parents.join(', ')}` : ''}
          {audit?.initial_stop != null && audit.initial_target != null
            ? ` · SL/TP ban đầu: ${formatPrice(audit.initial_stop)} / ${formatPrice(audit.initial_target)}`
            : ' · Chưa lưu SL/TP ban đầu; các mức bên dưới là mức cập nhật gần nhất.'}
        </p>
      )}
      {trade.monitoring?.partial_exits?.map((leg) => (
        <p key={leg.time} className="micro">
          Đã chốt {leg.quantity} tại {formatPrice(leg.price)} · {utcTime(leg.time)} · PnL sau phí,
          chưa trừ funding {formatPrice(leg.gross - leg.entry_fee - leg.exit_fee)} USDT. Còn{' '}
          {trade.quantity}; không mở thêm lệnh.
        </p>
      ))}
      <p>
        {trade.monitoring?.status === 'recovered' && (
          <span className="negative">
            {trade.monitoring.execution_model === 'historical_levels_v1'
              ? `Khớp giả lập lịch sử: ${trade.monitoring.fill_basis === 'gap_open' ? 'giá mở nến vượt SL' : 'mức SL/TP'}, kèm trượt giá bất lợi. Thời gian đóng trong nến là ước lượng, không phải tick thật.`
              : 'Bản đối soát cũ: đóng theo bid/ask khi phục hồi, không khớp tại SL/TP lịch sử.'}
            {trade.monitoring.ambiguous ? ' Cùng nến chạm cả hai: ưu tiên SL.' : ''}
          </span>
        )}
        {trade.origin === 'manual'
          ? 'Bạn mở lệnh Market thủ công; lệnh này không có tín hiệu của bot.'
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
          Chiến thuật: {strategy?.title} {strategy?.generation && `(${strategy.generation})`}.{' '}
          {strategy?.detail} Nến tín hiệu đóng tại {formatPrice(audit.signal_close)}. Ngân sách SL
          lúc vào: {formatPrice(audit.risk_budget)} USDT (cấu hình {audit.risk_percent}% equity, có
          giới hạn lỗ ngày).
        </p>
      ) : (
        trade.origin === 'auto' && (
          <p className="micro">
            Lệnh cũ chưa có bản lưu chiến thuật và ngân sách rủi ro tại thời điểm vào.
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
          · Phí{' '}
          {formatPrice(
            trade.entry_fee +
              trade.exit_fee +
              (trade.monitoring?.partial_exits ?? []).reduce(
                (sum, leg) => sum + leg.entry_fee + leg.exit_fee,
                0,
              ),
          )}{' '}
          · Funding {formatPrice(trade.funding_cost)} USDT
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
