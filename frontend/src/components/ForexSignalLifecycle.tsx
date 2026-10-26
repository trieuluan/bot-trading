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
  const pending = (setup: ForexSignalSetup) => setup.intents.some((intent) =>
    ['waiting', 'triggered', 'submitting', 'submitted', 'uncertain'].includes(intent.status));
  const rows = setups.filter((setup) => filter === 'all' || (filter === 'pending' ? pending(setup) : !pending(setup)));
  const selected = setups.find((setup) => setup.id === selectedId);
  const allIntents = setups.flatMap((setup) => setup.intents);
  const waiting = allIntents.filter((intent) => intent.status === 'waiting').length;
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
    <details id="forex-signals" className="forex-signal-lifecycle" open={open}
      onToggle={(event) => setOpen(event.currentTarget.open)}>
      <summary>
        <Activity size={14} aria-hidden="true" />
        <strong>Chọn setup để xem chart</strong>
        <span>
          {totalSignals} tín hiệu · {setups.length} setup ({waiting} đang canh)
        </span>
        {unresolved > 0 && <span className="pair-intent is-uncertain">{unresolved} cần đối soát</span>}
        {selected && <span className="signal-setup-selected">Đang xem: {selected.side === 'long' ? 'Mua' : 'Bán'} · {formatTime(selected.time, true)}</span>}
        <span className="signal-lifecycle-disclosure">Danh sách setup</span>
      </summary>
      <div className="signal-lifecycle-toolbar">
        <div className="signal-lifecycle-filters" role="group" aria-label="Lọc setup">
          {([['pending', 'Đang canh / xử lý'], ['history', 'Lịch sử'], ['all', 'Tất cả']] as const).map(([value, label]) =>
            <button type="button" key={value} aria-pressed={filter === value} onClick={() => setFilter(value)}>{label}</button>)}
        </div>
        {selected && <button type="button" className="signal-lifecycle-clear-btn" onClick={() => onSelect(null)}>
          <X size={12} aria-hidden="true" /> Bỏ chọn setup
        </button>}
      </div>
      <p className="signal-lifecycle-guide">
        Bấm vào một setup bên dưới để chart <strong>chỉ vẽ duy nhất</strong> vòng đời và mức canh
        giá của setup đó; vị thế đang mở vẫn giữ nguyên. Bấm lại để bỏ chọn. Chọn setup không gửi hay hủy lệnh.
      </p>
      <div
        className="signal-lifecycle-list"
        role="region"
        aria-label={`Vòng đời tín hiệu ${symbol}`}
        tabIndex={0}
      >
        {rows.map((setup) => {
          const isSelected = setup.id === selectedId;
          const mainIntent = setup.intents[0];
          const state = mainIntent
            ? entryLifecycle(mainIntent)
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
              className={`signal-lifecycle-row is-interactive${isSelected ? ' is-selected' : ''}`}
              onClick={() => onSelect(isSelected ? null : setup.id)}
              onKeyDown={(event) => {
                if (event.key === 'Enter' || event.key === ' ') {
                  event.preventDefault();
                  onSelect(isSelected ? null : setup.id);
                }
              }}
              role="button"
              tabIndex={0}
              aria-pressed={isSelected}
            >
              <div className="signal-lifecycle-heading">
                <span className={`pair-intent is-${state.tone}`}>{state.short}</span>
                <strong className={setup.side === 'long' ? 'positive' : 'negative'}>
                  {sideVn}
                </strong>
                <span className="signal-setup-tags">{tagParts.join(' · ')}</span>
                <time>{formatTime(setup.time, true)}</time>
                <span className="signal-lifecycle-select-action">
                  {isSelected ? (
                    <span className="is-active">
                      <Check size={12} aria-hidden="true" /> Đang xem trên chart
                    </span>
                  ) : (
                    <span>Xem trên chart</span>
                  )}
                </span>
              </div>
              <div className="signal-lifecycle-meta">
                {mainIntent ? (
                  <>
                    <span>
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
                      <span title={mainIntent.reason}>
                        {reasons[mainIntent.reason] ?? mainIntent.reason}
                      </span>
                    )}
                    {fill && <span>Fill #{fill.position_id} @ {price(fill.price)}</span>}
                  </>
                ) : (
                  <span>Chỉ ghi nhận tín hiệu · chưa có lệnh canh giá tương ứng; không phải fill broker.</span>
                )}
              </div>
              {isSelected && <div className="signal-lifecycle-meta">
                {mainIntent && <ol className="signal-lifecycle-steps" aria-label="Vòng đời setup đã chọn">
                  {([
                    ['Tín hiệu', true], ['Canh giá', true], ['Kích hoạt', mainIntent.triggered_at != null],
                    ['Gửi broker', mainIntent.status === 'submitted' || !!mainIntent.broker_fills?.length],
                    ['Khớp', !!mainIntent.broker_fills?.length],
                  ] as const).map(([label, done]) => <li key={label} className={done ? 'is-done' : ''}>{label}</li>)}
                </ol>}
                {setup.intents.map((intent) => <div className="signal-setup-strategies" key={intent.id}>
                  {intent.strategy} · {entryLifecycle(intent).label} · Limit {price(intent.limit_price)} · Stop {price(intent.stop_price)}
                  {intent.expires_at ? ` · Hạn ${formatTime(intent.expires_at, true)}` : ''}
                  {intent.reason ? ` · ${reasons[intent.reason] ?? intent.reason}` : ''}
                  {intent.broker_fills?.map((fill) => ` · Fill #${fill.position_id} @ ${price(fill.price)}`).join('')}
                </div>)}
                <span className="signal-setup-strategies">Cây ghi nhận: {setup.signals.map((signal) => signal.strategy).join(', ')}</span>
              </div>}
            </article>
          );
        })}
        {!rows.length && (
          <p className="signal-lifecycle-empty">
            {setups.length ? 'Không có setup trong bộ lọc này. Chọn Lịch sử hoặc Tất cả để xem tín hiệu đã qua.'
              : 'Chưa có tín hiệu ghi nhận hoặc setup canh giá trong dữ liệu hiện tại.'}
          </p>
        )}
      </div>
    </details>
  );
}
