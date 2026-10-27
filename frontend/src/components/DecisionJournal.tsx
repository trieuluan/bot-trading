import { useMemo, useState } from 'react';
import type { ExnessExecution } from '../types/exness-execution';
import { feedErrorText, type Tone } from '../lib/forex-labels';
import { relativeTime, formatTime } from '../lib/time-format';
import { timeZoneLabel } from '../lib/time-zone';
import { ChevronDown, ListFilter } from 'lucide-react';

type Journal = NonNullable<ExnessExecution['journal']>;
type Funnel = NonNullable<ExnessExecution['funnel']>;

const outcomeLabels: Record<string, string> = {
  no_signal: 'Không có tín hiệu',
  shadow_only: 'Chỉ có gen shadow',
  not_selected: 'Có tín hiệu, bot không chọn',
  signal: 'Có tín hiệu chọn',
  order_sent: 'Đã gửi lệnh',
  order_planned: 'Đã lập lệnh',
  execution_error: 'Lỗi khi gửi lệnh sàn',
  api_rate_limited: 'Exness giới hạn tần suất',
  worker_error: 'Lỗi vòng xử lý bot',
  old_or_duplicate_signal: 'Tín hiệu cũ/trùng',
  position_capacity_for_split: 'Không đủ chỗ chia lệnh',
  reconciling: 'Đang đối soát lệnh',
  position_active: 'Đang có vị thế',
  session_close_near: 'Gần đóng phiên',
  history_catching_up: 'Đang đồng bộ lịch sử',
  paused: 'Bot tạm dừng',
  feed_not_ready: 'Dữ liệu nến chưa sẵn sàng',
  outside_signal_window: 'Ngoài cửa sổ đánh giá M15 (5 phút)',
  ask_candle_missing: 'Thiếu nến ask',
  policy_missing: 'Chưa có policy học',
  preflight_failed: 'Kiểm tra trước lệnh thất bại',
};

const outcomeKey = (outcome: string) => outcome.replace(/^(blocked|unavailable):/, '');
export const outcomeText = (outcome: string) =>
  outcomeLabels[outcomeKey(outcome)] ?? outcomeKey(outcome);

export function outcomeTone(outcome: string): Tone {
  const key = outcomeKey(outcome);
  if (['order_sent', 'order_planned', 'signal'].includes(key)) return 'ok';
  if (['worker_error', 'execution_error', 'api_rate_limited', 'preflight_failed'].includes(key))
    return 'bad';
  if (key === 'no_signal') return 'idle';
  return 'warn';
}

/** Lý do chi tiết từ worker: dịch các mẫu hay gặp, giữ nguyên phần còn lại. */
export function reasonText(reason: string | null | undefined) {
  if (!reason) return null;
  const text = reason.replace(/^ExnessError:\s*/, '');
  if (/rate limit/i.test(text)) return feedErrorText(text);
  if (text === 'poor_rr') return 'Tỷ lệ lợi nhuận / rủi ro (R/R) chưa đạt ngưỡng của chiến thuật.';
  const poorRr = text.match(/^skipped_poor_rr:\s*(.*)$/);
  if (poorRr) return `R/R kém · ${poorRr[1].replace('structural TP below', 'TP cấu trúc dưới')}`;
  return text;
}

interface Group {
  key: string;
  from: number;
  to: number;
  count: number;
  rows: Journal;
}

/** Gộp các nến liên tiếp cùng kết quả mà không có tín hiệu (vd. 12 nến "không có tín hiệu"). */
export function groupRows(rows: Journal): Group[] {
  const groups: Group[] = [];
  for (const row of [...rows].sort((left, right) => right.time - left.time)) {
    const quiet = !row.confirmed.length && !row.selected;
    const signature = quiet ? `${row.outcome}|${row.reason ?? ''}` : null;
    const last = groups.at(-1);
    if (signature && last && last.key === signature && last.from - row.time === 900) {
      last.from = row.time;
      last.count += 1;
      last.rows.push(row);
    } else {
      groups.push({
        key: signature ?? `row-${row.time}`,
        from: row.time,
        to: row.time,
        count: 1,
        rows: [row],
      });
    }
  }
  return groups;
}

const sideText = (side?: string) =>
  side === 'long' || side === 'buy' ? 'BUY' : side === 'short' || side === 'sell' ? 'SELL' : null;

function Signals({ row }: { row: Journal[number] }) {
  if (!row.confirmed.length && !row.selected) return <span className="muted text-text-muted">—</span>;
  const ordered = [...row.confirmed].sort(
    (left, right) =>
      Number(right.strategy === row.selected) - Number(left.strategy === row.selected) ||
      Number(!!left.shadow) - Number(!!right.shadow),
  );
  const shown = ordered.slice(0, 2);
  const rest = ordered.length - shown.length;
  const side = sideText(ordered[0]?.side);
  return (
    <div
      className="journal-signals flex min-w-0 flex-wrap items-center gap-1.5"
      title={ordered.map((c) => `${c.strategy}${c.shadow ? ' (shadow)' : ''}`).join('\n')}
    >
      {side && <span className={`side-badge side-${side.toLowerCase()} inline-flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 text-xs font-medium bg-surface-3 text-text-secondary`}>{side}</span>}
      {row.selected && !ordered.some((c) => c.strategy === row.selected) && (
        <span className="journal-chip is-picked inline-flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 text-xs font-medium bg-surface-3 text-text-secondary bg-bull/10 text-bull">{row.selected}</span>
      )}
      {shown.map((c) => (
        <span
          key={c.strategy}
          className={`journal-chip inline-flex max-w-full items-center gap-1 rounded-md border px-2 py-1 text-[11px] ${c.strategy === row.selected ? 'border-bull/25 bg-bull/10 text-bull' : 'border-border-subtle bg-surface-2 text-text-secondary'}`}
        >
          <span className="truncate" title={c.strategy}>{c.strategy}</span>
          {c.strategy === row.selected && <span className="shrink-0">· Bot chọn</span>}
          {c.shadow && <span className="shrink-0 text-text-muted">· Mô phỏng</span>}
        </span>
      ))}
      {rest > 0 && <span className="journal-more text-xs text-text-muted">+{rest}</span>}
    </div>
  );
}

const PAGE = 12;

/** "21/10 11:45 → 14:30" (cùng ngày) hoặc "20/10 23:45 → 21/10 01:00". */
function rangeText(from: number, to: number) {
  const [fromDate, fromTime] = formatTime(from, false, false).split(' ');
  const [toDate, toTime] = formatTime(to, false, false).split(' ');
  return fromDate === toDate
    ? `${fromDate} ${fromTime} → ${toTime}`
    : `${fromDate} ${fromTime} → ${toDate} ${toTime}`;
}

export function DecisionJournal({
  journal,
  funnel,
}: {
  journal: Journal | undefined;
  funnel: Funnel | undefined;
}) {
  const [filter, setFilter] = useState<string | null>(null);
  const [limit, setLimit] = useState(PAGE);
  const rows = useMemo(
    () => (journal ?? []).filter((row) => !filter || row.outcome === filter),
    [journal, filter],
  );
  const groups = useMemo(() => groupRows(rows), [rows]);
  const reasons = Object.entries((journal ?? []).reduce<Record<string, number>>((counts, row) => {
    counts[row.outcome] = (counts[row.outcome] ?? 0) + 1;
    return counts;
  }, {})).sort((a, b) => b[1] - a[1]);
  const total = funnel?.candles ?? journal?.length ?? 0;

  const steps: [string, number, number | null][] = funnel
    ? [
        ['Đã đánh giá', funnel.evaluated, null],
        ['Có tín hiệu', funnel.with_signal, funnel.evaluated],
        ['Bot chọn', funnel.selected, funnel.with_signal],
        ['Đã gửi lệnh', funnel.orders, funnel.selected],
      ]
    : [];

  return (
    <div className="journal min-w-0 space-y-4">
      {funnel && total > 0 && (
        <ol className="journal-funnel grid grid-cols-2 gap-2 lg:grid-cols-4" aria-label="Phễu quyết định">
          {steps.map(([name, value, base], index) => (
            <li key={name} className="min-w-0 rounded-xl border border-border-subtle bg-surface-2/50 px-3 py-2.5">
              <span className="flex items-center gap-1.5 text-xs text-text-muted"><span className="font-mono text-[10px] opacity-60">0{index + 1}</span>{name}</span>
              <div className="mt-1 flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
              <strong className={`font-mono text-xl font-semibold tabular-nums ${index === 3 && value > 0 ? 'text-bull' : 'text-text-primary'}`}>{value.toLocaleString('vi-VN')}</strong>
              <small className="text-[11px] text-text-muted">
                {base == null
                  ? `/${total.toLocaleString('vi-VN')} nến`
                  : base
                    ? `${Math.round((value / base) * 100)}% bước trước`
                    : '—'}
              </small>
              </div>
              <div className="mt-2 h-1 overflow-hidden rounded-full bg-surface-3"><span
                aria-hidden="true"
                className={`block h-full rounded-full ${index === 3 ? 'bg-bull' : 'bg-sky-400/70'}`}
                style={{
                  width: `${funnel.evaluated ? Math.min(100, (value / funnel.evaluated) * 100) : 0}%`,
                }}
              /></div>
            </li>
          ))}
        </ol>
      )}

      {reasons.length > 0 && (
        <div className="journal-reasons flex flex-wrap items-center gap-2 text-xs" role="group" aria-label="Lọc theo kết quả">
          <span className="mr-1 inline-flex items-center gap-1.5 text-text-muted"><ListFilter size={14} aria-hidden="true" />Kết quả</span>
          <button
            type="button"
            className="journal-reason inline-flex min-h-8 items-center gap-2 rounded-lg border border-border-base px-2.5 py-1.5 text-text-muted transition-colors hover:bg-surface-2 focus-visible:outline-2 focus-visible:outline-sky-400 aria-pressed:border-sky-400/40 aria-pressed:bg-sky-400/10 aria-pressed:text-sky-200"
            aria-pressed={filter === null}
            onClick={() => {
              setFilter(null);
              setLimit(PAGE);
            }}
          >
            Tất cả <b className="rounded bg-surface-3 px-1.5 font-mono text-[10px] tabular-nums">{journal?.length ?? 0}</b>
          </button>
          {reasons.map(([outcome, count]) => (
            <button
              type="button"
              key={outcome}
              className={`journal-reason inline-flex min-h-8 max-w-full items-center gap-2 rounded-lg border border-border-base px-2.5 py-1.5 text-left transition-colors hover:bg-surface-2 focus-visible:outline-2 focus-visible:outline-sky-400 aria-pressed:border-sky-400/40 aria-pressed:bg-sky-400/10 ${outcomeTone(outcome) === 'bad' ? 'text-rose-300' : outcomeTone(outcome) === 'ok' ? 'text-bull' : 'text-text-secondary'}`}
              aria-pressed={filter === outcome}
              onClick={() => {
                setFilter(filter === outcome ? null : outcome);
                setLimit(PAGE);
              }}
            >
              {outcomeText(outcome)} <b className="shrink-0 rounded bg-surface-3 px-1.5 font-mono text-[10px] tabular-nums">{count}</b>
            </button>
          ))}
        </div>
      )}

      <div className="space-y-2" aria-label="Nhật ký các nến đã qua">
      {groups.length ? (
        <>
          <ul className="space-y-2">
            {groups.slice(0, limit).map((group) => {
              const row = group.rows[0];
              const reason = reasonText(row.reason);
              const tone = outcomeTone(row.outcome);
              const toneBadge = tone === 'ok'
                ? 'border-bull/25 bg-bull/10 text-bull'
                : tone === 'bad'
                  ? 'border-rose-400/25 bg-rose-400/10 text-rose-300'
                  : tone === 'warn'
                    ? 'border-amber-400/25 bg-amber-400/10 text-amber-200'
                    : 'border-border-subtle bg-surface-2 text-text-muted';
              return (
                <li
                  key={group.key + group.to}
                  className={`flex flex-col gap-3 rounded-xl border p-3.5 transition-colors sm:flex-row sm:items-start sm:justify-between ${tone === 'bad' ? 'border-rose-400/20 bg-rose-950/10' : tone === 'ok' ? 'border-bull/20 bg-bull/5' : 'border-border-subtle bg-surface-1'}`}
                >
                  <div className="min-w-0 space-y-1.5 sm:max-w-xs">
                    <div className="flex flex-wrap items-center gap-2 font-mono text-xs text-text-primary">
                      <span>{group.count > 1 ? rangeText(group.from + 900, group.to + 900) : formatTime(row.time + 900, false, false)}</span>
                      {group.count > 1 ? (
                        <span className="rounded bg-surface-3 px-1.5 py-0.5 text-[11px] font-sans text-text-muted">{group.count} nến liên tiếp</span>
                      ) : (
                        <span className="text-[11px] font-sans text-text-muted">({relativeTime(row.time + 900)})</span>
                      )}
                    </div>
                    <span className="block text-[11px] text-text-muted">Đóng nến M15 · {timeZoneLabel()}</span>
                    {group.count > 1 && (
                      <details className="group text-[11px] text-text-muted">
                        <summary className="inline-flex cursor-pointer items-center gap-1 rounded py-1 focus-visible:outline-2 focus-visible:outline-sky-400">Xem {group.count} mốc nến<ChevronDown size={12} className="group-open:rotate-180" aria-hidden="true" /></summary>
                        <ul className="mt-1 space-y-1 border-l border-border-base pl-2 font-mono">{group.rows.map((item) => <li key={item.time}>{formatTime(item.time + 900, false, false)}</li>)}</ul>
                      </details>
                    )}
                  </div>

                  <div className="min-w-0 flex-1 space-y-2 sm:px-4">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className={`inline-flex items-center gap-1.5 rounded-md border px-2 py-0.5 text-xs font-medium ${toneBadge}`}>
                        <span aria-hidden="true" className={`size-1.5 rounded-full ${tone === 'ok' ? 'bg-bull' : tone === 'bad' ? 'bg-rose-400' : tone === 'warn' ? 'bg-amber-400' : 'bg-text-muted'}`} />
                        {outcomeText(row.outcome)}
                      </span>
                    </div>
                    {reason && (
                      <p className="m-0 break-words text-xs leading-relaxed text-text-secondary">{reason}</p>
                    )}
                    {(row.reason || !!row.events?.length) && (
                      <details className="group min-w-0 text-[11px] text-text-muted">
                        <summary className="inline-flex cursor-pointer items-center gap-1 rounded py-1 focus-visible:outline-2 focus-visible:outline-sky-400">Chi tiết xử lý{group.count === 1 && !!row.events?.length && ` · ${row.events.length} mốc ghi nhận`}<ChevronDown size={12} className="group-open:rotate-180" aria-hidden="true" /></summary>
                        <div className="mt-1 space-y-2 rounded-lg border border-border-subtle bg-canvas/50 p-2.5">
                          <p>Mã kết quả: <code className="break-all text-text-secondary">{row.outcome}</code></p>
                          {(outcomeKey(row.outcome) === 'outside_signal_window' || row.events?.some((event) => outcomeKey(event.outcome) === 'outside_signal_window')) && <p className="leading-relaxed">Luồng đánh giá tín hiệu M15 chỉ nhận tín hiệu trong 5 phút sau nến đóng. Nhánh Limit/Stop ảo dùng thời hạn riêng trong chi tiết setup; đây không phải lỗi từ chối lệnh của sàn.</p>}
                          {!!row.events?.length && <p className="leading-relaxed">Các mốc bên dưới là diễn biến được lưu, không phải tổng số lần kiểm tra. Kết quả trên thẻ được giữ lại khi vòng sau không đánh giá được tín hiệu.</p>}
                          {row.reason && <p>Mã / lý do gốc: <code className="break-all text-text-secondary">{row.reason}</code></p>}
                          {group.count === 1 && !!row.events?.length && <ul className="max-h-40 space-y-2 overflow-y-auto">{row.events.map((event, index) => <li key={`${event.at}-${index}`} className="flex flex-wrap gap-x-2 gap-y-1"><span className="font-mono">{formatTime(event.at, true)}</span><span>{outcomeText(event.outcome)}</span></li>)}</ul>}
                        </div>
                      </details>
                    )}
                  </div>

                  <div className="min-w-0 lg:w-64 lg:shrink-0">
                    <Signals row={row} />
                  </div>
                </li>
              );
            })}
          </ul>
          {groups.length > limit && (
            <button
              type="button"
              className="secondary journal-more-button inline-flex items-center justify-center gap-1.5 rounded-md border border-border-active bg-surface-2 px-3 py-1.5 text-xs text-text-secondary hover:bg-surface-3 mx-auto my-3 flex"
              onClick={() => setLimit((value) => value + PAGE)}
            >
              Xem thêm {Math.min(PAGE, groups.length - limit)} dòng · còn{' '}
              {groups.length - limit}
            </button>
          )}
        </>
      ) : (
        <p className="journal-empty grid min-h-24 place-content-center gap-2 p-4 text-center text-xs text-text-muted">
          {filter
            ? 'Không có nến nào khớp bộ lọc.'
            : 'Chưa có nến nào được ghi nhật ký. Bot ghi lại sau mỗi nến M15 đóng.'}
        </p>
      )}
      </div>
    </div>
  );
}
