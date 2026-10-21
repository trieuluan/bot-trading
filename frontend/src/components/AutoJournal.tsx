import { TriangleAlert } from 'lucide-react';
import type { AutoPaperState } from '../types/paper';
import { formatTime } from '../lib/time-format';

export function AutoJournal({
  state,
  onViewTrade,
}: {
  state: AutoPaperState | null;
  onViewTrade?: (id: number) => void;
}) {
  const coverage = state?.coverage;
  if (!coverage) return null;
  const journal = state.journal ?? [];
  return (
    <section className="ap-journal" aria-label="Nhật ký Auto Paper 24 giờ">
      <header>
        <strong>Nhật ký · 24 giờ qua</strong>
        <span
          className={coverage.missed ? 'ap-journal-missed' : undefined}
          title="Nến không được đánh giá: Auto tắt, worker trễ hoặc đang chờ dữ liệu (quote, funding, đối soát)."
        >
          {coverage.missed > 0 && <TriangleAlert size={12} aria-hidden="true" />}
          Đã xét {coverage.evaluated}/{coverage.window_candles} nến
          {coverage.missed > 0 && ` · lỡ ${coverage.missed}`}
        </span>
      </header>
      {journal.length > 0 && (
        <ol>
          {journal.map((row) => (
            <li key={row.candle_time} className={row.action}>
              <time>{formatTime(row.candle_time, false, false)}</time>
              {row.action === 'opened' ? (
                <span>
                  Đã mở{' '}
                  {row.trade_id && onViewTrade ? (
                    <button
                      type="button"
                      className="text-button"
                      onClick={() => onViewTrade(row.trade_id!)}
                    >
                      #{row.trade_id}
                    </button>
                  ) : (
                    row.trade_id && `#${row.trade_id}`
                  )}{' '}
                  {row.side?.toUpperCase()}
                </span>
              ) : (
                <span title={row.reason}>
                  {/^bỏ qua/i.test(row.reason) ? row.reason : `Bỏ qua · ${row.reason}`}
                </span>
              )}
            </li>
          ))}
        </ol>
      )}
      <p>
        {coverage.no_setup > 0
          ? `${coverage.no_setup} nến không có tín hiệu đủ điều kiện.`
          : journal.length
            ? ''
            : 'Chưa có nến nào được xét trong 24 giờ.'}
      </p>
    </section>
  );
}
