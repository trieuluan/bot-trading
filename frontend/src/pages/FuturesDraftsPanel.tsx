import { useState } from 'react';
import { formatTime } from '../lib/time-format';
import { exitLabel, strategyShortTitle, type Genome } from '../lib/futures-strategy-info';

export type Draft = {
  id: string;
  strategy: string;
  genome: Genome;
  generation: number;
  parents: string[];
  status:
    'queued' | 'running' | 'insufficient' | 'waiting_slot' | 'rejected' | 'error' | 'admitted';
  phase: string;
  completed: number | null;
  total: number | null;
  attempts: number;
  created_at: number;
  updated_at: number;
  next_retry_at: number | null;
  reason: string | null;
  admitted_at: number | null;
  admitted_strategy: string | null;
  replay: {
    checks: Record<string, boolean>;
    total_samples: number;
    validation_samples: number;
    validation_mean_r: number | null;
    validation_drawdown_r: number;
  } | null;
};
export type DraftWorker = { stale?: boolean; consumer_enabled?: boolean; phase?: string };

const FILTERS: [string, string, (draft: Draft) => boolean][] = [
  ['all', 'Tất cả', (d) => d.status !== 'admitted'],
  ['queued', 'Chờ replay', (d) => d.status === 'queued'],
  ['running', 'Đang chạy', (d) => d.status === 'running'],
  ['insufficient', 'Thiếu mẫu', (d) => d.status === 'insufficient'],
  ['waiting_slot', 'Chờ slot', (d) => d.status === 'waiting_slot'],
  ['rejected', 'Không đạt', (d) => d.status === 'rejected'],
  ['error', 'Lỗi', (d) => d.status === 'error'],
  ['admitted', 'Đã vào forward', (d) => d.status === 'admitted'],
];
const PHASES: Record<string, string> = {
  queued: 'Chờ worker',
  data: 'Chuẩn bị dữ liệu',
  candles: 'Tải nến',
  fair_price: 'Tải fair price',
  funding: 'Tải funding',
  open_interest: 'Kiểm tra OI',
  replay: 'Replay 90 ngày',
  done: 'Xong',
};
const CHECKS: Record<string, string> = {
  total_samples_gte_30: '≥30 mẫu tổng',
  validation_samples_gte_10: '≥10 mẫu kiểm tra',
  validation_mean_gt_0: 'R kiểm tra > 0',
  validation_dd_lt_10r: 'DD kiểm tra < 10R',
};
const STATUS: Record<string, string> = {
  queued: 'Chờ replay',
  running: 'Đang chạy',
  insufficient: 'Chưa đủ bằng chứng',
  waiting_slot: 'Đạt · chờ slot',
  rejected: 'Không đạt',
  error: 'Lỗi',
  admitted: 'Đã vào forward',
};

function phaseText(phase: string) {
  return phase.startsWith('benchmark:')
    ? `Tải benchmark ${phase.slice(10).replace('_USDT', '')}`
    : (PHASES[phase] ?? phase);
}

export function FuturesDraftsPanel({
  drafts,
  worker,
  now,
  onViewTree,
}: {
  drafts: Draft[];
  worker?: DraftWorker;
  now: number;
  onViewTree: (strategy: string) => void;
}) {
  const [filter, setFilter] = useState('all');
  const match = FILTERS.find(([key]) => key === filter)![2];
  const rows = [...drafts].filter(match).sort((a, b) => a.created_at - b.created_at);
  const stalled = (d: Draft) => d.status === 'running' && now - d.updated_at > 90;
  return (
    <>
      <p className="evo-note rounded border border-border-subtle bg-surface-2 p-3 text-xs leading-relaxed text-text-secondary">
        Con mới: Con nháp → Replay 90 ngày (70/30) → Đạt/chờ slot → Forward. Replay chỉ sàng lọc,
        không cộng vào mẫu forward. Thiếu mẫu tự thử lại sau 7 ngày; không đạt thì dừng.
      </p>
      {worker?.consumer_enabled === false && !worker.stale && (
        <p className="evo-warn rounded border border-amber-400/30 bg-amber-400/5 p-3 text-xs text-amber-200" role="status">
          Worker replay đang tắt; con nháp chờ cho đến khi bật.
        </p>
      )}
      {worker?.stale !== false && (
        <p className="evo-warn rounded border border-amber-400/30 bg-amber-400/5 p-3 text-xs text-amber-200" role="status">
          Worker replay chậm hoặc mất kết nối; con nháp sẽ chờ.
        </p>
      )}
      {rows.some(stalled) && (
        <p className="evo-warn rounded border border-amber-400/30 bg-amber-400/5 p-3 text-xs text-amber-200" role="status">
          Worker chậm: có job không cập nhật quá 90 giây.
        </p>
      )}
      <div className="evo-filters flex min-w-0 flex-wrap items-center gap-2 text-xs" role="group" aria-label="Lọc con nháp">
        {FILTERS.map(([key, label, test]) => (
          <button key={key} aria-pressed={filter === key} onClick={() => setFilter(key)}>
            {label} <span className="evo-count ml-auto text-xs text-text-muted">{drafts.filter(test).length}</span>
          </button>
        ))}
      </div>
      {rows.length === 0 && <p className="evo-empty text-center text-text-muted">Không có con nháp trong bộ lọc này.</p>}
      <div className="evo-grid grid min-w-0 gap-3 md:grid-cols-2 2xl:grid-cols-3">
        {rows.map((draft) => {
          const pct =
            draft.total && draft.completed !== null
              ? Math.min(100, Math.floor((100 * draft.completed) / draft.total))
              : null;
          const result = draft.replay;
          return (
            <article key={draft.id} className={`evo-card draft-${draft.status} min-w-0 rounded-lg border border-border-base bg-surface-1 space-y-3 p-3 [&.selected]:border-bull [&.holding]:border-bull/50 [&.signaling]:border-amber-300/50`}>
              <div className="evo-card-top flex min-w-0 flex-wrap items-center justify-between gap-2 text-xs">
                <div>
                  <strong title={draft.strategy}>
                    {strategyShortTitle(draft.genome, draft.strategy)}
                  </strong>
                  <code>{draft.strategy.split(':')[1]}</code>
                </div>
                <span className="pill inline-flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 text-xs font-medium bg-surface-3 text-text-secondary">G{draft.generation}</span>
              </div>
              <p className="evo-pills flex min-w-0 flex-wrap items-center gap-2 text-xs">
                <span className={`pill ${draft.status === 'waiting_slot' ? 'green' : ''} inline-flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 text-xs font-medium bg-surface-3 text-text-secondary`}>
                  {STATUS[draft.status]}
                </span>
                <span className="pill inline-flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 text-xs font-medium bg-surface-3 text-text-secondary">{draft.genome.entry_policy ?? 'market'}</span>
                <span className="pill blue inline-flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 text-xs font-medium bg-surface-3 text-text-secondary text-sky-300">{exitLabel(draft.genome.exit_policy)}</span>
              </p>
              <p className="evo-rule border-y border-border-subtle py-2 text-text-secondary">
                Bố mẹ: {draft.parents.map((p) => p.split(':')[1]).join(' × ')}
              </p>
              {draft.status === 'running' && (
                <div className="draft-progress relative h-1.5 overflow-hidden rounded-full bg-surface-3 [&>i]:block [&>i]:h-full [&>i]:bg-bull" role="progressbar" aria-valuenow={pct ?? undefined}>
                  <span>
                    {phaseText(draft.phase)}
                    {pct === null
                      ? ' · đang chờ số liệu'
                      : ` · ${draft.completed}/${draft.total} nến · ${pct}%`}
                  </span>
                  <i style={{ width: `${pct ?? 0}%` }} data-indeterminate={pct === null} />
                </div>
              )}
              {draft.attempts > 0 && <small>Lần chạy {draft.attempts}/3</small>}
              {result && (
                <ul className="draft-checks">
                  <li>
                    Mẫu {result.total_samples} · kiểm tra {result.validation_samples} · R{' '}
                    {result.validation_mean_r?.toFixed(2) ?? '—'} · DD{' '}
                    {result.validation_drawdown_r.toFixed(2)}R
                  </li>
                  {Object.entries(result.checks).map(([key, ok]) => (
                    <li key={key} className={ok ? "positive text-bull" : "negative text-bear"}>
                      {ok ? '✓' : '✗'} {CHECKS[key] ?? key}
                    </li>
                  ))}
                </ul>
              )}
              {draft.status === 'waiting_slot' && (
                <small>
                  Chờ slot:{' '}
                  {draft.reason === 'no_retirement_candidate'
                    ? 'quần thể đầy, chưa có cây đủ điều kiện loại'
                    : draft.reason === 'ownership_unknown'
                      ? 'chưa xác định chắc quyền sở hữu lệnh đang hoạt động'
                      : 'đang chờ lượt nhận theo thứ tự'}
                </small>
              )}
              {draft.status === 'insufficient' && draft.next_retry_at && (
                <small>Thiếu mẫu · thử lại {formatTime(draft.next_retry_at)}</small>
              )}
              {draft.status === 'queued' && draft.next_retry_at && (
                <small>
                  Thử lại {formatTime(draft.next_retry_at)}
                  {draft.reason ? ` · ${draft.reason}` : ''}
                </small>
              )}
              {draft.status === 'error' && <small className="negative text-bear">{draft.reason}</small>}
              {stalled(draft) && <small className="negative text-bear">Worker chậm hoặc mất kết nối</small>}
              {draft.status === 'admitted' && draft.admitted_strategy && (
                <button type="button" onClick={() => onViewTree(draft.admitted_strategy!)}>
                  Xem cây forward
                </button>
              )}
            </article>
          );
        })}
      </div>
    </>
  );
}
