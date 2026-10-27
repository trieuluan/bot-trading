import { Activity, Check, X } from 'lucide-react';
import { useEffect, useState } from 'react';
import type { ForexSignalSetup } from '../lib/forex-entry-lifecycle';
import { entryLifecycle } from '../lib/forex-entry-lifecycle';
import { formatTime } from '../lib/time-format';
import { priceDigits } from '../lib/forex-labels';

export function ForexSignalLifecycle({
  setups,
  symbol,
  selectedId,
  onSelect,
}: {
  setups: ForexSignalSetup[];
  symbol: string;
  selectedId?: string;
  onSelect: (id: string | null) => void;
}) {
  const [open, setOpen] = useState(false);
  const [filter, setFilter] = useState('pending');
  useEffect(() => {
    const show = () => {
      if (window.location.hash === '#forex-signals') setOpen(true);
    };
    setOpen(false);
    setFilter('pending');
    show();
    window.addEventListener('hashchange', show);
    return () => window.removeEventListener('hashchange', show);
  }, [symbol]);
  const shadowPending = (setup: ForexSignalSetup) => setup.signals.some((signal) =>
    signal.strategy.startsWith('hast4_') && ['waiting', 'pending', 'open'].includes(signal.status));
  const pending = (setup: ForexSignalSetup) => shadowPending(setup) || setup.intents.some((intent) =>
    ['waiting', 'triggered', 'submitting', 'submitted', 'uncertain'].includes(intent.status));
  const rows = setups.filter((setup) => filter === 'all' || (filter === 'pending' ? pending(setup) : !pending(setup)));
  const selected = setups.find((setup) => setup.id === selectedId);
  const allIntents = setups.flatMap((setup) => setup.intents);
  const waiting = allIntents.filter((intent) => intent.status === 'waiting').length;
  const shadowCount = setups.filter(shadowPending).length;
  const unresolved = allIntents.filter((intent) => intent.status === 'uncertain').length;
  const totalSignals = new Set(
    setups.flatMap((setup) => setup.signals.map((signal) => `${signal.strategy}:${signal.available_at}:${signal.side}`)),
  ).size;
  const price = (value: number | null | undefined) =>
    value == null || !Number.isFinite(value) ? '—' : value.toFixed(priceDigits(symbol));
  const reasons: Record<string, string> = {
    quote_gap: 'Mất nhịp giá',
    quote_stale: 'Giá quá cũ',
    level_already_crossed: 'Giá đã vượt mức chờ',
    entry_expired: 'Quá thời hạn chờ',
    worker_restart: 'Worker khởi động lại',
    trigger_preflight_failed: 'Không qua kiểm tra trước gửi',
    ambiguous_entry: 'Không rõ nhánh chạm trước',
    capacity_unavailable: 'Không đủ chỗ mở lệnh',
    invalidated: 'Setup mất hiệu lực',
  };

  return (
    <details id="forex-signals" className="forex-signal-lifecycle group/lifecycle min-w-0 border-y border-border-base bg-surface-1 text-xs text-text-secondary" open={open}
      onToggle={(event) => setOpen(event.currentTarget.open)}>
      <summary className="flex cursor-pointer list-none flex-wrap items-center gap-2 px-3 py-2.5 select-none hover:bg-surface-2 transition-colors">
        <span className="inline-flex size-6 shrink-0 items-center justify-center rounded-md bg-surface-3 text-text-secondary"><Activity size={13} aria-hidden="true" /></span>
        <strong className="text-xs font-semibold text-text-primary">Setup trên chart</strong>
        <span className="text-xs text-text-muted">
          {setups.length} setup · {totalSignals} tín hiệu · {waiting} canh broker · {shadowCount} mô phỏng
        </span>
        {unresolved > 0 && <span className="pair-intent is-uncertain inline-flex shrink-0 items-center gap-1 rounded-md px-2 py-0.5 font-mono text-[11px] font-semibold text-amber-200 bg-amber-400/10 border border-amber-400/20">{unresolved} cần đối soát</span>}
        {selected && <span className="signal-setup-selected text-xs font-medium text-bull">Đang xem: {selected.side === 'long' ? 'Mua' : 'Bán'} · {formatTime(selected.time, true)}</span>}
        <span className="signal-lifecycle-disclosure ml-auto text-xs font-medium text-text-muted after:content-['+'] group-open/lifecycle:after:content-['−']">Danh sách</span>
      </summary>
      <div className="signal-lifecycle-toolbar flex min-w-0 flex-wrap items-center justify-between gap-2 border-t border-border-subtle bg-surface-2/30 px-3 py-2">
        <div className="signal-lifecycle-filters flex flex-wrap items-center gap-1" role="group" aria-label="Lọc setup">
          {([['pending', 'Đang canh / xử lý'], ['history', 'Lịch sử'], ['all', 'Tất cả']] as const).map(([value, label]) =>
            <button type="button" key={value} aria-pressed={filter === value} onClick={() => setFilter(value)}
              className={`rounded-lg border px-3 py-1 text-xs font-medium transition-colors ${filter === value ? 'border-bull/20 bg-bull/10 font-semibold text-bull' : 'border-transparent text-text-muted hover:bg-surface-2 hover:text-text-primary'}`}>{label}</button>)}
        </div>
        {selected && <button type="button" className="signal-lifecycle-clear-btn inline-flex items-center gap-1 rounded border-0 bg-transparent px-2 py-1 text-xs text-text-secondary hover:text-bear" onClick={() => onSelect(null)}>
          <X size={12} aria-hidden="true" /> Bỏ chọn setup
        </button>}
      </div>
      <details className="signal-lifecycle-help border-t border-border-subtle px-3 py-2 text-xs text-text-muted [&>summary]:cursor-pointer [&_p]:mt-2 [&_p]:leading-relaxed">
        <summary className="cursor-pointer hover:text-text-primary select-none font-medium">Hướng dẫn · trạng thái</summary>
        <p className="signal-lifecycle-guide mt-1 text-emerald-400/90">Chọn để xem trên chart · bấm lại để bỏ chọn. Không gửi hay hủy lệnh.</p>
        <p className="mt-0.5 leading-relaxed text-text-muted">Canh: chờ giá · Kích: đang gửi · Đã gửi: chờ xác nhận · Khớp: broker xác nhận · Đối soát: chưa rõ kết quả · Hủy / Hết hạn: dừng canh · Mô phỏng: V4 chạy ảo, không phải fill broker.</p>
      </details>
      <div
        className="signal-lifecycle-list flex max-h-[250px] min-w-0 flex-col overflow-y-auto overscroll-y-contain"
        role="region"
        aria-label={`Vòng đời tín hiệu ${symbol}`}
        tabIndex={0}
      >
        {rows.map((setup) => {
          const isSelected = setup.id === selectedId;
          const mainIntent = setup.intents[0];
          const state = mainIntent
            ? entryLifecycle(mainIntent)
            : shadowPending(setup)
              ? { label: 'Mô phỏng đang chạy', short: 'Mô phỏng', tone: 'waiting' }
              : { label: 'Tín hiệu ghi nhận', short: 'Ghi nhận', tone: 'idle' };
          const sideVn = setup.side === 'long' ? 'MUA' : 'BÁN';
          const v4Count = setup.signals.filter((signal) => signal.strategy.startsWith('hast4_')).length;
          const v3Count = setup.signals.filter((signal) => !signal.strategy.startsWith('hast4_')).length;
          const tagParts = [];
          if (v4Count > 0) tagParts.push(`V4 ×${v4Count}`);
          if (v3Count > 0) tagParts.push(`V3 ×${v3Count}`);
          const fill = setup.intents.flatMap((intent) => intent.broker_fills ?? [])[0];

          return (
            <article
              key={setup.id}
              className={`signal-lifecycle-row is-interactive min-w-0 shrink-0 border-t border-l-2 border-border-subtle transition-colors ${isSelected ? 'is-selected border-l-bull bg-surface-3' : 'border-l-transparent hover:bg-surface-2'}`}
            >
              <button type="button" className="signal-lifecycle-select block w-full min-w-0 cursor-pointer border-0 bg-transparent px-3 py-2.5 text-left text-inherit"
                onClick={() => onSelect(isSelected ? null : setup.id)} aria-pressed={isSelected}>
              <div className="signal-lifecycle-heading flex min-w-0 flex-wrap items-center gap-2 text-xs">
                <span className={`pair-intent is-${state.tone} inline-flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 text-xs font-medium [&.is-waiting]:bg-tone-waiting-bg [&.is-waiting]:text-tone-waiting [&.is-triggered]:bg-tone-triggered-bg [&.is-triggered]:text-tone-triggered [&.is-filled]:bg-tone-filled-bg [&.is-filled]:text-tone-filled [&.is-uncertain]:bg-tone-uncertain-bg [&.is-uncertain]:text-tone-uncertain [&.is-cancelled]:bg-tone-cancelled-bg [&.is-cancelled]:text-tone-cancelled [&.is-ended]:bg-tone-ended-bg [&.is-ended]:text-tone-ended`} title={state.label}>{state.short}</span>
                {mainIntent && shadowPending(setup) && <span className="signal-setup-tags text-[10px] px-1.5 py-0.5 rounded bg-surface-3 text-text-muted border border-border-subtle text-xs">V4 mô phỏng</span>}
                <strong className={`font-bold uppercase tracking-wide text-xs ${setup.side === 'long' ? 'positive text-emerald-400' : 'negative text-rose-400'}`}>
                  {sideVn}
                </strong>
                <span className="signal-setup-tags font-mono text-xs text-text-muted tabular-nums">{tagParts.join(' · ')}</span>
                <time className="ml-auto font-mono text-xs text-text-muted tabular-nums" title={formatTime(setup.time, true)}>{formatTime(setup.time)}</time>
                <span className="signal-lifecycle-select-action hidden shrink-0 text-xs text-bull sm:inline-flex">
                  {isSelected ? (
                    <span className="is-active inline-flex items-center gap-1 text-xs font-semibold text-emerald-400">
                      <Check size={12} aria-hidden="true" /> Đang xem
                    </span>
                  ) : (
                    <span className="text-xs text-text-muted hover:text-emerald-300">Xem trên chart</span>
                  )}
                </span>
              </div>
              {isSelected && <div className="signal-lifecycle-meta mt-2 grid min-w-0 gap-2 border-t border-border-subtle pt-2 text-xs text-text-secondary sm:grid-cols-2 [&>*]:min-w-0 [&>*]:break-words">
                {mainIntent ? (
                  <>
                    <span className="font-mono tabular-nums">
                      Limit {price(mainIntent.limit_price)} · Stop {price(mainIntent.stop_price)}
                    </span>
                    {mainIntent.winner && (
                      <span>
                        Kích nhánh {mainIntent.winner.toUpperCase()}
                        {mainIntent.triggered_at ? ` lúc ${formatTime(mainIntent.triggered_at, true)}` : ''}
                      </span>
                    )}
                    {mainIntent.ended_at && (
                      <span>Kết thúc {formatTime(mainIntent.ended_at, true)}</span>
                    )}
                    {mainIntent.reason && (
                      <span title={mainIntent.reason} className="text-amber-400/90">
                        {reasons[mainIntent.reason] ?? mainIntent.reason}
                      </span>
                    )}
                    {fill && <span className="font-mono text-emerald-400 tabular-nums">Fill #{fill.position_id} @ {price(fill.price)}</span>}
                  </>
                ) : (
                  <span>{shadowPending(setup) ? 'V4 đang canh / chạy mô phỏng · chọn để xem H4/H1/M15 và mức giá ảo.'
                    : 'Chỉ ghi nhận tín hiệu · chưa có lệnh canh giá tương ứng; không phải fill broker.'}</span>
                )}
              </div>}
                {isSelected && mainIntent && <ol className="signal-lifecycle-steps m-0 mt-2 flex list-none flex-wrap gap-1 p-0 text-xs" aria-label="Vòng đời setup đã chọn">
                  {([
                    ['Tín hiệu', true], ['Canh giá', true], ['Kích hoạt', mainIntent.triggered_at != null],
                    ['Gửi broker', mainIntent.status === 'submitted' || !!mainIntent.broker_fills?.length],
                    ['Khớp', !!mainIntent.broker_fills?.length],
                  ] as const).map(([label, done]) => <li key={label} className={`px-1.5 py-0.5 rounded ${done ? 'is-done bg-emerald-950/60 text-emerald-400 border border-emerald-800/40' : 'bg-surface-2 text-text-muted/60'}`}>{label}</li>)}
                </ol>}
              </button>
              {isSelected && <details className="signal-lifecycle-details border-t border-border-subtle px-3 py-2 text-xs [&>summary]:cursor-pointer [&>summary]:text-text-muted">
                <summary className="cursor-pointer text-text-muted hover:text-text-primary select-none py-1">Chi tiết thực thi · {setup.intents.length} nhánh</summary>
                <div className="signal-lifecycle-meta mt-2 grid min-w-0 gap-2 border-t border-border-subtle pt-2 text-xs text-text-secondary sm:grid-cols-2 [&>*]:min-w-0 [&>*]:break-words">
                {setup.intents.map((intent) => <div className="signal-setup-strategies min-w-0 break-all font-mono text-xs text-text-secondary" key={intent.id}>
                  {intent.strategy} · {entryLifecycle(intent).label} · Limit {price(intent.limit_price)} · Stop {price(intent.stop_price)}
                  {intent.expires_at ? ` · Hạn ${formatTime(intent.expires_at, true)}` : ''}
                  {intent.reason ? ` · ${reasons[intent.reason] ?? intent.reason}` : ''}
                  {intent.broker_fills?.map((fill) => ` · Fill #${fill.position_id} @ ${price(fill.price)}`).join('')}
                </div>)}
                <span className="signal-setup-strategies block pt-1 text-text-muted/70 min-w-0 break-all font-mono text-xs text-text-secondary">Cây ghi nhận: {setup.signals.map((signal) => signal.strategy).join(', ')}</span>
                </div>
              </details>}
            </article>
          );
        })}
        {!rows.length && (
          <p className="signal-lifecycle-empty m-0 px-4 py-4 text-center text-xs text-text-muted">
            {setups.length ? 'Không có setup trong bộ lọc này. Chọn Lịch sử hoặc Tất cả để xem tín hiệu đã qua.'
              : 'Chưa có tín hiệu ghi nhận hoặc setup canh giá trong dữ liệu hiện tại.'}
          </p>
        )}
      </div>
    </details>
  );
}
