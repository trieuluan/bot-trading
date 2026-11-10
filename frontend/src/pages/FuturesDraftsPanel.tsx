import { useState } from 'react';
import { formatTime } from '../lib/time-format';
import {
  entryPolicyLabel,
  exitLabel,
  strategyShortTitle,
  type Genome,
} from '../lib/futures-strategy-info';

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
  window?: { scope?: string };
  replay: {
    scope?: string;
    symbols?: string[];
    checks: Record<string, boolean>;
    total_samples: number;
    validation_samples: number;
    validation_mean_r: number | null;
    validation_drawdown_r: number;
    per_symbol?: Record<
      string,
      {
        dataset_sha256?: string;
        strategies?: Array<{
          strategy: string;
          train?: { resolved: number; mean_r: number | null; max_drawdown_r: number };
          validation?: { resolved: number; mean_r: number | null; max_drawdown_r: number };
        }>;
      }
    >;
  } | null;
};
export type DraftWorker = {
  stale?: boolean;
  consumer_enabled?: boolean;
  phase?: string;
  heartbeat_at?: number;
  active_job?: {
    purpose: 'audit' | 'draft' | 'parents';
    symbol?: string | null;
    replay_symbol?: string | null;
    universe?: boolean;
    strategy?: string | null;
    draft_id?: string | null;
    phase?: string | null;
    completed?: number | null;
    total?: number | null;
    updated_at?: number | null;
  };
};

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
  replay: 'Replay',
  cached: 'Đọc kết quả lưu sẵn',
  done: 'Hoàn tất',
};

const CHECKS: Record<string, string> = {
  total_samples_gte_30: 'Mẫu tổng ≥ 30',
  validation_samples_gte_10: 'Mẫu kiểm tra ≥ 10',
  validation_mean_gt_0: 'R kiểm tra > 0',
  validation_dd_lt_10r: 'Drawdown kiểm tra < 10R',
  universe_complete: 'Đủ 10 cặp tiền',
  validation_symbols_gte_3: '≥ 3 cặp có mẫu kiểm tra',
  validation_pair_dd_lt_10r: 'Drawdown từng cặp < 10R',
};

const STATUS_CONFIG: Record<Draft['status'], { label: string; badge: string; cardBorder: string }> =
  {
    queued: {
      label: 'Chờ replay',
      badge: 'border-border-base bg-surface-2 text-text-muted',
      cardBorder: 'border-border-base',
    },
    running: {
      label: 'Đang replay',
      badge: 'border-sky-400/40 bg-sky-500/10 text-sky-300 animate-pulse',
      cardBorder: 'border-sky-500/40',
    },
    insufficient: {
      label: 'Chưa đủ mẫu',
      badge: 'border-amber-400/40 bg-amber-500/10 text-amber-300',
      cardBorder: 'border-amber-500/30',
    },
    waiting_slot: {
      label: 'Đạt · Chờ slot',
      badge: 'border-bull/40 bg-bull-bg text-bull',
      cardBorder: 'border-bull/60',
    },
    rejected: {
      label: 'Không đạt',
      badge: 'border-bear/40 bg-bear-bg text-bear',
      cardBorder: 'border-bear/30',
    },
    error: {
      label: 'Lỗi',
      badge: 'border-bear/40 bg-bear-bg text-bear',
      cardBorder: 'border-bear/40',
    },
    admitted: {
      label: 'Đã vào forward',
      badge: 'border-bull/40 bg-surface-2 text-bull',
      cardBorder: 'border-bull/30',
    },
  };

function phaseText(phase: string): string {
  if (phase.startsWith('parents:')) return `Bố mẹ · ${phaseText(phase.slice(8))}`;
  const split = phase.indexOf(':');
  if (split > 0 && phase.slice(0, split).endsWith('_USDT')) {
    return `${phase.slice(0, split).replace('_USDT', '')} · ${phaseText(phase.slice(split + 1))}`;
  }
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
  const rows = [...drafts].filter(match).sort((a, b) => b.created_at - a.created_at);
  const stalled = (d: Draft) => d.status === 'running' && now - d.updated_at > 90;

  return (
    <div className="space-y-4">
      <div className="flex flex-col gap-2 rounded-lg border border-border-subtle bg-surface-2/60 p-3 sm:flex-row sm:items-center sm:justify-between text-xs text-text-secondary">
        <div className="flex items-center gap-2">
          <span>
            <b>Sàng lọc con nháp</b> · D1/H4: 340 ngày, 10 cặp. M15/H1: 60 ngày, cặp riêng. Chia 70%
            huấn luyện / 30% kiểm tra.
          </span>
        </div>
        <div className="text-text-muted">Replay tách biệt forward. Các cặp có thể tương quan.</div>
      </div>

      {worker?.phase?.startsWith('parents:') && !worker.stale && (
        <div
          role="status"
          className="flex items-center gap-2 rounded-lg border border-sky-500/30 bg-sky-500/5 px-3 py-2 text-xs text-sky-200"
        >
          <span className="inline-block h-2 w-2 animate-ping rounded-full bg-sky-400" />
          <span>
            Tiến trình bố mẹ nền: <b>{phaseText(worker.phase)}</b>
          </span>
        </div>
      )}
      {worker?.consumer_enabled === false && !worker.stale && (
        <p
          className="rounded border border-amber-400/30 bg-amber-400/5 p-3 text-xs text-amber-200"
          role="status"
        >
          Worker replay đang tắt; con nháp chờ cho đến khi bật.
        </p>
      )}
      {worker?.stale !== false && (
        <p
          className="rounded border border-amber-400/30 bg-amber-400/5 p-3 text-xs text-amber-200"
          role="status"
        >
          Worker replay chậm hoặc mất kết nối; con nháp sẽ chờ.
        </p>
      )}
      {rows.some(stalled) && (
        <p
          className="rounded border border-amber-400/30 bg-amber-400/5 p-3 text-xs text-amber-200"
          role="status"
        >
          Worker chậm: có job không cập nhật quá 90 giây.
        </p>
      )}

      <div
        className="flex min-w-0 flex-wrap items-center gap-1.5"
        role="group"
        aria-label="Lọc con nháp"
      >
        {FILTERS.map(([key, label, test]) => {
          const count = drafts.filter(test).length;
          const active = filter === key;
          return (
            <button
              key={key}
              type="button"
              aria-pressed={active}
              onClick={() => setFilter(key)}
              className={`inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-medium transition-colors ${
                active
                  ? 'border border-border-active bg-surface-3 text-text-primary shadow-sm'
                  : 'border border-border-subtle bg-surface-1/60 text-text-muted hover:bg-surface-2 hover:text-text-secondary'
              }`}
            >
              <span>{label}</span>
              <span
                className={`rounded px-1.5 py-0.5 text-xs tabular-nums ${
                  active ? 'bg-surface-1 text-text-primary' : 'bg-surface-2 text-text-muted'
                }`}
              >
                {count}
              </span>
            </button>
          );
        })}
      </div>

      {rows.length === 0 && (
        <div className="rounded-lg border border-border-subtle bg-surface-1/50 py-12 text-center text-xs text-text-muted">
          Không có con nháp trong bộ lọc này.
        </div>
      )}

      <div className="grid min-w-0 gap-3.5 md:grid-cols-2 2xl:grid-cols-3">
        {rows.map((draft) => {
          const pct =
            draft.total && draft.completed !== null
              ? Math.max(0, Math.min(100, Math.floor((100 * draft.completed) / draft.total)))
              : null;
          const result = draft.replay;
          const config = STATUS_CONFIG[draft.status] || STATUS_CONFIG.queued;
          const passedCount = result ? Object.values(result.checks).filter(Boolean).length : 0;
          const totalChecks = result ? Object.keys(result.checks).length : 0;
          const hasValidation = (result?.validation_samples ?? 0) > 0;

          return (
            <article
              key={draft.id}
              className={`relative min-w-0 flex flex-col justify-between rounded-xl border bg-surface-1 p-4 shadow-sm ${config.cardBorder}`}
            >
              <div className="space-y-3">
                <div className="flex flex-wrap items-start justify-between gap-2">
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className="rounded bg-surface-3 px-1.5 py-0.5 font-mono text-[11px] font-semibold text-text-secondary">
                        G{draft.generation}
                      </span>
                      <h4
                        className="truncate text-sm font-semibold text-text-primary"
                        title={draft.strategy}
                      >
                        {strategyShortTitle(draft.genome)}
                      </h4>
                    </div>
                    <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-text-muted">
                      <code title={draft.strategy}>
                        #{(draft.strategy.split(':')[1] ?? draft.strategy).slice(0, 8)}
                      </code>
                      <span>
                        {(result?.scope ?? draft.window?.scope) === 'native_universe_v1'
                          ? '340 ngày · 10 cặp'
                          : '60 ngày · cặp riêng'}
                      </span>
                      {draft.attempts > 0 && <span>· Lần chạy {draft.attempts}/3</span>}
                    </div>
                  </div>
                  <span
                    className={`inline-flex shrink-0 items-center rounded-full border px-2.5 py-0.5 text-xs font-medium ${config.badge}`}
                  >
                    {config.label}
                  </span>
                </div>

                <div className="flex flex-wrap items-center gap-1.5 text-xs">
                  <span className="rounded-md border border-border-subtle bg-surface-2 px-2 py-0.5 text-text-secondary">
                    Vào: <b>{entryPolicyLabel(draft.genome.entry_policy)}</b>
                  </span>
                  <span className="rounded-md border border-border-subtle bg-surface-2 px-2 py-0.5 text-sky-300">
                    Thoát: <b>{exitLabel(draft.genome.exit_policy)}</b>
                  </span>
                </div>

                {draft.status === 'running' && (
                  <div className="space-y-1.5 rounded-lg border border-sky-500/20 bg-sky-500/5 p-2.5 text-xs">
                    <div className="flex justify-between font-medium text-sky-200">
                      <span>{phaseText(draft.phase)}</span>
                      <span>{pct !== null ? `${pct}%` : 'Đang xử lý'}</span>
                    </div>
                    <div
                      role="progressbar"
                      aria-label={phaseText(draft.phase)}
                      aria-valuemin={0}
                      aria-valuemax={100}
                      aria-valuenow={pct ?? undefined}
                      className="relative h-1.5 overflow-hidden rounded-full bg-surface-3"
                    >
                      <div
                        className="h-full rounded-full bg-sky-400 transition-all duration-300"
                        style={{
                          width: pct === null ? '100%' : `${pct}%`,
                          opacity: pct === null ? 0.3 : 1,
                        }}
                      />
                    </div>
                    {draft.total && draft.completed !== null && (
                      <div className="text-right text-[11px] text-text-muted">
                        {draft.completed.toLocaleString()} / {draft.total.toLocaleString()} nến
                      </div>
                    )}
                  </div>
                )}

                {result && (
                  <div className="space-y-2 rounded-lg border border-border-subtle bg-surface-2/30 p-2.5">
                    <div className="grid grid-cols-2 gap-3 border-b border-border-subtle/80 pb-3 text-center sm:grid-cols-4">
                      <div>
                        <div className="text-xs text-text-muted">Mẫu tổng</div>
                        <div className="font-mono text-xs font-semibold text-text-primary">
                          {result.total_samples}
                        </div>
                      </div>
                      <div>
                        <div className="text-xs text-text-muted">Kiểm tra</div>
                        <div className="font-mono text-xs font-semibold text-text-primary">
                          {result.validation_samples}
                        </div>
                      </div>
                      <div>
                        <div className="text-xs text-text-muted">R TB kiểm tra</div>
                        <div
                          className={`font-mono text-xs font-semibold ${
                            !hasValidation || result.validation_mean_r === null
                              ? 'text-text-muted'
                              : result.validation_mean_r > 0
                                ? 'text-bull'
                                : 'text-bear'
                          }`}
                        >
                          {hasValidation && result.validation_mean_r !== null
                            ? `${result.validation_mean_r.toFixed(2)}R`
                            : '—'}
                        </div>
                      </div>
                      <div>
                        <div className="text-xs text-text-muted">DD kiểm tra</div>
                        <div
                          className={`font-mono text-xs font-semibold ${
                            !hasValidation
                              ? 'text-text-muted'
                              : result.validation_drawdown_r >= 10
                                ? 'text-bear'
                                : result.validation_drawdown_r >= 5
                                  ? 'text-amber-300'
                                  : 'text-text-secondary'
                          }`}
                        >
                          {hasValidation ? `−${result.validation_drawdown_r.toFixed(1)}R` : '—'}
                        </div>
                      </div>
                    </div>

                    {!hasValidation && (
                      <p className="text-xs leading-relaxed text-text-secondary">
                        Chưa có lệnh kiểm tra đã đóng. Chưa thể kết luận lợi nhuận hoặc drawdown.
                      </p>
                    )}
                    <div className="space-y-1 pt-1">
                      <div className="flex items-center justify-between text-[11px] text-text-muted">
                        <span>Tiêu chuẩn cổng kiểm tra:</span>
                        <span className="font-mono font-medium text-text-secondary">
                          {passedCount}/{totalChecks} đạt
                        </span>
                      </div>
                      <div className="grid grid-cols-1 gap-1 sm:grid-cols-2">
                        {Object.entries(result.checks).map(([key, ok]) => {
                          const noData =
                            !hasValidation &&
                            key !== 'total_samples_gte_30' &&
                            key !== 'validation_samples_gte_10' &&
                            key !== 'universe_complete' &&
                            key !== 'validation_symbols_gte_3';
                          return (
                            <div
                              key={key}
                              className={`flex items-start gap-1.5 rounded px-2 py-1 text-xs ${
                                noData
                                  ? 'bg-surface-2 text-text-muted'
                                  : ok
                                    ? 'bg-bull/10 text-bull'
                                    : 'bg-bear/10 text-bear'
                              }`}
                            >
                              <span
                                className="font-bold"
                                aria-label={noData ? 'Chưa có mẫu' : ok ? 'Đạt' : 'Không đạt'}
                              >
                                {noData ? '—' : ok ? '✓' : '✗'}
                              </span>
                              <span className="min-w-0 break-words">{CHECKS[key] ?? key}</span>
                            </div>
                          );
                        })}
                      </div>
                    </div>
                    {result.per_symbol && Object.keys(result.per_symbol).length > 0 && (
                      <details className="group pt-1">
                        <summary className="cursor-pointer text-[11px] text-text-muted hover:text-text-primary select-none flex items-center justify-between">
                          <span>Kết quả riêng từng cặp</span>
                          <span className="text-[10px] text-text-muted group-open:rotate-180 transition-transform">
                            ▼
                          </span>
                        </summary>
                        <div className="mt-2 overflow-x-auto rounded border border-border-subtle bg-surface-1/60">
                          <table className="w-full text-left font-mono text-[11px]">
                            <caption className="sr-only">
                              Kết quả replay riêng từng cặp tiền
                            </caption>
                            <thead>
                              <tr className="border-b border-border-subtle text-[10px] text-text-muted">
                                <th scope="col" className="px-2 py-1 font-medium">
                                  Cặp
                                </th>
                                <th scope="col" className="px-2 py-1 text-right font-medium">
                                  Mẫu train
                                </th>
                                <th scope="col" className="px-2 py-1 text-right font-medium">
                                  R train
                                </th>
                                <th scope="col" className="px-2 py-1 text-right font-medium">
                                  Mẫu val
                                </th>
                                <th scope="col" className="px-2 py-1 text-right font-medium">
                                  R val
                                </th>
                                <th scope="col" className="px-2 py-1 text-right font-medium">
                                  DD val
                                </th>
                              </tr>
                            </thead>
                            <tbody className="divide-y divide-border-subtle/40">
                              {(result.symbols ?? Object.keys(result.per_symbol)).map((symbol) => {
                                const data = result.per_symbol?.[symbol] ?? {};
                                const strat =
                                  data.strategies?.find((s) => s.strategy === draft.strategy) ??
                                  data.strategies?.[0];
                                const tr = strat?.train;
                                const val = strat?.validation;
                                const valCount = val?.resolved ?? 0;
                                const valMean = val?.mean_r ?? null;
                                const valDd = val?.max_drawdown_r ?? 0;
                                return (
                                  <tr key={symbol} className="hover:bg-surface-2/40">
                                    <td className="px-2 py-1 font-semibold text-text-primary">
                                      {symbol.replace('_USDT', '')}
                                    </td>
                                    <td className="px-2 py-1 text-right text-text-secondary">
                                      {tr?.resolved ?? 0}
                                    </td>
                                    <td
                                      className={`px-2 py-1 text-right ${
                                        tr && tr.mean_r !== null && tr.mean_r !== undefined
                                          ? tr.mean_r > 0
                                            ? 'text-bull'
                                            : tr.mean_r < 0
                                              ? 'text-bear'
                                              : 'text-text-muted'
                                          : 'text-text-muted'
                                      }`}
                                    >
                                      {tr && tr.mean_r !== null && tr.mean_r !== undefined
                                        ? `${tr.mean_r > 0 ? '+' : ''}${tr.mean_r.toFixed(2)}R`
                                        : '—'}
                                    </td>
                                    <td className="px-2 py-1 text-right text-text-secondary">
                                      {valCount}
                                    </td>
                                    <td
                                      className={`px-2 py-1 text-right font-semibold ${
                                        valCount > 0 && valMean !== null
                                          ? valMean > 0
                                            ? 'text-bull'
                                            : valMean < 0
                                              ? 'text-bear'
                                              : 'text-text-muted'
                                          : 'text-text-muted'
                                      }`}
                                    >
                                      {valCount > 0 && valMean !== null
                                        ? `${valMean > 0 ? '+' : ''}${valMean.toFixed(2)}R`
                                        : '—'}
                                    </td>
                                    <td
                                      className={`px-2 py-1 text-right ${
                                        valCount === 0
                                          ? 'text-text-muted'
                                          : valDd >= 10
                                            ? 'text-bear'
                                            : valDd >= 5
                                              ? 'text-amber-300'
                                              : 'text-text-secondary'
                                      }`}
                                    >
                                      {valCount > 0 ? `−${valDd.toFixed(1)}R` : '—'}
                                    </td>
                                  </tr>
                                );
                              })}
                            </tbody>
                          </table>
                        </div>
                      </details>
                    )}
                  </div>
                )}
              </div>

              <div className="mt-3 space-y-2 border-t border-border-subtle pt-2 text-xs [&>*]:break-words">
                <details className="text-text-muted">
                  <summary className="py-1 hover:text-text-primary">
                    Dòng dõi & mã chiến thuật
                  </summary>
                  <dl className="mt-2 space-y-2">
                    <div>
                      <dt>Chiến thuật</dt>
                      <dd className="break-all font-mono text-text-secondary">{draft.strategy}</dd>
                    </div>
                    <div>
                      <dt>Bố mẹ</dt>
                      <dd className="break-all font-mono text-text-secondary">
                        {draft.parents.join(' × ') || 'Gốc (G0)'}
                      </dd>
                    </div>
                  </dl>
                </details>
                {draft.status === 'rejected' && (
                  <p className="text-text-muted">Đã dừng sàng lọc · không tự thử lại.</p>
                )}
                {draft.status === 'waiting_slot' && (
                  <div className="text-bull">
                    Đạt điều kiện ·{' '}
                    {draft.reason === 'no_retirement_candidate'
                      ? 'quần thể forward đã đầy, chờ có cây yếu thế chỗ'
                      : draft.reason === 'ownership_unknown'
                        ? 'chờ lệnh forward hiện hành hoàn tất kiểm tra'
                        : 'chờ kết nạp theo hàng đợi'}
                  </div>
                )}
                {draft.status === 'insufficient' && draft.next_retry_at && (
                  <div className="text-amber-300">
                    Chưa đủ mẫu · Tự thử lại vào: {formatTime(draft.next_retry_at)}
                  </div>
                )}
                {draft.status === 'queued' && draft.next_retry_at && (
                  <div className="text-text-muted">
                    Hẹn giờ chạy: {formatTime(draft.next_retry_at)}
                    {draft.reason ? ` (${draft.reason})` : ''}
                  </div>
                )}
                {draft.status === 'error' && (
                  <div className="font-mono text-bear">{draft.reason}</div>
                )}
                {stalled(draft) && (
                  <div className="text-bear">Quá thời gian cập nhật (worker chậm)</div>
                )}
                {draft.status === 'admitted' && draft.admitted_strategy && (
                  <button
                    type="button"
                    onClick={() => onViewTree(draft.admitted_strategy!)}
                    className="inline-flex w-full items-center justify-center rounded-md border border-bull/40 bg-bull/10 py-1.5 font-medium text-bull transition-colors hover:bg-bull/20"
                  >
                    Xem chi tiết cây forward →
                  </button>
                )}
              </div>
            </article>
          );
        })}
      </div>
    </div>
  );
}
