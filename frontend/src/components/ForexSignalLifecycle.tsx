import { Activity } from 'lucide-react';
import type { ForexEntryIntent, ForexRecordedSignal } from '../types/forex-entry';
import { entryLifecycle } from '../lib/forex-entry-lifecycle';
import { formatTime } from '../lib/time-format';
import { priceDigits } from '../lib/forex-labels';

export function ForexSignalLifecycle({ intents, signals, symbol }: {
  intents: ForexEntryIntent[];
  signals: ForexRecordedSignal[];
  symbol: string;
}) {
  const waiting = intents.filter((intent) => intent.status === 'waiting').length;
  const unresolved = intents.filter((intent) => intent.status === 'uncertain').length;
  const price = (value: number | null) => value == null ? '—' : value.toFixed(priceDigits(symbol));
  const rows = [...intents].sort((left, right) =>
    (right.available_at ?? 0) - (left.available_at ?? 0),
  );
  const reasons: Record<string, string> = {
    quote_gap: 'Mất nhịp giá', quote_stale: 'Giá quá cũ', level_already_crossed: 'Giá đã vượt mức chờ',
    entry_expired: 'Quá thời hạn chờ', worker_restart: 'Worker khởi động lại',
    trigger_preflight_failed: 'Không qua kiểm tra trước gửi', ambiguous_entry: 'Không rõ nhánh chạm trước',
    capacity_unavailable: 'Không đủ chỗ mở lệnh', invalidated: 'Setup mất hiệu lực',
  };
  return (
    <details className="forex-signal-lifecycle">
      <summary>
        <Activity size={14} aria-hidden="true" />
        <strong>Tín hiệu & canh giá</strong>
        <span>{signals.length} ghi nhận · {waiting} canh giá</span>
        {unresolved > 0 && <span className="pair-intent is-uncertain">{unresolved} cần đối soát</span>}
        <span className="signal-lifecycle-disclosure">Chi tiết</span>
      </summary>
      <p>Chấm tròn: tín hiệu / lệnh ảo. Mũi tên: fill broker. Nét chấm xám: mức chờ đã hủy / hết hạn gần nhất.</p>
      <div className="signal-lifecycle-list" role="region" aria-label={`Vòng đời tín hiệu ${symbol}`} tabIndex={0}>
        {rows.map((intent) => {
          const state = entryLifecycle(intent);
          const filled = !!intent.broker_fills?.length;
          const ended = ['completed', 'cancelled', 'expired'].includes(intent.status);
          const steps = [
            ['Tín hiệu', true], ['Canh giá', true], ['Kích hoạt', intent.triggered_at != null],
            ['Gửi broker', intent.status === 'submitted' || filled], ['Khớp', filled],
          ] as const;
          return <article className="signal-lifecycle-row" key={intent.id}>
            <div className="signal-lifecycle-heading">
              <span className={`pair-intent is-${state.tone}`}>{state.short}</span>
              <strong className={intent.side === 'long' ? 'positive' : 'negative'}>{intent.side === 'long' ? 'MUA' : 'BÁN'}</strong>
              <span className="font-mono" title={intent.strategy}>{intent.strategy}</span>
              <time>{formatTime(intent.available_at, true)}</time>
            </div>
            <ol className="signal-lifecycle-steps" aria-label={state.label}>
              {steps.map(([label, done]) => <li key={label} className={done ? 'is-done' : ''}>{label}</li>)}
              {ended && <li className={`is-terminal is-${state.tone}`}>{state.short}</li>}
            </ol>
            <div className="signal-lifecycle-meta">
              <span>Limit {price(intent.limit_price)} · Stop {price(intent.stop_price)}</span>
              {intent.winner && <span>Nhánh {intent.winner.toUpperCase()}{intent.triggered_at ? ` · ${formatTime(intent.triggered_at, true)}` : ''}</span>}
              {!ended && <span>Hạn {formatTime(intent.expires_at, true)}</span>}
              {intent.ended_at && <span>Kết thúc {formatTime(intent.ended_at, true)}</span>}
              {intent.reason && <span title={intent.reason}>{reasons[intent.reason] ?? intent.reason}</span>}
              {intent.broker_fills?.map((fill) => <span key={fill.position_id}>Fill #{fill.position_id} · {price(fill.price)}</span>)}
            </div>
          </article>;
        })}
        {signals.filter((signal) => !intents.some((intent) => intent.strategy === signal.strategy && intent.available_at === signal.available_at))
          .map((signal) => <article className="signal-lifecycle-row" key={`${signal.strategy}:${signal.available_at}`}>
            <div className="signal-lifecycle-heading">
              <span className="pair-intent">Ghi nhận</span>
              <strong className={signal.side === 'long' ? 'positive' : 'negative'}>{signal.side === 'long' ? 'MUA' : 'BÁN'}</strong>
              <span className="font-mono">{signal.strategy}</span>
              <time>{formatTime(signal.available_at, true)}</time>
            </div>
            <div className="signal-lifecycle-meta">Forward mô phỏng · không có lệnh canh giá tương ứng trong dữ liệu hiện tại; không phải fill broker.</div>
          </article>)}
        {!intents.length && !signals.length && <p>Chưa có tín hiệu ghi nhận hoặc lệnh canh giá trong dữ liệu hiện tại. Setup dự kiến không phải tín hiệu đã giao dịch.</p>}
      </div>
    </details>
  );
}
