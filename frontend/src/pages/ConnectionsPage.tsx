import { useState } from 'react';
import { Activity } from 'lucide-react';
import type { Snapshot } from '../types/trading';
import { selectMarket } from '../lib/api';
import { formatTime, relativeTime } from '../lib/time-format';
import { formatQuote, mexcErrorText } from '../lib/format';
import { FUTURES_SYMBOLS } from '../lib/futures-symbol';
import type { Board } from '../components/FuturesBoard';
import { EmptyState } from '../components/EmptyState';

type Tone = 'ok' | 'warn' | 'bad' | 'idle';

const sources = {
  mexc_futures: {
    name: 'MEXC Futures (live)',
    description: 'Chart WebSocket live · nến đóng REST 15 giây · khớp Paper REST 5 giây',
  },
  synthetic: {
    name: 'Demo replay',
    description: 'Dữ liệu tổng hợp · chạy offline · phát lại tăng tốc',
  },
} as const;
type Source = keyof typeof sources;

const kindLabels: Record<string, string> = {
  auto_paper: 'Auto Paper',
  bot: 'Bot',
  market: 'Thị trường',
  paper: 'Paper',
  learner: 'Bộ học',
};

const eventTime = (time: string) => (time.endsWith('Z') || time.includes('+') ? time : `${time}Z`);

/** Gộp các sự kiện liên tiếp cùng nội dung (vd. 10 dòng "Worker khởi động lại" cùng lúc). */
function groupEvents(events: Snapshot['events']) {
  const groups: { event: Snapshot['events'][number]; count: number; first: string }[] = [];
  for (const event of events) {
    const last = groups.at(-1);
    if (last && last.event.message === event.message && last.event.kind === event.kind) {
      last.count += 1;
      last.first = event.time;
    } else groups.push({ event, count: 1, first: event.time });
  }
  return groups;
}

function Card({
  title,
  tone,
  status,
  children,
}: {
  title: string;
  tone: Tone;
  status: string;
  children: React.ReactNode;
}) {
  return (
    <section className={`conn-card tone-${tone}`}>
      <header>
        <h2>{title}</h2>
        <span className={`status-chip tone-${tone} inline-flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 text-xs font-medium bg-surface-3 text-text-secondary`}>{status}</span>
      </header>
      {children}
    </section>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <dt>{label}</dt>
      <dd>{children}</dd>
    </div>
  );
}

export function ConnectionsPage({
  snapshot,
  connected,
  connecting,
  board,
  onRefresh,
}: {
  snapshot: Snapshot;
  connected: boolean;
  connecting: boolean;
  board: Board;
  onRefresh: () => Promise<void>;
}) {
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const current = snapshot.data_source as Source;
  const live = current === 'mexc_futures';
  const now = Date.now();

  async function choose(source: Source) {
    const confirmed = window.confirm(
      `Chuyển nguồn dữ liệu sang "${sources[source].name}"?\n\n` +
        '• Bot tạo tín hiệu sẽ bị dừng khi chuyển nguồn.\n' +
        '• Chart, ví Paper và Auto Paper sẽ dùng nguồn mới.\n' +
        (source === 'synthetic'
          ? '• Demo replay là dữ liệu tổng hợp, không phải giá MEXC thật.'
          : '• Cần kết nối được API public của MEXC.'),
    );
    if (!confirmed) return;
    setBusy(true);
    setMessage('');
    try {
      await selectMarket(source);
      await onRefresh();
      setMessage(
        'Đã đổi nguồn dữ liệu. Bot đã dừng khi chuyển nguồn; bật lại Auto Paper tại Giao dịch & Bot.',
      );
    } catch (cause) {
      setMessage(cause instanceof Error ? cause.message : 'Không kết nối được nguồn dữ liệu.');
    } finally {
      setBusy(false);
    }
  }

  const apiTone: Tone = connecting
    ? 'idle'
    : !connected
      ? 'bad'
      : snapshot.bot.online
        ? 'ok'
        : 'warn';
  const feedTone: Tone = !connected
    ? 'idle'
    : snapshot.market.stale || snapshot.market.error
      ? 'warn'
      : 'ok';
  const autoRows = FUTURES_SYMBOLS.map((s) => board[s].auto);
  const autoOn = autoRows.filter((a) => a?.enabled).length;
  const autoWorker = autoRows.some((a) => a?.worker_online);
  const boardLoaded = autoRows.some(Boolean);
  const events = groupEvents(snapshot.events);

  return (
    <div className="forex-connection">
      <div className="conn-cards">
        <Card
          title="API & worker"
          tone={apiTone}
          status={
            connecting
              ? 'Đang kết nối'
              : !connected
                ? 'Mất kết nối'
                : snapshot.bot.online
                  ? 'Online'
                  : 'Chưa có heartbeat'
          }
        >
          <dl className="forex-summary-list grid grid-cols-1 gap-2 sm:grid-cols-2 [&>div]:rounded [&>div]:bg-surface-2 [&>div]:p-2">
            <Row label="API">{connecting ? '…' : connected ? 'Kết nối' : 'Mất kết nối'}</Row>
            <Row label="Heartbeat worker">
              {snapshot.bot.heartbeat
                ? `${formatTime(eventTime(snapshot.bot.heartbeat), true)} · ${relativeTime(eventTime(snapshot.bot.heartbeat))}`
                : 'Chưa có'}
            </Row>
            <Row label="Nến đóng gần nhất">
              {snapshot.market.last_closed_time
                ? `${formatTime(snapshot.market.last_closed_time)} · ${relativeTime(snapshot.market.last_closed_time + 900)}`
                : 'Chưa có dữ liệu'}
            </Row>
          </dl>
          <p className="conn-note">
            Worker Paper lấy REST ~5 giây/lần; đóng trình duyệt không dừng bot.
          </p>
        </Card>

        <Card
          title="Nguồn dữ liệu"
          tone={feedTone}
          status={live ? (snapshot.market.stale ? 'Dữ liệu chậm' : 'Đang cập nhật') : 'Replay'}
        >
          <dl className="forex-summary-list grid grid-cols-1 gap-2 sm:grid-cols-2 [&>div]:rounded [&>div]:bg-surface-2 [&>div]:p-2">
            <Row label="Đang dùng">{sources[current]?.name ?? current}</Row>
            <Row label="Đồng bộ gần nhất">
              {snapshot.market.last_sync
                ? `${formatTime(snapshot.market.last_sync, true)} · ${relativeTime(snapshot.market.last_sync)}`
                : 'Chưa có'}
            </Row>
            <Row label="Phạm vi">{FUTURES_SYMBOLS.length} cặp USDT perpetual · 15m</Row>
          </dl>
          {snapshot.market.error && (
            <p className="conn-hint">Lỗi: {mexcErrorText(snapshot.market.error)}</p>
          )}
        </Card>

        <Card
          title="Auto Paper"
          tone={!boardLoaded ? 'idle' : !autoWorker ? 'bad' : autoOn ? 'ok' : 'warn'}
          status={
            !boardLoaded
              ? 'Đang tải'
              : !autoWorker
                ? 'Worker offline'
                : `${autoOn}/${FUTURES_SYMBOLS.length} cặp đang bật`
          }
        >
          <dl className="forex-summary-list grid grid-cols-1 gap-2 sm:grid-cols-2 [&>div]:rounded [&>div]:bg-surface-2 [&>div]:p-2">
            <Row label="Worker Auto Paper">
              {!boardLoaded ? '…' : autoWorker ? 'Có heartbeat' : 'Không có heartbeat'}
            </Row>
            <Row label="Lệnh hôm nay">
              {boardLoaded ? autoRows.reduce((sum, a) => sum + (a?.daily_trades ?? 0), 0) : '—'}
            </Row>
          </dl>
          <p className="conn-note">Bật/dừng từng cặp tại Giao dịch & Bot → Auto Paper.</p>
        </Card>
      </div>

      <section className="panel forex-pairs-panel min-w-0 rounded-lg border border-border-base bg-surface-1 p-4">
        <div className="forex-pairs-head flex min-w-0 flex-wrap items-center gap-2 mb-3 justify-between">
          <h2>Sức khỏe {FUTURES_SYMBOLS.length} cặp</h2>
          <span className="muted text-text-muted">Giá MEXC, độ mới quote và trạng thái Auto Paper từng cặp</span>
        </div>
        <div className="table-scroll min-w-0 max-w-full overflow-x-auto overscroll-x-contain">
          <table className="forex-pairs-table w-full border-collapse whitespace-nowrap text-xs">
            <thead>
              <tr>
                <th>Cặp</th>
                <th className="num font-mono tabular-nums text-right">Giá</th>
                <th>Quote</th>
                <th>Auto Paper</th>
                <th className="num font-mono tabular-nums text-right">Vị thế</th>
                <th>Ghi chú</th>
              </tr>
            </thead>
            <tbody>
              {!boardLoaded &&
                Array.from({ length: 5 }, (_, index) => (
                  <tr key={index} className="skeleton-row min-h-5 animate-pulse rounded bg-surface-3" aria-hidden="true">
                    <td colSpan={6}>
                      <span className="skeleton-line min-h-5 animate-pulse rounded bg-surface-3" />
                    </td>
                  </tr>
                ))}
              {boardLoaded &&
                FUTURES_SYMBOLS.map((s) => {
                  const { wallet: w, auto: a } = board[s];
                  const age = w?.quote ? Math.max(0, now / 1000 - w.quote.received_at) : null;
                  const quoteTone: Tone =
                    age == null ? 'idle' : w?.stale || age > 20 ? 'warn' : 'ok';
                  const note =
                    mexcErrorText(w?.error) ?? w?.funding_error ?? a?.learner_error ?? null;
                  return (
                    <tr key={s} style={{ cursor: 'default' }}>
                      <th scope="row">{s.replace('_USDT', ' / USDT')}</th>
                      <td className="num font-mono font-mono tabular-nums text-right font-mono tabular-nums">
                        {w?.quote ? formatQuote(w.quote.lastPrice) : '—'}
                      </td>
                      <td>
                        <span className={`dot-label tone-${quoteTone} inline-flex items-center gap-1.5 text-xs text-text-muted [&.offline]:text-bear [&.ok]:text-bull [&.bad]:text-bear [&.warn]:text-amber-300`}>
                          {age == null
                            ? 'Chưa có'
                            : `${Math.round(age)}s${w?.stale ? ' · cũ' : ''}`}
                        </span>
                      </td>
                      <td>
                        <span
                          className={`status-chip tone-${!a ? 'idle' : !a.worker_online ? 'bad' : a.enabled ? 'ok' : 'idle'} inline-flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 text-xs font-medium bg-surface-3 text-text-secondary`}
                        >
                          {!a
                            ? '—'
                            : !a.worker_online
                              ? 'Worker off'
                              : a.enabled
                                ? 'Đang bật'
                                : 'Đang dừng'}
                        </span>
                      </td>
                      <td className="num font-mono tabular-nums text-right">{w?.positions.length ?? '—'}</td>
                      <td className={note ? "negative text-bear" : "muted text-text-muted"}>{note ?? '—'}</td>
                    </tr>
                  );
                })}
            </tbody>
          </table>
        </div>
      </section>

      <section className="panel conn-feed min-w-0 rounded-lg border border-border-base bg-surface-1 p-4">
        <div className="forex-pairs-head flex min-w-0 flex-wrap items-center gap-2 mb-3 justify-between">
          <h2>Đổi nguồn dữ liệu</h2>
          <span className="muted text-text-muted">Chỉ dùng khi cần thử offline; bot sẽ dừng khi chuyển</span>
        </div>
        <div className="source-options">
          {(Object.keys(sources) as Source[]).map((source) => (
            <div key={source} className={`source-option ${current === source ? 'is-current' : ''}`}>
              <div>
                <strong>{sources[source].name}</strong>
                <small>{sources[source].description}</small>
              </div>
              {current === source ? (
                <span className="status-chip tone-ok inline-flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 text-xs font-medium bg-surface-3 text-text-secondary">Đang dùng</span>
              ) : (
                <button
                  type="button"
                  className="secondary inline-flex items-center justify-center gap-1.5 rounded-md border border-border-active bg-surface-2 px-3 py-1.5 text-xs text-text-secondary hover:bg-surface-3"
                  disabled={!connected || busy}
                  onClick={() => void choose(source)}
                >
                  {busy ? 'Đang chuyển…' : 'Chuyển sang nguồn này'}
                </button>
              )}
            </div>
          ))}
        </div>
        {message && (
          <p role="status" className="conn-hint tone-ok source-message">
            {message}
          </p>
        )}
        <p className="micro text-xs leading-relaxed text-text-muted">
          MEXC không có sandbox API; Demo Trading trên web không phải môi trường khớp lệnh thử cho
          bot. Chưa thu thập API key hay bật quyền đặt lệnh.{' '}
          <a href="https://www.mexc.com/mexc-api" target="_blank" rel="noreferrer">
            FAQ API MEXC ↗
          </a>
        </p>
      </section>

      <section className="panel conn-feed min-w-0 rounded-lg border border-border-base bg-surface-1 p-4">
        <div className="forex-pairs-head flex min-w-0 flex-wrap items-center gap-2 mb-3 justify-between">
          <h2>Hoạt động hệ thống</h2>
          <span className="muted text-text-muted">{snapshot.events.length} sự kiện gần nhất</span>
        </div>
        {events.length ? (
          <ul className="activity-list">
            {events.map(({ event, count, first }) => (
              <li key={event.id}>
                <Activity size={15} aria-hidden="true" />
                <div>
                  <strong>
                    {event.message}
                    {count > 1 && <span className="activity-count">×{count}</span>}
                  </strong>
                  <small>
                    {formatTime(eventTime(event.time), true)}
                    {count > 1 &&
                    formatTime(eventTime(first), true) !== formatTime(eventTime(event.time), true)
                      ? ` (từ ${formatTime(eventTime(first), true, false)})`
                      : ''}
                  </small>
                </div>
                <span className="pill inline-flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 text-xs font-medium bg-surface-3 text-text-secondary">{kindLabels[event.kind] ?? event.kind}</span>
              </li>
            ))}
          </ul>
        ) : (
          <EmptyState
            title="Chưa có sự kiện"
            description="Bật Auto Paper khi API và worker đã kết nối để bắt đầu ghi nhật ký."
          />
        )}
      </section>
    </div>
  );
}
