import { Activity, Check, ChevronDown, X } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import type { ForexSignalSetup } from '../lib/forex-entry-lifecycle';
import type { ForexEntryIntent } from '../types/forex-entry';
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
  const [filter, setFilter] = useState<'pending' | 'history' | 'all'>('pending');
  const [detailId, setDetailId] = useState<string | null>(null);
  const dialogRef = useRef<HTMLDialogElement | null>(null);
  const openerRef = useRef<HTMLButtonElement | null>(null);

  useEffect(() => {
    const show = () => {
      if (window.location.hash === '#forex-signals') setOpen(true);
    };
    setOpen(false);
    setFilter('pending');
    setDetailId(null);
    show();
    window.addEventListener('hashchange', show);
    return () => window.removeEventListener('hashchange', show);
  }, [symbol]);

  const shadowPending = (setup: ForexSignalSetup) =>
    setup.signals.some(
      (signal) =>
        signal.strategy.startsWith('hast4_') &&
        ['waiting', 'pending', 'open'].includes(signal.status),
    );

  const pending = (setup: ForexSignalSetup) =>
    shadowPending(setup) ||
    setup.intents.some((intent) =>
      ['waiting', 'triggered', 'submitting', 'submitted', 'uncertain'].includes(intent.status),
    );

  const pendingCount = setups.filter(pending).length;
  const rows = setups.filter((setup) =>
    filter === 'all' ? true : filter === 'pending' ? pending(setup) : !pending(setup),
  );
  const selected = setups.find((setup) => setup.id === selectedId);
  const detail = setups.find((setup) => setup.id === detailId);
  useEffect(() => {
    const dialog = dialogRef.current;
    if (!detail || !dialog) return;
    const opener = openerRef.current;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    dialog.showModal();
    return () => {
      dialog.close();
      document.body.style.overflow = previousOverflow;
      if (opener?.isConnected) opener.focus({ preventScroll: true });
    };
  }, [detail?.id, symbol]);
  const allIntents = setups.flatMap((setup) => setup.intents);
  const waiting = allIntents.filter((intent) => intent.status === 'waiting').length;
  const shadowCount = setups.filter(shadowPending).length;
  const unresolved = allIntents.filter((intent) => intent.status === 'uncertain').length;
  const totalSignals = new Set(
    setups.flatMap((setup) =>
      setup.signals.map((signal) => `${signal.strategy}:${signal.available_at}:${signal.side}`),
    ),
  ).size;

  const price = (value: number | null | undefined) =>
    value == null || !Number.isFinite(value) ? '—' : value.toFixed(priceDigits(symbol));

  const reasonCatalog: Record<string, { label: string; hint: string }> = {
    poor_rr: { label: 'R/R kém', hint: 'Dưới 1.5R sau chi phí' },
    sl_too_tight: { label: 'SL quá sát', hint: 'Khoảng cách SL dưới ngưỡng ATR của phiên bản chiến thuật' },
    sl_too_wide: { label: 'SL quá xa', hint: 'Vượt 3 ATR H1' },
    price_drift: { label: 'Giá lệch quá xa', hint: 'Giá hiện tại lệch quá 0.25R so với giá tham chiếu' },
    virtual_entry_expired: { label: 'Entry ảo không còn hợp lệ', hint: 'Không tìm thấy intent hoặc quote của intent không đủ mới' },
    invalid_htf_levels: { label: 'Mức HTF không hợp lệ', hint: 'Thiếu hoặc lỗi cấu trúc TP/SL' },
    quote_gap: { label: 'Mất nhịp giá', hint: 'Khoảng quote vượt quá 2 giây' },
    quote_stale: { label: 'Giá quá cũ', hint: 'Dữ liệu quote không đủ mới' },
    level_already_crossed: { label: 'Giá đã vượt mức chờ', hint: 'Không thể vào đuổi' },
    entry_expired: { label: 'Quá thời hạn chờ', hint: 'Hết thời gian canh nến' },
    worker_restart: { label: 'Worker khởi động lại', hint: 'Hủy canh để đảm bảo an toàn' },
    preflight_failed: {
      label: 'Không qua kiểm tra trước gửi',
      hint: 'Bản ghi chưa lưu nguyên nhân chi tiết; chưa xác định kiểm tra nào đã chặn lệnh.',
    },
    trigger_preflight_failed: { label: 'Không qua kiểm tra trước gửi', hint: 'Chưa có thông tin chi tiết' },
    ambiguous_entry: { label: 'Không rõ nhánh chạm trước', hint: 'Dữ liệu không đủ xác định thứ tự kích hoạt' },
    capacity_unavailable: { label: 'Không đủ chỗ mở lệnh', hint: 'Giới hạn số vị thế đồng thời' },
    invalidated: { label: 'Setup mất hiệu lực', hint: 'Cấu trúc thị trường đã thay đổi' },
  };

  const explainReason = (intent?: ForexEntryIntent | null) => {
    if (!intent?.reason) return null;
    const code = intent.reason_code || (intent.reason !== 'preflight_failed' ? intent.reason : null);
    const mapped = code ? reasonCatalog[code] : reasonCatalog[intent.reason];
    if (mapped) return { code: code || intent.reason, label: mapped.label, hint: mapped.hint, detail: intent.reason_detail };
    return { code: code || intent.reason, label: 'Kiểm tra trước gửi không đạt', hint: 'Chưa có diễn giải cho mã này; xem chi tiết kỹ thuật.', detail: intent.reason_detail || code || intent.reason };
  };

  const closeDialog = () => setDetailId(null);

  return (
    <>
    <details
      id="forex-signals"
      className="forex-signal-lifecycle group/lifecycle min-w-0 border-y border-border-base bg-surface-1 text-xs text-text-secondary"
      open={open}
      onToggle={(event) => setOpen(event.currentTarget.open)}
    >
      <summary className="flex cursor-pointer list-none flex-wrap items-center gap-2 px-3 py-2.5 select-none hover:bg-surface-2 transition-colors">
        <span className="inline-flex size-6 shrink-0 items-center justify-center rounded-md bg-surface-3 text-text-secondary">
          <Activity size={13} aria-hidden="true" />
        </span>
        <strong className="text-xs font-semibold text-text-primary">Setup trên chart</strong>
        <span className="rounded-md bg-surface-3 px-1.5 py-0.5 font-mono text-[11px] text-text-secondary">
          {setups.length}
        </span>
        <span className="text-[11px] text-text-muted">{totalSignals} tín hiệu</span>
        {waiting > 0 && <span className="text-[11px] text-amber-300">{waiting} canh broker</span>}
        {shadowCount > 0 && <span className="text-[11px] text-sky-300">{shadowCount} mô phỏng</span>}
        {unresolved > 0 && (
          <span className="pair-intent is-uncertain inline-flex shrink-0 items-center gap-1 rounded-md px-2 py-0.5 font-mono text-[11px] font-semibold text-amber-200 bg-amber-400/10 border border-amber-400/20">
            {unresolved} cần đối soát
          </span>
        )}
        {selected && (
          <span className="signal-setup-selected text-xs font-medium text-sky-300">
            Đang xem: {selected.side === 'long' ? 'Mua' : 'Bán'} · {formatTime(selected.time, true)}
          </span>
        )}
        <span className="signal-lifecycle-disclosure ml-auto inline-flex items-center gap-1 text-xs font-medium text-text-muted group-hover/lifecycle:text-text-primary">
          <span>Danh sách</span>
          <ChevronDown
            size={14}
            className="transition-transform duration-200 group-open/lifecycle:rotate-180"
            aria-hidden="true"
          />
        </span>
      </summary>

      <div className="signal-lifecycle-toolbar flex min-w-0 flex-wrap items-center justify-between gap-2 border-t border-border-subtle bg-surface-2/30 px-3 py-2">
        <div className="signal-lifecycle-filters flex flex-wrap items-center gap-1" role="group" aria-label="Lọc setup">
          {(
            [
              ['pending', 'Đang canh / xử lý', pendingCount],
              ['history', 'Lịch sử', setups.length - pendingCount],
              ['all', 'Tất cả', setups.length],
            ] as const
          ).map(([value, label, count]) => (
            <button
              type="button"
              key={value}
              aria-pressed={filter === value}
              onClick={() => setFilter(value)}
              className={`inline-flex min-h-8 items-center gap-2 rounded-lg border px-2.5 py-1 text-xs font-medium transition-colors ${
                filter === value
                  ? 'border-sky-400/30 bg-sky-400/10 font-semibold text-sky-200'
                  : 'border-transparent text-text-muted hover:bg-surface-2 hover:text-text-primary'
              }`}
            >
              {label}
              <span className="rounded bg-surface-3 px-1.5 font-mono text-[10px] text-text-secondary">
                {count}
              </span>
            </button>
          ))}
        </div>
        {selected && (
          <button
            type="button"
            className="signal-lifecycle-clear-btn inline-flex items-center gap-1 rounded border-0 bg-transparent px-2 py-1 text-xs text-text-secondary hover:text-bear"
            onClick={() => onSelect(null)}
          >
            <X size={12} aria-hidden="true" /> Bỏ chọn setup
          </button>
        )}
      </div>

      <div
        className="signal-lifecycle-list grid max-h-[260px] min-w-0 grid-cols-1 items-start gap-2 overflow-y-auto overscroll-y-contain px-3 py-2 sm:grid-cols-2 xl:grid-cols-3"
        role="region"
        aria-label={`Vòng đời tín hiệu ${symbol}`}
        tabIndex={0}
      >
        {rows.map((setup) => {
          const isSelected = setup.id === selectedId;
          const mainIntent = setup.intents[0];
          const execState = mainIntent ? entryLifecycle(mainIntent) : null;
          const isSimulating = shadowPending(setup);
          const sideVn = setup.side === 'long' ? 'MUA' : 'BÁN';
          const v4Count = setup.signals.filter((signal) => signal.strategy.startsWith('hast4_')).length;
          const v3Count = setup.signals.filter((signal) => !signal.strategy.startsWith('hast4_')).length;
          const tagParts: string[] = [];
          if (v4Count > 0) tagParts.push(`V4 ×${v4Count}`);
          if (v3Count > 0) tagParts.push(`V3 ×${v3Count}`);
          const fill = setup.intents.flatMap((intent) => intent.broker_fills ?? [])[0];
          const reasonInfo = explainReason(mainIntent);

          const execTone = !execState
            ? 'border-border-subtle bg-surface-2 text-text-muted'
            : execState.tone === 'filled'
              ? 'border-emerald-400/20 bg-emerald-400/10 text-emerald-300'
              : execState.tone === 'waiting' || execState.tone === 'triggered'
                ? 'border-amber-400/20 bg-amber-400/10 text-amber-300'
                : execState.tone === 'uncertain'
                  ? 'border-rose-400/20 bg-rose-400/10 text-rose-300'
                  : 'border-border-subtle bg-surface-2 text-text-muted';

          return (
            <article
              key={setup.id}
              className={`signal-lifecycle-row is-interactive flex min-w-0 items-stretch rounded-lg border transition-colors ${
                isSelected
                  ? 'is-selected border-sky-400/60 bg-sky-400/5 ring-1 ring-sky-400/20'
                  : 'border-border-base bg-canvas/40 hover:border-border-active hover:bg-surface-2'
              }`}
            >
              <button
                type="button"
                className="signal-lifecycle-select flex min-w-0 flex-1 cursor-pointer flex-col gap-1 border-0 bg-transparent px-3 py-2 text-left text-inherit"
                onClick={() => onSelect(isSelected ? null : setup.id)}
                aria-pressed={isSelected}
              >
                <span className="flex min-w-0 items-center gap-2 text-xs">
                  {isSelected && (
                    <Check size={12} className="shrink-0 text-sky-300" aria-label="Đang xem trên chart" />
                  )}
                  <strong
                    className={`rounded px-1.5 py-0.5 text-[11px] font-bold tracking-wide ${
                      setup.side === 'long' ? 'positive bg-bull/15 text-bull' : 'negative bg-bear/15 text-bear'
                    }`}
                  >
                    {sideVn}
                  </strong>
                  <span className="signal-setup-tags truncate font-mono text-text-muted">
                    {tagParts.join(' · ')}
                  </span>
                  <time
                    className="ml-auto shrink-0 font-mono text-[11px] text-text-muted tabular-nums"
                    title={formatTime(setup.time, true)}
                  >
                    {formatTime(setup.time)}
                  </time>
                </span>

                <span className="flex min-w-0 flex-wrap items-center gap-1.5 text-[11px]">
                  {execState ? (
                    <span
                      className={`pair-intent is-${execState.tone} shrink-0 rounded-md border px-1.5 py-0.5 font-medium ${execTone}`}
                      title={execState.label}
                    >
                      {execState.short}
                    </span>
                  ) : (
                    <span className="shrink-0 rounded-md border border-border-subtle bg-surface-2 px-1.5 py-0.5 font-medium text-text-muted">
                      Ghi nhận
                    </span>
                  )}
                  {v4Count > 0 && (
                    <span className="shrink-0 rounded bg-sky-400/10 px-1.5 py-0.5 font-medium text-sky-300">
                      Mô phỏng: {isSimulating ? 'Đang chạy' : 'Đã ghi nhận'}
                    </span>
                  )}
                  {reasonInfo && (
                    <span className="truncate text-amber-400/90" title={reasonInfo.hint}>
                      {reasonInfo.label}
                    </span>
                  )}
                  {fill && (
                    <span className="truncate font-mono text-emerald-400">
                      Fill @ {price(fill.price)}
                    </span>
                  )}
                </span>
              </button>

              <button
                type="button"
                className="signal-lifecycle-detail-btn m-1.5 shrink-0 self-center rounded-md border border-border-base bg-surface-2 px-2 py-1 text-[11px] text-text-secondary hover:border-border-active hover:text-text-primary"
                onClick={(event) => {
                  openerRef.current = event.currentTarget;
                  setDetailId(setup.id);
                }}
              >
                Chi tiết
              </button>
            </article>
          );
        })}

        {!rows.length && (
          <p className="signal-lifecycle-empty col-span-full m-0 rounded-lg border border-dashed border-border-base px-4 py-4 text-center text-xs text-text-muted">
            {setups.length
              ? 'Không có setup trong bộ lọc này. Chọn Lịch sử hoặc Tất cả để xem tín hiệu đã qua.'
              : 'Chưa có tín hiệu ghi nhận hoặc setup canh giá trong dữ liệu hiện tại.'}
          </p>
        )}
      </div>

      <details className="signal-lifecycle-help border-t border-border-subtle px-3 py-2 text-[11px] text-text-muted [&_p]:mt-2 [&_p]:leading-relaxed">
        <summary className="cursor-pointer font-medium select-none hover:text-text-primary">Hướng dẫn · trạng thái</summary>
        <p>Chọn để xem trên chart · bấm lại để bỏ chọn. Bấm Chi tiết để xem timeline đầy đủ. Không gửi hay hủy lệnh.</p>
        <p>Thực thi và mô phỏng là hai luồng độc lập. Mô phỏng không phải fill broker.</p>
      </details>
    </details>
      <dialog
        ref={dialogRef}
        aria-label="Chi tiết setup"
        onClose={closeDialog}
        onClick={(event) => {
          if (event.target === dialogRef.current) closeDialog();
        }}
        className="signal-drawer fixed inset-y-0 right-0 left-auto m-0 h-dvh max-h-none w-[min(520px,100vw)] max-w-none border-0 border-l border-border-active bg-surface-1 p-0 text-xs text-text-secondary shadow-2xl backdrop:bg-black/60 backdrop:backdrop-blur-sm open:flex open:flex-col open:animate-in open:slide-in-from-right duration-300 motion-reduce:animate-none"
      >
        {detail && (
          <div className="flex h-full flex-col overflow-y-auto p-5">
            <header className="flex items-center justify-between gap-2 border-b border-border-base pb-3">
              <div className="flex items-center gap-2">
                <strong
                  className={`rounded px-1.5 py-0.5 text-[11px] font-bold ${
                    detail.side === 'long' ? 'bg-bull/15 text-bull' : 'bg-bear/15 text-bear'
                  }`}
                >
                  {detail.side === 'long' ? 'MUA' : 'BÁN'}
                </strong>
                <span className="font-mono text-text-primary">
                  {symbol} · {formatTime(detail.time, true)}
                </span>
              </div>
              <button
                type="button"
                autoFocus
                aria-label="Đóng chi tiết"
                className="rounded p-1 text-text-muted hover:bg-surface-3 hover:text-text-primary"
                onClick={closeDialog}
              >
                <X size={16} />
              </button>
            </header>

            {detail.intents.map((intent) => {
              const fills = intent.broker_fills ?? [];
              const steps: [string, string | null][] = [
                ['Tín hiệu', intent.available_at != null ? formatTime(intent.available_at, true) : null],
                ['Canh giá', intent.created_at != null ? formatTime(intent.created_at, true) : null],
                [
                  'Kích hoạt',
                  intent.triggered_at != null
                    ? `${(intent.winner ?? '').toUpperCase()} · ${formatTime(intent.triggered_at, true)}`
                    : null,
                ],
                ['Gửi broker', intent.status === 'submitted' || fills.length ? 'Đã gửi · Không có thời gian ghi nhận' : null],
                [
                  'Khớp',
                  fills.length
                    ? fills.map((fill) => `#${fill.position_id} @ ${price(fill.price)}`).join(', ') + ' · Không có thời gian ghi nhận'
                    : null,
                ],
                ['Kết thúc', intent.ended_at != null ? formatTime(intent.ended_at, true) : null],
              ];

              return (
                <section key={intent.id} className="mt-4 space-y-3">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="rounded-md border border-border-base bg-surface-2 px-2 py-0.5 font-medium text-text-primary">
                      Thực thi: {entryLifecycle(intent).label}
                    </span>
                  </div>
                  {(() => {
                    const r = explainReason(intent);
                    if (!r) return null;
                    return (
                      <div className="rounded-md border border-amber-400/20 bg-amber-400/10 p-2 text-amber-200">
                        <div className="font-semibold">{r.label}</div>
                        <div className="text-[11px] text-amber-300/80">{r.hint}</div>
                        {r.detail && r.detail !== r.label && <div className="mt-1 font-mono text-[10px] text-amber-400/70 break-all">{r.detail}</div>}
                      </div>
                    );
                  })()}
                  <dl className="grid grid-cols-2 gap-2 font-mono tabular-nums">
                    <div className="rounded-md bg-sky-400/10 p-2">
                      <dt className="text-[10px] text-sky-300">LIMIT</dt>
                      <dd className="m-0 text-sm font-semibold text-text-primary">{price(intent.limit_price)}</dd>
                    </div>
                    <div className="rounded-md bg-sky-400/10 p-2">
                      <dt className="text-[10px] text-sky-300">STOP</dt>
                      <dd className="m-0 text-sm font-semibold text-text-primary">{price(intent.stop_price)}</dd>
                    </div>
                    <div className="rounded-md bg-amber-400/10 p-2"><dt className="text-amber-300">SL dự kiến</dt><dd className="m-0">{price(intent.signal?.stop)}</dd></div>
                    <div className="rounded-md bg-teal-400/10 p-2"><dt className="text-teal-300">TP dự kiến</dt><dd className="m-0">{price(intent.signal?.target)}</dd></div>
                  </dl>
                  <p>Nhánh kích hoạt: {intent.winner?.toUpperCase() ?? 'Chưa ghi nhận'} · Hạn canh: {intent.expires_at ? formatTime(intent.expires_at, true) : 'Không có thời gian ghi nhận'}</p>
                  <ol className="m-0 list-none space-y-1.5 p-0">
                    {steps.map(([label, info]) => (
                      <li
                        key={label}
                        className={`flex items-start gap-2 rounded-md border px-2 py-1.5 ${
                          info
                            ? 'border-emerald-800/40 bg-emerald-950/40 text-emerald-300'
                            : 'border-border-subtle text-text-muted'
                        }`}
                      >
                        <span className="w-20 shrink-0 font-medium">{label}</span>
                        <span className="min-w-0 break-words font-mono">{info ?? (label === 'Gửi broker' ? 'Chưa có xác nhận gửi' : label === 'Khớp' ? 'Chưa có fill xác nhận' : 'Không có thời gian ghi nhận')}</span>
                      </li>
                    ))}
                  </ol>
                  <p className="m-0 break-all font-mono text-[11px] text-text-muted" title={intent.reason ?? undefined}>
                    {intent.strategy}
                    {intent.reason ? ` · ${intent.reason}` : ''}
                  </p>
                </section>
              );
            })}

            {!detail.intents.length && (
              <p className="mt-4 text-text-muted">
                {shadowPending(detail)
                  ? 'V4 đang chạy mô phỏng đối chiếu · mức giá và trạng thái xem tại thanh snapshot sát chart; không gửi lệnh broker.'
                  : 'Chỉ ghi nhận tín hiệu · chưa có lệnh canh giá tương ứng; không phải fill broker.'}
              </p>
            )}

            <section className="mt-4 border-t border-border-base pt-3">
              <h4 className="m-0 mb-2 font-semibold text-text-primary">Cây ghi nhận ({detail.signals.length})</h4>
              <ul className="m-0 list-none space-y-1 p-0 font-mono text-[11px]">
                {detail.signals.map((signal) => (
                  <li key={`${signal.strategy}${signal.available_at}`} className="break-all text-text-secondary">
                    {signal.strategy} · {signal.strategy.startsWith('hast4_') ? 'Mô phỏng' : 'Tín hiệu'}: {({ waiting: 'Chờ kích hoạt', pending: 'Chờ xử lý', open: 'Đang mở', resolved: 'Hoàn tất', excluded: 'Đã loại', ambiguous: 'Chưa xác định', recorded: 'Đã ghi nhận' } as Record<string, string>)[signal.status] ?? signal.status}
                  </li>
                ))}
              </ul>
            </section>
          </div>
        )}
      </dialog>

    </>
  );
}
