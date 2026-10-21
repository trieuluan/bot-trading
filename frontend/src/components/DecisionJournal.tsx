import { useMemo, useState } from 'react';
import type { ExnessExecution } from '../types/exness-execution';
import { feedErrorText, relativeTime, utcTime, type Tone } from '../lib/forex-labels';

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
  outside_signal_window: 'Quá cửa sổ 300s sau nến đóng',
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
function reasonText(reason: string | null | undefined) {
  if (!reason) return null;
  const text = reason.replace(/^ExnessError:\s*/, '');
  if (/rate limit/i.test(text)) return feedErrorText(text);
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
function groupRows(rows: Journal): Group[] {
  const groups: Group[] = [];
  for (const row of rows) {
    const quiet = !row.confirmed.length && !row.selected;
    const signature = quiet ? `${row.outcome}|${row.reason ?? ''}` : null;
    const last = groups.at(-1);
    if (signature && last && last.key === signature) {
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
  if (!row.confirmed.length && !row.selected) return <span className="muted">—</span>;
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
      className="journal-signals"
      title={ordered.map((c) => `${c.strategy}${c.shadow ? ' (shadow)' : ''}`).join('\n')}
    >
      {side && <span className={`side-badge side-${side.toLowerCase()}`}>{side}</span>}
      {row.selected && !ordered.some((c) => c.strategy === row.selected) && (
        <span className="journal-chip is-picked">{row.selected}</span>
      )}
      {shown.map((c) => (
        <span
          key={c.strategy}
          className={`journal-chip ${c.strategy === row.selected ? 'is-picked' : ''} ${c.shadow ? 'is-shadow' : ''}`}
        >
          {c.strategy}
        </span>
      ))}
      {rest > 0 && <span className="journal-more">+{rest}</span>}
    </div>
  );
}

const PAGE = 12;

/** "21/10 11:45 → 14:30" (cùng ngày) hoặc "20/10 23:45 → 21/10 01:00". */
function rangeText(from: number, to: number) {
  const [fromDate, fromTime] = utcTime(from).replace(' UTC', '').split(' ');
  const [toDate, toTime] = utcTime(to).replace(' UTC', '').split(' ');
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
  const reasons = Object.entries(funnel?.outcomes ?? {}).sort((a, b) => b[1] - a[1]);
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
    <div className="journal">
      {funnel && total > 0 && (
        <ol className="journal-funnel" aria-label="Phễu quyết định">
          {steps.map(([name, value, base]) => (
            <li key={name}>
              <strong>{value.toLocaleString('vi-VN')}</strong>
              <span>{name}</span>
              <small>
                {base == null
                  ? `/${total.toLocaleString('vi-VN')} nến`
                  : base
                    ? `${Math.round((value / base) * 100)}% bước trước`
                    : '—'}
              </small>
              <i
                aria-hidden="true"
                style={{
                  width: `${funnel.evaluated ? Math.max(2, (value / funnel.evaluated) * 100) : 0}%`,
                }}
              />
            </li>
          ))}
        </ol>
      )}

      {reasons.length > 0 && (
        <div className="journal-reasons" role="group" aria-label="Lọc theo kết quả">
          <span className="journal-reasons-label">Kết quả theo nến</span>
          <button
            type="button"
            className="journal-reason"
            aria-pressed={filter === null}
            onClick={() => {
              setFilter(null);
              setLimit(PAGE);
            }}
          >
            Tất cả <b>{total}</b>
          </button>
          {reasons.map(([outcome, count]) => (
            <button
              type="button"
              key={outcome}
              className={`journal-reason tone-${outcomeTone(outcome)}`}
              aria-pressed={filter === outcome}
              onClick={() => {
                setFilter(filter === outcome ? null : outcome);
                setLimit(PAGE);
              }}
            >
              {outcomeText(outcome)} <b>{count}</b>
            </button>
          ))}
        </div>
      )}

      {groups.length ? (
        <>
          <div className="table-wrap">
            <table className="learner-clean-table journal-table">
              <thead>
                <tr>
                  <th>Nến đóng · UTC</th>
                  <th>Kết quả</th>
                  <th>Tín hiệu · bot chọn</th>
                </tr>
              </thead>
              <tbody>
                {groups.slice(0, limit).map((group) => {
                  const row = group.rows[0];
                  const reason = reasonText(row.reason);
                  const checks = row.events?.length ?? 0;
                  return (
                    <tr key={group.key + group.to} className={`tone-${outcomeTone(row.outcome)}`}>
                      <td className="journal-time">
                        {group.count > 1 ? (
                          <>
                            {rangeText(group.from + 900, group.to + 900)}
                            <small>{group.count} nến liên tiếp</small>
                          </>
                        ) : (
                          <>
                            {utcTime(row.time + 900).replace(' UTC', '')}
                            <small>
                              {relativeTime(row.time + 900)}
                              {checks > 1 ? ` · ${checks} lần kiểm tra` : ''}
                            </small>
                          </>
                        )}
                      </td>
                      <td>
                        <span className={`dot-label tone-${outcomeTone(row.outcome)}`}>
                          {outcomeText(row.outcome)}
                        </span>
                        {reason && <small className="journal-reason-text">{reason}</small>}
                      </td>
                      <td>
                        <Signals row={row} />
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          {groups.length > limit && (
            <button
              type="button"
              className="secondary journal-more-button"
              onClick={() => setLimit(limit + PAGE * 2)}
            >
              Xem thêm {Math.min(PAGE * 2, groups.length - limit)} dòng · còn{' '}
              {groups.length - limit}
            </button>
          )}
        </>
      ) : (
        <p className="journal-empty">
          {filter
            ? 'Không có nến nào khớp bộ lọc.'
            : 'Chưa có nến nào được ghi nhật ký. Bot ghi lại sau mỗi nến M15 đóng.'}
        </p>
      )}
    </div>
  );
}
