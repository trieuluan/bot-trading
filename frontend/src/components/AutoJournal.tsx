import { useState } from 'react';
import { createPortal } from 'react-dom';
import { TriangleAlert } from 'lucide-react';
import type { AutoPaperState } from '../types/paper';
import { formatTime } from '../lib/time-format';

type Entry = NonNullable<AutoPaperState['timeline']>[number];

const LABELS = {
  no_setup: 'Không có tín hiệu',
  opened: 'Đã mở lệnh',
  pending: 'Đặt lệnh chờ',
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
    <section className="ap-journal min-w-0 space-y-2 py-2" aria-label="Nhật ký Auto Paper 24 giờ">
      <header className="flex min-w-0 flex-wrap items-center justify-between gap-x-3 gap-y-1 text-xs">
        <strong className="text-text-primary">Nhật ký · 24 giờ qua</strong>
        <span className={`inline-flex flex-wrap items-center gap-1 ${missedTotal ? 'ap-journal-missed text-amber-300' : 'text-text-muted'}`}>
          {missedTotal > 0 && <TriangleAlert size={12} aria-hidden="true" />}
          Đã xét {coverage.evaluated}/{coverage.tracked_candles} nến
          {missedTotal > 0 && ` · lỡ ${missedTotal}`}
        </span>
      </header>
      <div
        className="ap-strip grid w-full min-w-0 gap-px"
        style={{ gridTemplateColumns: `repeat(${cells.length}, minmax(0, 1fr))` }}
        role="group"
        onMouseLeave={hideTip}
        onBlur={(event) => {
          if (!event.currentTarget.contains(event.relatedTarget)) hideTip();
        }}
        onKeyDown={(event) => {
          if (event.key === 'Escape') hideTip();
        }}
        aria-label={`${coverage.evaluated}/${coverage.tracked_candles} nến đã xét, ${coverage.opened} mở lệnh, ${coverage.pending ?? 0} lệnh chờ, ${coverage.skipped} bỏ qua, ${missedTotal} lỡ trong ${coverage.window_candles} nến gần nhất`}
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
              className={`ap-cell ${action} block h-6 min-w-0 rounded-sm border-0 bg-surface-3 p-0 focus-visible:z-10 focus-visible:outline-2 focus-visible:outline-sky-400 [&.opened]:bg-bull [&.pending]:bg-amber-400 [&.skipped]:bg-slate-600 [&.missed]:bg-bear [&.untracked]:bg-transparent [&.untracked]:ring-1 [&.untracked]:ring-inset [&.untracked]:ring-border-base`}
              aria-label={title}
              onMouseEnter={(e) => showTip(e, title)}
              onFocus={(e) => showTip(e, title)}
              onClick={() => onViewTrade(entry.trade_id!)}
            />
          ) : (
            <span
              key={candle}
              className={`ap-cell ${action} block h-6 min-w-0 rounded-sm bg-surface-3 focus-visible:z-10 focus-visible:outline-2 focus-visible:outline-sky-400 [&.opened]:bg-bull [&.pending]:bg-amber-400 [&.skipped]:bg-slate-600 [&.missed]:bg-bear [&.untracked]:bg-transparent [&.untracked]:ring-1 [&.untracked]:ring-inset [&.untracked]:ring-border-base`}
              tabIndex={0}
              role="img"
              aria-label={title}
              onMouseEnter={(e) => showTip(e, title)}
              onFocus={(e) => showTip(e, title)}
            />
          );
        })}
      </div>
      {typeof document !== 'undefined' && tip.visible &&
        createPortal(
          <div
            className={`ap-portal-tooltip pointer-events-none fixed z-[80] w-[min(280px,calc(100vw-20px))] -translate-x-1/2 ${tip.below ? 'translate-y-0' : '-translate-y-full'} max-h-[70dvh] overflow-hidden whitespace-normal break-words rounded-lg border border-border-active bg-surface-2 p-3 text-xs leading-relaxed text-text-secondary shadow-lg`}
            style={{ top: `${tip.top}px`, left: `${tip.left}px` }}
            role="tooltip"
            aria-hidden={!tip.visible}
          >
            {tip.text}
          </div>,
          document.body,
        )}
      <div className="ap-strip-axis flex justify-between text-xs text-text-muted" aria-hidden="true">
        <span>{time(cells[0].candle)}</span>
        <span>{time(coverage.end_time)}</span>
      </div>
      {!!coverage.pending && <p>{coverage.pending} tín hiệu đang có lệnh chờ.</p>}
      <div className="ap-legend flex flex-wrap gap-x-3 gap-y-2 text-[11px] text-text-muted [&>span]:inline-flex [&>span]:items-center [&>span]:gap-1.5 [&_i]:inline-block [&_i]:size-2 [&_i]:shrink-0 [&_i]:rounded-sm" aria-hidden="true">
        <span>
          <i className="bg-surface-3" /> không tín hiệu
        </span>
        <span>
          <i className="bg-bull" /> mở lệnh
        </span>
        <span>
          <i className="bg-amber-400" /> lệnh chờ
        </span>
        <span>
          <i className="bg-slate-600" /> bỏ qua
        </span>
        <span>
          <i className="bg-bear" /> lỡ có lý do
        </span>
        <span>
          <i className="bg-surface-3" /> không bản ghi
        </span>
        {coverage.tracked_candles < coverage.window_candles && (
          <span>
            <i className="bg-transparent ring-1 ring-inset ring-border-base" /> chưa theo dõi
          </span>
        )}
      </div>
      {reasons.size > 0 && (
        <p className="ap-journal-reasons flex flex-wrap items-center gap-2 text-xs text-text-muted">
          Lỡ:{' '}
          {[...reasons.entries()]
            .sort((a, b) => b[1] - a[1])
            .map(([reason, count]) => `${reason} (${count})`)
            .join(' · ')}
        </p>
      )}
      {events.length > 0 && (
        <ol className="max-h-64 space-y-2 overflow-y-auto border-t border-border-subtle pt-2">
          {events.map((row) => (
            <li key={row.candle_time} className={`${row.action} flex min-w-0 flex-wrap items-baseline gap-x-3 gap-y-1 text-xs`}>
              <time className="shrink-0 whitespace-nowrap font-mono tabular-nums text-text-muted">{time(row.candle_time)}</time>
              {row.action === 'opened' ? (
                <span className="min-w-0 break-words">
                  Đã mở{' '}
                  {row.trade_id && onViewTrade ? (
                    <button
                      type="button"
                      className="text-button inline-flex items-center gap-1 text-xs text-bull underline-offset-4 hover:underline"
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
                <span className="min-w-0 break-words" title={row.reason}>
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
