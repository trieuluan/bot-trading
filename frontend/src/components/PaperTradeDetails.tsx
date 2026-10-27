import type { PaperTrade } from '../types/paper';
import { formatPrice, formatQuote } from '../lib/format';
import { tradePartialProgress } from '../lib/paper-position';
import { exitLabel, tradeStrategy } from '../lib/futures-strategy-info';
import { formatTime } from '../lib/time-format';

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
export const utcTime = (time: number) => formatTime(time, true);

const signed = (v: number) => `${v > 0 ? '+' : ''}${formatPrice(v)}`;
const tone = (v: number) => (v > 0 ? 'positive' : v < 0 ? 'negative' : undefined);

export function PaperTradeDetails({ trade, symbol }: { trade: PaperTrade; symbol?: string }) {
  const closed = trade.exit_time !== null;
  const audit = trade.audit;
  const strategy = tradeStrategy(audit);
  const unit = symbol ? symbol.replace('_USDT', '') : '';
  const dir = trade.side === 'long' ? 1 : -1;
  // Gross PnL of the size still held if price reaches each level; SL can sit in profit after trailing.
  const atStop = dir * (trade.stop - trade.entry) * trade.quantity;
  const atTarget = dir * (trade.target - trade.entry) * trade.quantity;
  const partial = tradePartialProgress(trade);
  const qty = (v: number) => `${v.toLocaleString('en-US')}${unit ? ` ${unit}` : ''}`;
  return (
    <div className="paper-trade-details">
      <div className="section-head mb-3 flex min-w-0 flex-wrap items-center justify-between gap-2 [&_p]:text-xs [&_p]:text-text-muted">
        <strong>
          #{trade.id} {trade.side.toUpperCase()} · {trade.origin === 'auto' ? 'AUTO' : 'THỦ CÔNG'}
        </strong>
        <span className={`pill ${closed ? 'amber' : ''} inline-flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 text-xs font-medium bg-surface-3 text-text-secondary`}>
          {closed ? 'LỊCH SỬ · ĐÃ ĐÓNG' : 'VỊ THẾ ĐANG MỞ'}
        </span>
      </div>
      <dl className="paper-trade-facts">
        <div>
          <dt>Entry · đã khớp mô phỏng</dt>
          <dd>{formatQuote(trade.entry)}</dd>
          <small>{utcTime(trade.entry_time)}</small>
        </div>
        <div>
          <dt>Exit · giá đóng thực tế</dt>
          <dd>{trade.exit === null ? 'Chưa thoát' : formatQuote(trade.exit)}</dd>
          <small>
            {closed
              ? `${exitLabels[trade.exit_reason ?? ''] ?? trade.exit_reason ?? 'Không rõ lý do'} · ${utcTime(trade.exit_time!)}`
              : 'SL/TP tiếp tục được worker theo dõi'}
          </small>
        </div>
        <div>
          <dt>SL · {atStop >= 0 ? 'mức khoá lời (trailing)' : 'mức kích hoạt cắt lỗ'}</dt>
          <dd className={atStop >= 0 ? "positive text-bull" : "negative text-bear"}>{formatQuote(trade.stop)}</dd>
          <small>
            {atStop >= 0 ? 'Khớp SL vẫn lời' : 'Khớp SL lỗ'}:{' '}
            <b className={tone(atStop)}>{signed(atStop)} USDT</b>
            {atStop >= 0 && <> · SL đã dời qua entry</>}
          </small>
        </div>
        <div>
          <dt>TP · mức kích hoạt chốt lời</dt>
          <dd className="positive text-bull">{formatQuote(trade.target)}</dd>
          <small>
            Khớp TP: <b className={tone(atTarget)}>{signed(atTarget)} USDT</b>
            {atStop < 0 && <> · {(atTarget / -atStop).toFixed(2)}R</>}
          </small>
        </div>
      </dl>
      <p className="paper-trade-note">
        PnL ở SL/TP tính trên {closed ? 'khối lượng lúc đóng' : 'phần còn giữ'} (
        {qty(trade.quantity)}
        ), trước phí, funding và trượt giá.
      </p>
      {partial && (
        <section className="paper-trade-partials" aria-label="Các lần chốt một phần">
          <div className="paper-trade-partials-head">
            <strong>
              Đã đóng {partial.closedPercent}% · {partial.count} lần chốt
            </strong>
            <b className={tone(partial.realizedNet)}>{signed(partial.realizedNet)} USDT</b>
          </div>
          <progress value={partial.closedPercent} max={100} />
          <p className="paper-trade-note">
            Ban đầu {qty(partial.originalQuantity)} · đã đóng {qty(partial.closedQuantity)} ·{' '}
            {closed ? 'phần cuối' : 'còn giữ'} {qty(trade.quantity)} ({partial.remainingPercent}%).
            Không mở thêm lệnh.
          </p>
          <ol>
            {trade.monitoring!.partial_exits!.map((leg, index) => {
              const realized = leg.gross - leg.entry_fee - leg.exit_fee;
              return (
                <li key={`${leg.time}-${index}`}>
                  <span>
                    <strong>Lần {index + 1}</strong> · {utcTime(leg.time)}
                  </span>
                  <span>
                    {qty(leg.quantity)} (
                    {((leg.quantity / partial.originalQuantity) * 100).toLocaleString('vi-VN', {
                      maximumFractionDigits: 1,
                    })}
                    %) tại {formatQuote(leg.price)}
                  </span>
                  <b className={tone(realized)}>{signed(realized)} USDT</b>
                </li>
              );
            })}
          </ol>
          <p className="paper-trade-note">
            PnL mỗi lần đã trừ phí mở phân bổ và phí chốt; chưa trừ funding. Đã cộng vào số dư.
          </p>
        </section>
      )}
      {closed && (
        <p>
          <strong>
            PnL ròng:{' '}
            <span className={(trade.net_pnl ?? 0) >= 0 ? "positive text-bull" : "negative text-bear"}>
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
      <section className="paper-trade-reason">
        <h3>Lý do vào lệnh</h3>
        {trade.monitoring?.status === 'recovered' && (
          <p className="negative text-bear">
            {trade.monitoring.execution_model === 'historical_levels_v1'
              ? `Khớp giả lập lịch sử: ${trade.monitoring.fill_basis === 'gap_open' ? 'giá mở nến vượt SL' : 'mức SL/TP'}, kèm trượt giá bất lợi. Thời gian đóng trong nến là ước lượng, không phải tick thật.`
              : 'Bản đối soát cũ: đóng theo bid/ask khi phục hồi, không khớp tại SL/TP lịch sử.'}
            {trade.monitoring.ambiguous ? ' Cùng nến chạm cả hai: ưu tiên SL.' : ''}
          </p>
        )}
        <p>
          {trade.origin === 'manual'
            ? 'Bạn mở lệnh Market thủ công; lệnh này không có tín hiệu của bot.'
            : (trade.entry_reason ?? 'Lệnh auto cũ chưa lưu lý do vào lệnh.')}
        </p>
        {trade.signal_time !== null && (
          <p className="paper-trade-note">
            Nến tín hiệu mở {utcTime(trade.signal_time)}, đóng {utcTime(trade.signal_time + 900)}.
            Bot vào sau khi nến đóng.
          </p>
        )}
        {audit ? (
          <p className="paper-trade-note">
            Chiến thuật: {strategy?.title} {strategy?.generation && `(${strategy.generation})`}.{' '}
            {strategy?.detail} Nến tín hiệu đóng tại {formatQuote(audit.signal_close)}. Ngân sách SL
            lúc vào: {formatPrice(audit.risk_budget)} USDT (cấu hình {audit.risk_percent}% equity,
            có giới hạn lỗ ngày).
          </p>
        ) : (
          trade.origin === 'auto' && (
            <p className="paper-trade-note">
              Lệnh cũ chưa có bản lưu chiến thuật và ngân sách rủi ro tại thời điểm vào.
            </p>
          )
        )}
        {trade.monitoring?.exit_policy && (
          <p className="paper-trade-note">
            Quản lý lệnh: {exitLabel(trade.monitoring.exit_policy)}
          </p>
        )}
        {strategy && (
          <p className="paper-trade-note">
            {strategy.source} · <code>{audit?.strategy}</code>
            {audit?.parents?.length ? ` · Cha mẹ: ${audit.parents.join(', ')}` : ''}
            {audit?.initial_stop != null && audit.initial_target != null
              ? ` · SL/TP ban đầu: ${formatQuote(audit.initial_stop)} / ${formatQuote(audit.initial_target)}`
              : ' · Chưa lưu SL/TP ban đầu; các mức phía trên là mức cập nhật gần nhất.'}
          </p>
        )}
      </section>
      <p className="paper-trade-note">
        Entry là giá đã vào, không phải lệnh chờ. Exit là giá đã thoát; chỉ là thoát do SL khi lý do
        ghi Stop-loss.{' '}
        {closed && 'Các mức giá trên chart thuộc lệnh lịch sử này, không phải lệnh đang chờ.'}
      </p>
    </div>
  );
}
