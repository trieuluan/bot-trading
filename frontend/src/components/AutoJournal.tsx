import { useState } from 'react';
import { createPortal } from 'react-dom';
import { TriangleAlert } from 'lucide-react';
import type { AutoPaperState } from '../types/paper';
import { formatTime } from '../lib/time-format';

type Entry = NonNullable<AutoPaperState['timeline']>[number];

const LABELS = {
  no_setup: 'Không có tín hiệu',
  opened: 'Đã mở lệnh',
  skipped: 'Bỏ qua',
  missed: 'Lỡ',
  none: 'Lỡ · không có bản ghi (worker không chạy)',
  untracked: 'Chưa theo dõi (trước bản ghi đầu tiên của cặp)',
};
const UNRECORDED = 'Không có bản ghi (worker không chạy)';

const time = (t: number) => formatTime(t, false, false);

export function AutoJournal({
  state,
  onViewTrade,
}: {
  state: AutoPaperState | null;
  onViewTrade?: (id: number) => void;
}) {
  const [tip, setTip] = useState<{
    text: string;
    top: number;
    left: number;
    below: boolean;
    visible: boolean;
  }>({ text: '', top: 0, left: 0, below: false, visible: false });
  const coverage = state?.coverage;
  if (!coverage) return null;
  const timeline = state.timeline ?? [];
  const byTime = new Map(timeline.map((entry) => [entry.candle_time, entry]));
  const cells = Array.from({ length: coverage.window_candles }, (_, index) => {
    const candle = coverage.end_time - (coverage.window_candles - 1 - index) * 900;
    return {
      candle,
      entry: byTime.get(candle) as Entry | undefined,
      tracked: candle >= coverage.start_time,
    };
  });
  const showTip = (
    event: React.MouseEvent<HTMLElement> | React.FocusEvent<HTMLElement>,
    text: string,
  ) => {
    const rect = event.currentTarget.getBoundingClientRect();
    const half = Math.min(280, window.innerWidth - 20) / 2 + 10;
    const left = Math.max(half, Math.min(rect.left + rect.width / 2, window.innerWidth - half));
    const below = rect.top < 72;
    setTip({ text, top: below ? rect.bottom + 8 : rect.top - 8, left, below, visible: true });
  };
  const hideTip = () => setTip((current) => ({ ...current, visible: false }));
  const events = timeline.filter(
    (entry) => entry.action === 'opened' || entry.action === 'skipped',
  );
  const missedTotal = coverage.missed + coverage.unrecorded;
  const reasons = new Map<string, number>();
  for (const entry of timeline)
    if (entry.action === 'missed') reasons.set(entry.reason, (reasons.get(entry.reason) ?? 0) + 1);
  if (coverage.unrecorded) reasons.set(UNRECORDED, coverage.unrecorded);
  return (
    <section className="ap-journal" aria-label="Nhật ký Auto Paper 24 giờ">
      <header>
        <strong>Nhật ký · 24 giờ qua</strong>
        <span className={missedTotal ? 'ap-journal-missed' : undefined}>
          {missedTotal > 0 && <TriangleAlert size={12} aria-hidden="true" />}
          Đã xét {coverage.evaluated}/{coverage.tracked_candles} nến
          {missedTotal > 0 && ` · lỡ ${missedTotal}`}
        </span>
      </header>
      <div
        className="ap-strip"
        role="group"
        onMouseLeave={hideTip}
        onBlur={(event) => {
          if (!event.currentTarget.contains(event.relatedTarget)) hideTip();
        }}
        onKeyDown={(event) => {
          if (event.key === 'Escape') hideTip();
        }}
        aria-label={`${coverage.evaluated}/${coverage.tracked_candles} nến đã xét, ${coverage.opened} mở lệnh, ${coverage.skipped} bỏ qua, ${missedTotal} lỡ trong ${coverage.window_candles} nến gần nhất`}
      >
        {cells.map(({ candle, entry, tracked }) => {
          const action = entry?.action ?? (tracked ? 'none' : 'untracked');
          const detail =
            !entry || action === 'no_setup'
              ? ''
              : action === 'opened'
                ? ` · #${entry.trade_id} ${entry.side?.toUpperCase() ?? ''}`
                : ` · ${entry.reason}`;
          const title = `${time(candle)} · ${LABELS[action]}${detail}`;
          return entry?.trade_id && onViewTrade ? (
            <button
              key={candle}
              type="button"
              className={`ap-cell ${action}`}
              aria-label={title}
              onMouseEnter={(e) => showTip(e, title)}
              onFocus={(e) => showTip(e, title)}
              onClick={() => onViewTrade(entry.trade_id!)}
            />
          ) : (
            <span
              key={candle}
              className={`ap-cell ${action}`}
              onMouseEnter={(e) => showTip(e, title)}
            />
          );
        })}
      </div>
      {typeof document !== 'undefined' &&
        createPortal(
          <div
            className={`ap-portal-tooltip${tip.below ? ' below' : ''}${tip.visible ? ' is-visible' : ''}`}
            style={{ top: `${tip.top}px`, left: `${tip.left}px` }}
            role="tooltip"
            aria-hidden={!tip.visible}
          >
            {tip.text}
          </div>,
          document.body,
        )}
      <div className="ap-strip-axis" aria-hidden="true">
        <span>{time(cells[0].candle)}</span>
        <span>{time(coverage.end_time)}</span>
      </div>
      <div className="ap-legend" aria-hidden="true">
        <span>
          <i className="ap-cell no_setup" /> không tín hiệu
        </span>
        <span>
          <i className="ap-cell opened" /> mở lệnh
        </span>
        <span>
          <i className="ap-cell skipped" /> bỏ qua
        </span>
        <span>
          <i className="ap-cell missed" /> lỡ có lý do
        </span>
        <span>
          <i className="ap-cell none" /> không bản ghi
        </span>
        {coverage.tracked_candles < coverage.window_candles && (
          <span>
            <i className="ap-cell untracked" /> chưa theo dõi
          </span>
        )}
      </div>
      {reasons.size > 0 && (
        <p className="ap-journal-reasons">
          Lỡ:{' '}
          {[...reasons.entries()]
            .sort((a, b) => b[1] - a[1])
            .map(([reason, count]) => `${reason} (${count})`)
            .join(' · ')}
        </p>
      )}
      {events.length > 0 && (
        <ol>
          {events.map((row) => (
            <li key={row.candle_time} className={row.action}>
              <time>{time(row.candle_time)}</time>
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
      {coverage.no_setup > 0 && <p>{coverage.no_setup} nến không có tín hiệu đủ điều kiện.</p>}
      {coverage.tracked_candles === 0 && <p>Cặp này chưa có nến nào được ghi nhận.</p>}
    </section>
  );
}
