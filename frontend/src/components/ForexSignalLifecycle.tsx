import { Check, X, ChevronLeft } from 'lucide-react';
import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import type { BrokerPosition, LearnerAnalysis } from '../types/exness-execution';
import type { ForexSignalSetup } from '../lib/forex-entry-lifecycle';
import type { ForexEntryIntent, ForexSignalChart } from '../types/forex-entry';
import { entryLifecycle } from '../lib/forex-entry-lifecycle';
import { ticketRole, ticketStatus } from '../lib/forex-position-groups';
import { chartTreeTag, treeLabel } from '../lib/forex-tree-label';
import { formatTime } from '../lib/time-format';
import { priceDigits, spreadText } from '../lib/forex-labels';
import { entrySlippage, executionLegs, executionSummary, executionTimeline } from '../lib/forex-execution-view';

interface ForexSignalLifecycleProps {
  open: boolean;
  initialTab?: 'positions' | 'setups';
  onTabChange: (tab: 'positions' | 'setups') => void;
  focusId?: string;
  symbol: string;
  positions: BrokerPosition[];
  selectedPositionId?: string;
  onSelectPosition: (id?: string) => void;
  pnlOf: (pos: BrokerPosition) => number | null;
  setups: ForexSignalSetup[];
  selectedSetupId?: string;
  onSelectSetup: (id: string | null, strategy?: string) => void;
  onClose: () => void;
  selectedV4Strategy?: string;
  strategies?: LearnerAnalysis['strategies'];
  entryStateLabels: Record<string, string>;
  hasMoreCandles?: boolean;
  loadingOlder?: boolean;
  onLoadOlder?: () => void;
  hasCandle: (time: number) => boolean;
}

export function ForexSignalLifecycle({
  open,
  initialTab = 'positions',
  onTabChange,
  focusId,
  symbol,
  positions,
  selectedPositionId,
  onSelectPosition,
  pnlOf,
  setups,
  selectedSetupId,
  onSelectSetup,
  onClose,
  selectedV4Strategy,
  strategies,
  entryStateLabels,
  hasMoreCandles,
  loadingOlder,
  onLoadOlder,
  hasCandle,
}: ForexSignalLifecycleProps) {
  const tab = initialTab;
  const [setupFilter, setSetupFilter] = useState<'pending' | 'history' | 'all'>('pending');
  const [detailedSetupId, setDetailedSetupId] = useState<string | null>(null);
  const dialogRef = useRef<HTMLDialogElement | null>(null);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const scrollPositions = useRef({ positions: 0, setups: 0 });
  const pendingFocus = useRef<string | undefined>(undefined);
  const detailTrigger = useRef<HTMLButtonElement | null>(null);
  const backRef = useRef<HTMLButtonElement | null>(null);
  const closeRef = useRef<HTMLButtonElement | null>(null);
  const [previewStrategy, setPreviewStrategy] = useState('');
  const [snapshot, setSnapshot] = useState<{ key: string; data?: ForexSignalChart; error?: string }>({ key: '' });
  const detailedSetup = setups.find((setup) => setup.id === detailedSetupId);
  const v4Signals = detailedSetup?.signals.filter((signal) => signal.strategy.startsWith('hast4_')) ?? [];
  const priceStrategies = [...new Set([...v4Signals.map((signal) => signal.strategy), ...(detailedSetup?.intents.map((intent) => intent.strategy) ?? [])])];
  const priceStrategy = priceStrategies.includes(previewStrategy) ? previewStrategy : priceStrategies[0];
  const v4Strategy = priceStrategy?.startsWith('hast4_') ? priceStrategy : undefined;
  const priceIntent = detailedSetup?.intents.find((intent) => intent.strategy === priceStrategy);
  const priceSignal = v4Signals.find((signal) => signal.strategy === priceStrategy);
  const snapshotKey = detailedSetup && v4Strategy ? `${symbol}:${detailedSetup.time}:${v4Strategy}` : '';
  const v4Chart = snapshot.key === snapshotKey ? snapshot.data : undefined;
  const v4Error = snapshot.key === snapshotKey ? snapshot.error : undefined;
  const priceLevels = v4Chart?.row.chart?.levels?.length ? v4Chart.row.chart.levels : priceIntent ? [
    { label: 'LIMIT ảo', price: priceIntent.limit_price },
    { label: 'STOP ENTRY ảo', price: priceIntent.stop_price },
    { label: 'SL dự kiến', price: priceIntent.signal?.stop },
    { label: 'TP dự kiến', price: priceIntent.signal?.target },
  ] : [];

  useEffect(() => {
    if (!open) setDetailedSetupId(null);
  }, [open]);

  useEffect(() => {
    if (detailedSetupId && !detailedSetup) setDetailedSetupId(null);
  }, [detailedSetupId, detailedSetup]);

  useEffect(() => {
    if (!open || !detailedSetup || !v4Strategy) return;
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 15_000);
    let active = true;
    setSnapshot({ key: snapshotKey });
    const query = new URLSearchParams({ symbol, strategy: v4Strategy, available_at: String(detailedSetup.time) });
    fetch(`/api/workspaces/exness_forex/learning/signal-chart?${query}`, { signal: controller.signal })
      .then(async (response) => {
        const body = await response.json();
        if (!response.ok) throw new Error(body.detail ?? 'Không tải được snapshot V4');
        if (body.symbol !== symbol || body.strategy !== v4Strategy || body.available_at !== detailedSetup.time)
          throw new Error('Snapshot không khớp setup đang xem');
        if (active) setSnapshot({ key: snapshotKey, data: body });
      })
      .catch((error) => {
        if (active) setSnapshot({ key: snapshotKey, error: error.name === 'AbortError' ? 'Tải snapshot quá hạn' : error.message });
      })
      .finally(() => clearTimeout(timeout));
    return () => { active = false; controller.abort(); clearTimeout(timeout); };
  }, [open, symbol, detailedSetup?.time, v4Strategy, snapshotKey]);

  useLayoutEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if (!open) return;
    const opener = document.activeElement as HTMLElement | null;
    const previousOverflow = document.body.style.overflow;
    dialog.showModal();
    document.body.style.overflow = 'hidden';
    closeRef.current?.focus();
    pendingFocus.current = focusId;
    return () => {
      dialog.close();
      document.body.style.overflow = previousOverflow;
      if (opener?.isConnected) opener.focus({ preventScroll: true });
    };
  }, [open]);

  useLayoutEffect(() => {
    if (!open || !scrollRef.current) return;
    if (tab === 'setups' && pendingFocus.current && !filteredSetups.some((setup) => setup.id === pendingFocus.current)
      && setups.some((setup) => setup.id === pendingFocus.current)) {
      setSetupFilter('all');
      return;
    }
    scrollRef.current.scrollTop = detailedSetup ? 0 : scrollPositions.current[tab];
    if (detailedSetup) backRef.current?.focus();
    else if (pendingFocus.current) {
      const row = [...scrollRef.current.querySelectorAll<HTMLElement>('[data-item-id]')]
        .find((element) => element.dataset.itemId === pendingFocus.current);
      row?.scrollIntoView({ block: 'nearest' });
      row?.querySelector<HTMLElement>('button')?.focus({ preventScroll: true });
      pendingFocus.current = undefined;
    } else if (detailTrigger.current?.isConnected) {
      detailTrigger.current.focus({ preventScroll: true });
      detailTrigger.current = null;
    }
  }, [open, tab, detailedSetup?.id, setupFilter]);

  const shadowPending = (setup: ForexSignalSetup) =>
    setup.signals.some((s) => s.strategy.startsWith('hast4_') && ['waiting', 'pending', 'open'].includes(s.status));
  const setupPending = (setup: ForexSignalSetup) =>
    shadowPending(setup) || setup.intents.some((i) => ['waiting', 'triggered', 'submitting', 'submitted', 'uncertain'].includes(i.status));

  const filteredSetups = setups.filter((s) => (setupFilter === 'all' ? true : setupFilter === 'pending' ? setupPending(s) : !setupPending(s)));
  const pendingSetupCount = setups.filter(setupPending).length;

  const price = (value: number | null | undefined) =>
    value == null || !Number.isFinite(value) ? '—' : value.toFixed(priceDigits(symbol));

  const reasonCatalog: Record<string, { label: string; hint: string }> = {
    poor_rr: { label: 'R/R kém', hint: 'Dưới 1.5R sau chi phí' },
    sl_too_tight: { label: 'SL quá sát', hint: 'Khoảng cách SL dưới ngưỡng ATR của phiên bản chiến thuật' },
    sl_too_wide: { label: 'SL quá xa', hint: 'Vượt 3 ATR H1' },
    price_drift: { label: 'Giá lệch quá xa', hint: 'Giá hiện tại lệch quá 0.25R so với giá tham chiếu' },
    virtual_entry_expired: { label: 'Entry ảo không còn hợp lệ', hint: 'Quote không đủ mới hoặc vượt hạn canh' },
    invalid_htf_levels: { label: 'Mức HTF không hợp lệ', hint: 'Thiếu hoặc lỗi cấu trúc TP/SL' },
    quote_gap: { label: 'Mất nhịp giá', hint: 'Không có quote quá 60 giây' },
    quote_stale: { label: 'Giá quá cũ', hint: 'Dữ liệu quote không đủ mới' },
    level_crossed_during_gap: { label: 'Giá chạm mức khi mất tick', hint: 'Không giả định đã khớp; không vào đuổi' },
    level_already_crossed: { label: 'Giá đã vượt mức chờ', hint: 'Không thể vào đuổi' },
    entry_expired: { label: 'Quá thời hạn chờ', hint: 'Hết thời gian canh nến' },
    worker_restart: { label: 'Worker khởi động lại', hint: 'Hủy canh để đảm bảo an toàn' },
    preflight_failed: { label: 'Không qua kiểm tra trước gửi', hint: 'Bản ghi chưa lưu nguyên nhân chi tiết; chưa xác định kiểm tra nào đã chặn lệnh.' },
    trigger_preflight_failed: { label: 'Canh giá đã hủy; thiếu lý do chi tiết', hint: 'Bản ghi chưa lưu kết quả executor. Chưa có xác nhận gửi broker; không suy diễn nguyên nhân.' },
    history_catching_up: { label: 'Đang đồng bộ lịch sử', hint: 'Executor chờ đồng bộ xong lịch sử giao dịch trước khi gửi lệnh.' },
    executor_active: { label: 'Executor đang bận', hint: 'Một tác vụ khác đang giữ khóa thực thi nên hủy để tránh gửi trùng.' },
    calibration_or_executor_active: { label: 'Executor đang bận', hint: 'Đang hiệu chỉnh hoặc thực thi tác vụ khác nên hủy để tránh gửi trùng.' },
    session_close_near: { label: 'Gần giờ đóng phiên', hint: 'Không mở lệnh mới sát giờ thị trường nghỉ.' },
    unverified_virtual_entry: { label: 'Không xác minh được entry ảo', hint: 'Quote hoặc dữ liệu setup không khớp bản ghi khi kích hoạt.' },
    position_active: { label: 'Đã có vị thế đang mở', hint: 'Giới hạn vị thế hiện tại chưa cho phép mở thêm lệnh.' },
    costs_unverified: { label: 'Chi phí chưa xác thực', hint: 'Spread, phí hoặc thông số sàn chưa đủ mới.' },
    reconciling: { label: 'Đang đối soát lệnh trước đó', hint: 'Có lệnh chưa rõ trạng thái nên không gửi thêm.' },
    paused: { label: 'Thực thi đang tạm dừng', hint: 'Bot không gửi lệnh mới khi đang tạm dừng.' },
    disabled: { label: 'Thực thi đang tắt', hint: 'Cấu hình hiện tại không cho phép executor chạy.' },
    waiting_signal: { label: 'Chưa có tín hiệu đủ điều kiện gửi', hint: 'Executor không nhận được tín hiệu đã xác nhận phù hợp.' },
    position_capacity_for_split: { label: 'Không đủ chỗ cho các nhánh lệnh', hint: 'Số vị thế còn trống không đủ cho kế hoạch chia lệnh.' },
    symbol_disabled: { label: 'Cặp tiền không được bật', hint: 'Cặp tiền không nằm trong danh sách thực thi.' },
    shadow_only_signal: { label: 'Tín hiệu chỉ mô phỏng', hint: 'Tín hiệu không được phép gửi lệnh thật.' },
    entry_pilot_disabled: { label: 'Pilot entry đang tắt', hint: 'Cấu hình thực thi entry ảo đang tắt.' },
    v2_execution_disabled: { label: 'Thực thi HTF đang tắt', hint: 'Cấu hình thực thi HTF hoặc demo đang tắt.' },
    execution_error: { label: 'Executor gặp lỗi', hint: 'Lỗi xảy ra trước khi xác nhận gửi lệnh.' },
    ambiguous_entry: { label: 'Không rõ nhánh chạm trước', hint: 'Dữ liệu không đủ xác định thứ tự kích hoạt' },
    capacity_unavailable: { label: 'Không đủ chỗ mở lệnh', hint: 'Giới hạn số vị thế đồng thời' },
    invalidated: { label: 'Setup mất hiệu lực', hint: 'Cấu trúc thị trường đã thay đổi' },
  };

  const explainReason = (intent?: ForexEntryIntent | null) => {
    if (!intent?.reason) return null;
    const code = intent.reason_code || (!['preflight_failed', 'trigger_preflight_failed'].includes(intent.reason) ? intent.reason : null);
    const mapped = code ? reasonCatalog[code] : reasonCatalog[intent.reason];
    if (mapped) return { code: code || intent.reason, label: mapped.label, hint: mapped.hint, detail: intent.reason_detail };
    return {
      code: code || intent.reason,
      label: 'Kiểm tra trước gửi không đạt',
      hint: 'Chưa có diễn giải cho mã này; xem chi tiết kỹ thuật.',
      detail: intent.reason_detail || code || intent.reason,
    };
  };

  return (
    <dialog
      ref={dialogRef}
      aria-label="Danh sách vị thế và setup"
      onCancel={(event) => { event.preventDefault(); onClose(); }}
      onClick={(e) => {
        if (e.target === dialogRef.current) onClose();
      }}
      className="forex-inspector fixed inset-y-0 right-0 left-auto m-0 h-dvh max-h-none w-[min(560px,100vw)] max-w-none border-0 border-l border-border-active bg-surface-1 p-0 text-xs text-text-secondary shadow-2xl backdrop:bg-black/60 backdrop:backdrop-blur-sm open:flex open:flex-col open:animate-in open:slide-in-from-right duration-300 motion-reduce:animate-none"
    >
      <header className="flex shrink-0 items-center justify-between border-b border-border-base px-4 py-3">
        <div className="flex items-center gap-2">
          {detailedSetup ? (
            <button
              ref={backRef}
              type="button"
              className="inline-flex items-center gap-1 rounded px-2 py-1 text-xs text-text-secondary hover:bg-surface-2 hover:text-text-primary"
              onClick={() => setDetailedSetupId(null)}
            >
              <ChevronLeft size={14} /> Quay lại
            </button>
          ) : (
            <div role="group" aria-label="Danh mục chart" className="flex items-center gap-1 rounded-lg border border-border-base bg-surface-2/60 p-0.5">
              <button
                type="button"
                aria-pressed={tab === 'positions'}
                onClick={() => onTabChange('positions')}
                className={`rounded-md px-3 py-1 font-medium transition-colors ${
                  tab === 'positions' ? 'bg-surface-1 font-semibold text-text-primary shadow-sm' : 'text-text-muted hover:text-text-primary'
                }`}
              >
                Vị thế ({positions.length})
              </button>
              <button
                type="button"
                aria-pressed={tab === 'setups'}
                onClick={() => onTabChange('setups')}
                className={`rounded-md px-3 py-1 font-medium transition-colors ${
                  tab === 'setups' ? 'bg-surface-1 font-semibold text-text-primary shadow-sm' : 'text-text-muted hover:text-text-primary'
                }`}
              >
                Setup ({setups.length})
              </button>
            </div>
          )}
        </div>
        <button
          ref={closeRef}
          type="button"
          aria-label="Đóng danh sách"
          className="rounded p-1 text-text-muted hover:bg-surface-3 hover:text-text-primary"
          onClick={onClose}
        >
          <X size={16} />
        </button>
      </header>

      <div ref={scrollRef} data-inspector-scroll onScroll={(event) => {
        if (!detailedSetup) scrollPositions.current[tab] = event.currentTarget.scrollTop;
      }} className="min-h-0 min-w-0 flex-1 overflow-y-auto overscroll-y-contain p-4">
        {detailedSetup ? (
          <article className="space-y-4">
            <header className="flex flex-wrap items-center justify-between gap-2 border-b border-border-base pb-3">
              <div className="flex items-center gap-2">
                <strong
                  className={`rounded px-1.5 py-0.5 text-[11px] font-bold ${
                    detailedSetup.side === 'long' ? 'bg-bull/15 text-bull' : 'bg-bear/15 text-bear'
                  }`}
                >
                  {detailedSetup.side === 'long' ? 'MUA' : 'BÁN'}
                </strong>
                <span className="font-mono text-text-primary">{symbol} · {formatTime(detailedSetup.time, true)}</span>
              </div>
              <button
                type="button"
                className={`rounded-md border px-2.5 py-1 text-xs font-medium transition-colors ${
                  detailedSetup.id === selectedSetupId
                    ? 'border-sky-400/40 bg-sky-400/10 text-sky-200'
                    : 'border-border-base bg-surface-2 text-text-primary hover:border-border-active'
                }`}
                onClick={() => {
                  onSelectSetup(detailedSetup.id, v4Strategy);
                  onClose();
                }}
              >
                {detailedSetup.id === selectedSetupId ? '✓ Đang hiển thị trên chart' : 'Xem trên chart'}
              </button>
            </header>

            {detailedSetup.intents.map((intent) => {
              const lifecycle = entryLifecycle(intent);
              const legs = executionLegs(intent, positions, pnlOf);
              const sum = executionSummary(legs);
              const slip = entrySlippage(intent, sum.averagePrice);
              const steps = executionTimeline(intent, legs);
              const dated = steps.find((step) => step.time != null);
              const r = explainReason(intent);
              const filled = legs.length > 0;
              const statusTone = lifecycle.tone === 'filled' ? 'border-bull/30 bg-bull/10 text-bull' : lifecycle.tone === 'cancelled' ? 'border-border-active bg-surface-2 text-text-secondary' : lifecycle.tone === 'uncertain' ? 'border-amber-400/30 bg-amber-400/10 text-amber-200' : 'border-sky-400/30 bg-sky-400/10 text-sky-200';
              const money = (v: number | null) => (v == null ? '—' : `${v > 0 ? '+' : ''}${v.toFixed(2)}`);
              const tone = (v: number | null) => (v == null || v === 0 ? 'text-text-primary' : v > 0 ? 'text-bull' : 'text-bear');
              const metrics: { label: string; value: string; cls?: string }[] = filled ? [
                { label: 'Giá vào TB', value: price(sum.averagePrice) },
                { label: 'Trượt giá', value: slip == null ? '—' : `${slip > 0 ? '+' : ''}${spreadText(slip, symbol)}`, cls: slip == null || slip <= 0 ? '' : 'text-amber-300' },
                { label: 'Tổng lot', value: sum.lots == null ? '—' : sum.lots.toFixed(2) },
                { label: 'P&L', value: sum.pnl == null ? '—' : `${money(sum.pnl)} USD`, cls: tone(sum.pnl) },
              ] : [
                { label: 'Nhánh', value: intent.winner?.toUpperCase() ?? 'Chưa kích hoạt' },
                { label: 'Giá chờ', value: price(intent.limit_price ?? intent.stop_price) },
                { label: 'Hạn canh', value: intent.expires_at ? formatTime(intent.expires_at, true) : '—' },
              ];

              return (
                <section key={intent.id} className="flex flex-col gap-3 rounded-lg border border-border-base bg-surface-2/40 p-3.5" aria-label="Kết quả thực thi">
                  <div className="flex flex-wrap items-start justify-between gap-2">
                    <div className="min-w-0">
                      <h4 className="m-0 flex flex-wrap items-center gap-2 text-sm font-semibold text-text-primary">
                        Kết quả thực thi
                        <span className="text-xs font-normal text-text-muted">
                          {intent.side === 'long' ? 'MUA' : 'BÁN'} {symbol}{intent.winner ? ` · ${intent.winner[0].toUpperCase()}${intent.winner.slice(1)}` : ''}
                        </span>
                      </h4>
                      <p className="m-0 mt-0.5 break-all font-mono text-[10px] text-text-muted">{intent.strategy}</p>
                    </div>
                    <span className={`shrink-0 rounded-md border px-2 py-1 text-xs font-medium ${statusTone}`}>{lifecycle.label}</span>
                  </div>
                  {r && (
                    <div className="rounded-md border border-border-active bg-surface-1 p-2.5">
                      <div className="font-semibold text-text-primary">{r.label}</div>
                      <div className="mt-0.5 text-[11px] text-text-muted">{r.hint}</div>
                      {r.detail && r.detail !== r.label && <div className="mt-1 break-all font-mono text-[10px] text-text-muted">Mã: {r.detail}</div>}
                    </div>
                  )}
                  <dl className={`m-0 grid grid-cols-2 gap-2 ${filled ? 'sm:grid-cols-4' : 'sm:grid-cols-3'}`}>
                    {metrics.map((m) => (
                      <div key={m.label} className="min-w-0 rounded-md border border-border-subtle bg-surface-1 px-2.5 py-2">
                        <dt className="text-[10px] uppercase tracking-wide text-text-muted">{m.label}</dt>
                        <dd className={`m-0 mt-0.5 break-words font-mono text-xs font-semibold ${m.cls || 'text-text-primary'}`}>{m.value}</dd>
                      </div>
                    ))}
                  </dl>
                  {filled && (
                    <div className="overflow-hidden rounded-md border border-border-subtle">
                      <table className="w-full border-collapse text-xs">
                        <thead>
                          <tr className="bg-surface-1 text-left text-[10px] uppercase tracking-wide text-text-muted">
                            <th className="px-2.5 py-1.5 font-medium">Vị thế</th>
                            <th className="px-2.5 py-1.5 text-right font-medium">Lot</th>
                            <th className="px-2.5 py-1.5 text-right font-medium">Giá vào</th>
                            <th className="px-2.5 py-1.5 text-right font-medium">P&L</th>
                          </tr>
                        </thead>
                        <tbody>
                          {legs.map((leg) => (
                            <tr
                              key={leg.positionId}
                              className={`border-t border-border-subtle ${leg.position ? 'cursor-pointer hover:bg-surface-1' : ''}`}
                              onClick={leg.position ? () => onSelectPosition(leg.positionId) : undefined}
                            >
                              <td className="px-2.5 py-2">
                                <div className="flex flex-wrap items-center gap-1.5">
                                  <span className="font-medium text-text-primary">{leg.role}</span>
                                  <span className={`rounded px-1.5 py-px text-[10px] ${leg.status === 'open' ? 'bg-sky-400/10 text-sky-200' : 'bg-surface-3 text-text-muted'}`}>
                                    {leg.status === 'open' ? 'Đang mở' : leg.status === 'closed' ? 'Đã đóng' : 'Chưa đồng bộ'}
                                  </span>
                                </div>
                                <div className="font-mono text-[10px] text-text-muted">#{leg.positionId}</div>
                              </td>
                              <td className="px-2.5 py-2 text-right font-mono">{leg.lots == null ? '—' : leg.lots.toFixed(2)}</td>
                              <td className="px-2.5 py-2 text-right font-mono">{price(leg.price)}</td>
                              <td className={`px-2.5 py-2 text-right font-mono font-semibold ${tone(leg.pnl)}`}>{money(leg.pnl)}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                  <div>
                    <div className="mb-2 flex items-baseline justify-between text-[10px] uppercase tracking-wide text-text-muted">
                      <span>Dòng thời gian</span>
                      {dated?.time != null && <span className="normal-case">{formatTime(dated.time, false)}</span>}
                    </div>
                    <ol className="m-0 list-none space-y-2.5 p-0" aria-label="Dòng thời gian thực thi">
                      {steps.map((step, index) => (
                        <li key={step.label} className="relative grid grid-cols-[14px_84px_minmax(0,1fr)] items-baseline gap-x-3">
                          {index < steps.length - 1 && <span className="absolute top-2.5 left-[4.5px] h-[calc(100%+10px)] w-px bg-border-base" aria-hidden="true" />}
                          <span className={`relative size-2.5 self-center rounded-full border ${step.done ? 'border-emerald-400 bg-emerald-400/50' : 'border-border-active bg-surface-1'}`} aria-hidden="true" />
                          <span className={`text-xs ${step.done ? 'font-medium text-text-primary' : 'text-text-muted'}`}>{step.label}</span>
                          <span className="min-w-0 break-words text-xs text-text-secondary">
                            {step.time != null && <span className="font-mono">{formatTime(step.time, true, false).slice(-8)}</span>}
                            {step.gap != null && step.gap > 0 && <span className="ml-1.5 font-mono text-[10px] text-text-muted">+{step.gap}s</span>}
                            {step.detail && <span className={step.time != null ? 'ml-2 text-text-muted' : ''}>{step.detail}</span>}
                            {step.time == null && step.missingTime && !step.detail && <span className="text-text-muted">—</span>}
                          </span>
                        </li>
                      ))}
                    </ol>
                  </div>
                </section>
              );
            })}

            {priceStrategies.length > 0 && (
              <section className="flex flex-col gap-3 rounded-lg border border-border-base bg-surface-2/40 p-3.5" aria-label="Kế hoạch giá V4">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div>
                    <h4 className="m-0 text-sm font-semibold text-text-primary">Kế hoạch giá{v4Strategy ? ' V4' : ''}</h4>
                    <p className="m-0 mt-0.5 text-[11px] text-text-muted">Mức mô phỏng, không phải lệnh broker.</p>
                  </div>
                  {priceStrategies.length > 1 ? (
                    <select
                      aria-label="Chọn cây xem mức giá"
                      className="max-w-full rounded border border-border-active bg-surface-1 px-2 py-1 text-xs text-text-primary"
                      value={priceStrategy}
                      onChange={(event) => setPreviewStrategy(event.target.value)}
                    >
                      {priceStrategies.map((strategy) => (
                        <option key={strategy} value={strategy}>
                          {strategy.replace('hast4_', '#').slice(0, 14)}
                        </option>
                      ))}
                    </select>
                  ) : (
                    <span className="font-mono text-[11px] text-text-muted">
                      {priceStrategy?.replace('hast4_', '#').slice(0, 10)}
                    </span>
                  )}
                </div>
                {priceSignal && <p className="m-0 text-[11px] text-sky-300">Mô phỏng: {entryStateLabels[priceSignal.status] ?? priceSignal.status}</p>}
                {v4Error && <p role="status" className="m-0 text-[11px] text-amber-200">{v4Error}{priceIntent ? ' · Hiển thị mức giá canh đã lưu thay thế.' : ''}</p>}
                {priceLevels.length ? (
                  <>
                    <dl className="m-0 grid grid-cols-1 gap-x-4 gap-y-2 rounded-md border border-border-subtle bg-surface-1 p-2.5 text-[11px] sm:grid-cols-2 [&_dd]:m-0 [&_dd]:mt-0.5 [&_dd]:font-mono [&_dd]:text-text-primary [&_dt]:text-text-muted">
                      <div><dt className="text-[10px] uppercase tracking-wide">{v4Chart ? 'Chụp tín hiệu' : 'Canh giá đã lưu'}</dt><dd className="break-words">{(v4Chart?.available_at ?? priceIntent?.available_at) != null ? formatTime((v4Chart?.available_at ?? priceIntent?.available_at)!, true) : 'Không có thời gian ghi nhận'}</dd></div>
                      <div><dt className="text-[10px] uppercase tracking-wide">Hạn canh</dt><dd className="break-words">{(v4Chart?.expires_at ?? priceIntent?.expires_at) != null ? formatTime((v4Chart?.expires_at ?? priceIntent?.expires_at)!, true) : 'Không có thời gian ghi nhận'}</dd></div>
                    </dl>
                    <ul className="m-0 grid list-none grid-cols-2 gap-2 p-0 font-mono">
                      {priceLevels.map((level) => {
                        const label = level.label.split(' · ')[0];
                        const tone = /SL/i.test(label)
                          ? 'border-amber-400/30 text-amber-300'
                          : /TP/i.test(label)
                            ? 'border-teal-400/30 text-teal-300'
                            : 'border-sky-400/30 text-sky-300';
                        return (
                          <li key={level.label} className={`flex min-w-0 flex-col rounded-md border bg-surface-1 px-2.5 py-2 ${tone}`}>
                            <span className="text-[10px] uppercase tracking-wide">{label}</span>
                            <strong className="break-words text-sm text-text-primary tabular-nums">{price(level.price)}</strong>
                          </li>
                        );
                      })}
                    </ul>
                  </>
                ) : (
                  <p className="m-0 text-[11px]">{v4Error ? 'Chưa có mức giá đã lưu để thay thế.' : 'Đang tải snapshot tín hiệu đã chọn…'}</p>
                )}
              </section>
            )}

            <section className="flex flex-col gap-3 rounded-lg border border-border-base bg-surface-2/40 p-3.5" aria-label="Chỉ báo và mức giá setup">
              <div>
                <h4 className="m-0 text-sm font-semibold text-text-primary">Bối cảnh tín hiệu</h4>
                <p className="m-0 mt-0.5 text-[11px] text-text-muted">Điều kiện cây dùng khi chụp tín hiệu.</p>
              </div>
              {detailedSetup.signals.map(({ strategy }) => {
                const isSnapshot = strategy === v4Chart?.strategy;
                const row = isSnapshot ? v4Chart.row : strategies?.[strategy];
                const overlay = row?.chart?.overlays;
                const indicators = overlay ? [...new Set(overlay.lines.map((line) => line.label)), ...overlay.notes] : [];
                return (
                  <article key={strategy} className="min-w-0 space-y-3 rounded-md border border-border-subtle bg-surface-1 p-3 text-[11px]">
                    <div className="flex min-w-0 items-start justify-between gap-3">
                      <div className="min-w-0">
                        <strong className="block break-words text-xs text-text-primary">{treeLabel(row, strategy).full}</strong>
                        <span className="mt-0.5 block font-mono text-[10px] text-text-muted">{chartTreeTag(row, strategy)}</span>
                      </div>
                      <span className="shrink-0 rounded bg-surface-3 px-1.5 py-0.5 text-[10px] text-text-muted">{isSnapshot ? 'Snapshot' : 'Dữ liệu hiện tại'}</span>
                    </div>
                    <p className="m-0 text-text-muted">{isSnapshot ? 'Snapshot của tín hiệu đang xem' : 'Dữ liệu cây hiện tại · không phải snapshot lịch sử'}</p>
                    {indicators.length ? (
                      <ul className="m-0 flex list-none flex-wrap gap-2 p-0" aria-label="Chỉ báo">
                        {indicators.map((item) => <li key={item} className="max-w-full break-words rounded border border-border-base bg-surface-2 px-2 py-1 text-text-secondary">{item}</li>)}
                      </ul>
                    ) : <p className="m-0 text-text-muted">Chưa có dữ liệu chỉ báo của cây</p>}
                    {[row?.captured_zone, row?.zone].map((zone, index) => zone && (
                      <dl key={index} className="m-0 grid grid-cols-[88px_minmax(0,1fr)] gap-x-3 gap-y-1.5 border-t border-border-subtle pt-3 [&_dd]:m-0 [&_dd]:break-words [&_dd]:text-text-secondary [&_dt]:text-text-muted">
                        <dt>{index === 0 ? 'Đã chụp' : 'Hiện tại'}</dt><dd>{zone.family} · {zone.side === 'long' ? 'LONG' : 'SHORT'}</dd>
                        {zone.target_reason && <><dt>Mục tiêu</dt><dd>{zone.target_reason}</dd></>}
                      </dl>
                    ))}
                  </article>
                );
              })}
              {!detailedSetup.signals.length && <p className="m-0 text-text-muted">Chưa có dữ liệu chỉ báo của cây</p>}
            </section>

            {!hasCandle(detailedSetup.time) && (
              <div className="rounded-lg border border-amber-400/20 bg-amber-400/10 p-2.5 text-xs text-amber-200">
                <p className="m-0">Nến của setup đã chọn chưa có trong dữ liệu chart.</p>
                {hasMoreCandles && (
                  <button
                    type="button"
                    disabled={loadingOlder}
                    onClick={onLoadOlder}
                    className="mt-1.5 rounded border border-amber-400/40 bg-amber-400/20 px-2 py-0.5 text-[11px] font-semibold text-amber-100"
                  >
                    Thêm lịch sử nến
                  </button>
                )}
              </div>
            )}

            {!detailedSetup.intents.length && (
              <p className="text-text-muted">
                {shadowPending(detailedSetup)
                  ? 'V4 đang chạy mô phỏng đối chiếu · mức giá và trạng thái hiển thị bên trên; không gửi lệnh broker.'
                  : 'Chỉ ghi nhận tín hiệu · chưa có lệnh canh giá tương ứng; không phải fill broker.'}
              </p>
            )}

            <section className="border-t border-border-base pt-3">
              <h4 className="m-0 mb-2 font-semibold text-text-primary">Cây ghi nhận ({detailedSetup.signals.length})</h4>
              <ul className="m-0 list-none space-y-1 p-0 font-mono text-[11px]">
                {detailedSetup.signals.map((sig) => (
                  <li key={`${sig.strategy}${sig.available_at}`} className="break-all text-text-secondary">
                    {sig.strategy} · {sig.strategy.startsWith('hast4_') ? 'Mô phỏng' : 'Tín hiệu'}: {entryStateLabels[sig.status] ?? sig.status}
                  </li>
                ))}
              </ul>
            </section>
          </article>
        ) : tab === 'positions' ? (
          <div className="space-y-2" role="group" aria-label="Danh sách vị thế broker">
            {!positions.length && (
              <p className="rounded-lg border border-dashed border-border-base p-6 text-center text-text-muted">
                Không có ticket phù hợp trong dữ liệu chart hiện tại.
              </p>
            )}
            {positions.map((pos) => {
              const selected = pos.position_id === selectedPositionId;
              const pnl = pos.status === 'closed' ? pos.pnl : pnlOf(pos);
              const closed = pos.status === 'closed';
              const roleLabel = ticketRole(pos);
              const tone = !closed
                ? 'border-border-base bg-surface-1'
                : pos.take_profit_filled
                  ? 'border-bull/40 bg-bull/10'
                  : pos.close_reason === 'stop_loss'
                    ? 'border-bear/40 bg-bear/5'
                    : 'border-amber-400/40 bg-amber-400/5';
              return (
                <div data-item-id={pos.position_id} key={pos.position_id}>
                <button
                  type="button"
                  className={`broker-position-chip flex w-full flex-col gap-2 rounded-lg border p-3 text-left transition-colors hover:border-border-active focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sky-400 ${tone} ${
                    selected ? 'ring-2 ring-sky-400/60' : ''
                  }`}
                  aria-pressed={selected}
                  aria-label={`Chọn ticket #${pos.position_id} · ${roleLabel}`}
                  onClick={() => {
                    if (!selected) onSelectPosition(pos.position_id);
                    onClose();
                  }}
                >
                  <div className="flex items-center gap-2">
                    <span
                      className={`rounded px-1.5 py-0.5 text-[10px] font-bold ${
                        pos.side === 'buy' ? 'bg-bull/10 text-bull' : 'bg-bear/10 text-bear'
                      }`}
                    >
                      {pos.side?.toUpperCase() ?? '—'}
                    </span>
                    <span className="font-mono text-sm font-semibold text-text-primary">#{pos.position_id}</span>
                    <span className="ml-auto rounded bg-surface-3 px-1.5 py-0.5 text-[10px] text-text-secondary">{roleLabel}</span>
                  </div>
                  <div className="flex items-baseline justify-between gap-3">
                    <span className="text-text-muted">{closed ? 'Đã đóng hết' : `${pos.remaining_volume ?? pos.volume ?? '—'} lot`}</span>
                    <strong
                      className={`font-mono text-sm tabular-nums ${
                        pnl == null ? 'text-text-muted' : pnl >= 0 ? 'text-bull' : 'text-bear'
                      }`}
                    >
                      {pnl == null ? '— USD' : `${pnl > 0 ? '+' : ''}${pnl.toFixed(2)} USD`}
                    </strong>
                  </div>
                  <div className="flex items-center justify-between text-[11px]">
                    <span
                      className={`rounded px-1.5 py-0.5 font-medium ${
                        closed
                          ? pos.take_profit_filled
                            ? 'bg-bull/15 text-bull'
                            : pos.close_reason === 'stop_loss'
                              ? 'bg-bear/10 text-bear'
                              : 'bg-amber-400/10 text-amber-300'
                          : 'bg-surface-3 text-text-secondary'
                      }`}
                    >
                      {ticketStatus(pos)}
                    </span>
                    {selected && <span className="font-semibold text-sky-300">✓ Đang hiển thị trên chart</span>}
                  </div>
                  <span className="text-[11px] text-text-muted">{closed ? 'P&L đã chốt · chưa phí/swap' : 'P&L thả nổi ước tính'}</span>
                </button>
                </div>
              );
            })}
          </div>
        ) : (
          <div className="space-y-3" role="region" aria-label={`Vòng đời tín hiệu ${symbol}`}>
            <div className="flex flex-wrap items-center gap-1" role="group" aria-label="Lọc setup">
              {(
                [
                  ['pending', 'Đang canh / xử lý', pendingSetupCount],
                  ['history', 'Lịch sử', setups.length - pendingSetupCount],
                  ['all', 'Tất cả', setups.length],
                ] as const
              ).map(([val, label, count]) => (
                <button
                  type="button"
                  key={val}
                  aria-pressed={setupFilter === val}
                  onClick={() => setSetupFilter(val)}
                  className={`inline-flex min-h-8 items-center gap-1.5 rounded-lg border px-2.5 py-1 text-xs font-medium transition-colors ${
                    setupFilter === val
                      ? 'border-sky-400/30 bg-sky-400/10 font-semibold text-sky-200'
                      : 'border-transparent text-text-muted hover:bg-surface-2 hover:text-text-primary'
                  }`}
                >
                  {label}
                  <span className="rounded bg-surface-3 px-1.5 font-mono text-[10px] text-text-secondary">{count}</span>
                </button>
              ))}
            </div>

            <div className="space-y-2">
              {!filteredSetups.length && (
                <p className="rounded-lg border border-dashed border-border-base p-6 text-center text-text-muted">
                  Không có setup nào trong bộ lọc này.
                </p>
              )}
              {filteredSetups.map((setup) => {
                const isSelected = setup.id === selectedSetupId;
                const mainIntent = setup.intents[0];
                const execState = mainIntent ? entryLifecycle(mainIntent) : null;
                const isSimulating = shadowPending(setup);
                const sideVn = setup.side === 'long' ? 'MUA' : 'BÁN';
                const v4Count = setup.signals.filter((s) => s.strategy.startsWith('hast4_')).length;
                const v3Count = setup.signals.filter((s) => !s.strategy.startsWith('hast4_')).length;
                const tagParts: string[] = [];
                if (v4Count > 0) tagParts.push(`V4 ×${v4Count}`);
                if (v3Count > 0) tagParts.push(`V3 ×${v3Count}`);
                const r = explainReason(mainIntent);

                return (
                  <article
                    key={setup.id}
                    data-item-id={setup.id}
                    className={`signal-lifecycle-row flex min-w-0 items-stretch rounded-lg border transition-colors ${
                      isSelected
                        ? 'is-selected border-sky-400/60 bg-sky-400/5 ring-1 ring-sky-400/20'
                        : 'border-border-base bg-canvas/40 hover:border-border-active hover:bg-surface-2'
                    }`}
                  >
                    <button
                      type="button"
                      className="signal-lifecycle-select flex min-w-0 flex-1 flex-col gap-1.5 p-3 text-left"
                      onClick={() => {
                        if (!isSelected) onSelectSetup(setup.id);
                        onClose();
                      }}
                      aria-pressed={isSelected}
                    >
                      <div className="flex min-w-0 items-center gap-2 text-xs">
                        {isSelected && <Check size={14} className="text-sky-300" />}
                        <strong
                          className={`rounded px-1.5 py-0.5 text-[11px] font-bold ${
                            setup.side === 'long' ? 'bg-bull/15 text-bull' : 'bg-bear/15 text-bear'
                          }`}
                        >
                          {sideVn}
                        </strong>
                        <span className="truncate font-mono text-text-muted">{tagParts.join(' · ')}</span>
                        <time className="ml-auto shrink-0 font-mono text-[11px] text-text-muted tabular-nums">
                          {formatTime(setup.time)}
                        </time>
                      </div>
                      <div className="flex flex-wrap items-center gap-1.5 text-[11px]">
                        {execState ? (
                          <span className="rounded-md border border-border-subtle bg-surface-2 px-1.5 py-0.5 font-medium text-text-secondary">
                            Thực thi: {execState.short}
                          </span>
                        ) : (
                          <span className="rounded-md border border-border-subtle bg-surface-2 px-1.5 py-0.5 text-text-muted">
                            Ghi nhận
                          </span>
                        )}
                        {v4Count > 0 && (
                          <span className="rounded bg-sky-400/10 px-1.5 py-0.5 font-medium text-sky-300">
                            Mô phỏng: {isSimulating ? 'Đang chạy' : 'Đã ghi nhận'}
                          </span>
                        )}
                        {r && <span className="truncate text-amber-400/90">{r.label}</span>}
                      </div>
                    </button>
                    <button
                      type="button"
                      className="signal-lifecycle-detail-btn m-2 shrink-0 self-center rounded-md border border-border-base bg-surface-2 px-2.5 py-1 text-xs text-text-secondary hover:border-border-active hover:text-text-primary"
                      onClick={(event) => { detailTrigger.current = event.currentTarget; setPreviewStrategy(setup.id === selectedSetupId ? selectedV4Strategy ?? '' : ''); setDetailedSetupId(setup.id); }}
                    >
                      Chi tiết
                    </button>
                  </article>
                );
              })}
            </div>
          </div>
        )}
      </div>
    </dialog>
  );
}
