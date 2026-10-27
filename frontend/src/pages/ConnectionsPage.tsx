import { useState } from 'react';
import { Activity, Database, Radio, Server, AlertCircle } from 'lucide-react';
import type { Snapshot } from '../types/trading';
import { selectMarket } from '../lib/api';
import { formatTime, relativeTime } from '../lib/time-format';
import { formatQuote, mexcErrorText } from '../lib/format';
import { FUTURES_SYMBOLS } from '../lib/futures-symbol';
import type { Board } from '../components/FuturesBoard';
import { EmptyState } from '../components/EmptyState';

type Tone = 'ok' | 'warn' | 'bad' | 'idle';

const toneClasses: Record<Tone, { badge: string; dot: string }> = {
  ok: {
    badge: 'border-bull/25 bg-bull/10 text-bull',
    dot: 'bg-bull',
  },
  warn: {
    badge: 'border-amber-400/25 bg-amber-400/10 text-amber-200',
    dot: 'bg-amber-400',
  },
  bad: {
    badge: 'border-bear/25 bg-bear/10 text-rose-300',
    dot: 'bg-bear',
  },
  idle: {
    badge: 'border-border-base bg-surface-2 text-text-muted',
    dot: 'bg-text-muted',
  },
};

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
  icon: Icon,
  tone,
  status,
  children,
}: {
  title: string;
  icon: typeof Server;
  tone: Tone;
  status: string;
  children: React.ReactNode;
}) {
  const t = toneClasses[tone];
  return (
    <section className="flex min-w-0 flex-col gap-3 rounded-xl border border-border-base bg-gradient-to-br from-surface-2/60 to-surface-1 p-4 shadow-sm">
      <header className="flex flex-wrap items-center justify-between gap-2 border-b border-border-subtle/60 pb-3">
        <div className="flex items-center gap-2">
          <Icon className="size-4 shrink-0 text-text-muted" aria-hidden="true" />
          <h2 className="text-sm font-semibold text-text-primary">{title}</h2>
        </div>
        <span className={`inline-flex items-center gap-1.5 rounded-md border px-2 py-0.5 text-[11px] font-medium ${t.badge}`}>
          <span className={`size-1.5 rounded-full ${t.dot}`} aria-hidden="true" />
          {status}
        </span>
      </header>
      {children}
    </section>
  );
}

function Row({ label, children, span2 }: { label: string; children: React.ReactNode; span2?: boolean }) {
  return (
    <div className={`min-w-0 rounded-lg border border-border-subtle bg-canvas/40 p-2.5 ${span2 ? 'sm:col-span-2' : ''}`}>
      <dt className="text-[11px] font-medium text-text-muted">{label}</dt>
      <dd className="mt-1 text-xs leading-relaxed text-text-primary [overflow-wrap:anywhere]">{children}</dd>
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
    <div className="flex min-w-0 flex-col gap-5 text-sm">
      {/* 3 cards trạng thái */}
      <div className="grid min-w-0 gap-3.5 lg:grid-cols-3">
        <Card
          title="API & worker"
          icon={Server}
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
          <dl className="grid grid-cols-1 gap-2 sm:grid-cols-2">
            <Row label="API">{connecting ? '…' : connected ? 'Kết nối' : 'Mất kết nối'}</Row>
            <Row label="Heartbeat worker">
              {snapshot.bot.heartbeat
                ? `${formatTime(eventTime(snapshot.bot.heartbeat), true)} · ${relativeTime(eventTime(snapshot.bot.heartbeat))}`
                : 'Chưa có'}
            </Row>
            <Row label="Nến đóng gần nhất" span2>
              {snapshot.market.last_closed_time
                ? `${formatTime(snapshot.market.last_closed_time)} · ${relativeTime(snapshot.market.last_closed_time + 900)}`
                : 'Chưa có dữ liệu'}
            </Row>
          </dl>
          <p className="mt-auto text-xs leading-relaxed text-text-muted">
            Worker Paper lấy REST ~5 giây/lần; đóng trình duyệt không dừng bot.
          </p>
        </Card>

        <Card
          title="Nguồn dữ liệu"
          icon={Radio}
          tone={feedTone}
          status={live ? (snapshot.market.stale ? 'Dữ liệu chậm' : 'Đang cập nhật') : 'Replay'}
        >
          <dl className="grid grid-cols-1 gap-2 sm:grid-cols-2">
            <Row label="Đang dùng">{sources[current]?.name ?? current}</Row>
            <Row label="Đồng bộ gần nhất">
              {snapshot.market.last_sync
                ? `${formatTime(snapshot.market.last_sync, true)} · ${relativeTime(snapshot.market.last_sync)}`
                : 'Chưa có'}
            </Row>
            <Row label="Phạm vi" span2>{FUTURES_SYMBOLS.length} cặp USDT perpetual · 15m</Row>
          </dl>
          {snapshot.market.error && (
            <div className="flex items-start gap-2 rounded-lg border border-bear/30 bg-bear/10 p-2.5 text-xs text-rose-300">
              <AlertCircle className="mt-0.5 size-3.5 shrink-0 text-bear" aria-hidden="true" />
              <span className="leading-relaxed">Lỗi: {mexcErrorText(snapshot.market.error)}</span>
            </div>
          )}
        </Card>

        <Card
          title="Auto Paper"
          icon={Activity}
          tone={!boardLoaded ? 'idle' : !autoWorker ? 'bad' : autoOn ? 'ok' : 'warn'}
          status={
            !boardLoaded
              ? 'Đang tải'
              : !autoWorker
                ? 'Worker offline'
                : `${autoOn}/${FUTURES_SYMBOLS.length} cặp đang bật`
          }
        >
          <dl className="grid grid-cols-1 gap-2 sm:grid-cols-2">
            <Row label="Worker Auto Paper">
              {!boardLoaded ? '…' : autoWorker ? 'Có heartbeat' : 'Không có heartbeat'}
            </Row>
            <Row label="Lệnh hôm nay">
              {boardLoaded ? autoRows.reduce((sum, a) => sum + (a?.daily_trades ?? 0), 0) : '—'}
            </Row>
          </dl>
          <p className="mt-auto text-xs leading-relaxed text-text-muted">
            Bật/dừng từng cặp tại Giao dịch & Bot → Auto Paper.
          </p>
        </Card>
      </div>

      {/* Bảng sức khỏe các cặp */}
      <section className="min-w-0 rounded-xl border border-border-base bg-surface-1 p-4 shadow-sm">
        <div className="mb-3 flex min-w-0 flex-wrap items-center justify-between gap-2">
          <div>
            <h2 className="text-sm font-semibold text-text-primary">Sức khỏe {FUTURES_SYMBOLS.length} cặp</h2>
            <p className="text-xs text-text-muted">Giá MEXC, độ mới quote và trạng thái Auto Paper từng cặp</p>
          </div>
        </div>
        <div className="table-scroll min-w-0 max-w-full overflow-x-auto overscroll-x-contain rounded-lg border border-border-subtle bg-canvas/30">
          <table className="w-full min-w-[740px] border-collapse text-xs">
            <caption className="sr-only">Sức khỏe kết nối, giá và Auto Paper của từng cặp Futures</caption>
            <thead>
              <tr className="border-b border-border-base bg-surface-2/90 text-[11px] font-medium tracking-wider text-text-muted uppercase">
                <th scope="col" className="px-3.5 py-2.5 text-left">Cặp</th>
                <th scope="col" className="px-3.5 py-2.5 text-right">Giá</th>
                <th scope="col" className="px-3.5 py-2.5 text-left">Quote</th>
                <th scope="col" className="px-3.5 py-2.5 text-left">Auto Paper</th>
                <th scope="col" className="px-3.5 py-2.5 text-right">Vị thế</th>
                <th scope="col" className="px-3.5 py-2.5 text-left">Ghi chú</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border-subtle/50">
              {!boardLoaded &&
                Array.from({ length: 5 }, (_, index) => (
                  <tr key={index} className="animate-pulse" aria-hidden="true">
                    <td colSpan={6} className="px-3.5 py-3">
                      <div className="h-4 w-full rounded bg-surface-3" />
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
                  const autoTone: Tone = !a ? 'idle' : !a.worker_online ? 'bad' : a.enabled ? 'ok' : 'idle';
                  return (
                    <tr key={s} className="transition-colors hover:bg-surface-2/60">
                      <th scope="row" className="whitespace-nowrap px-3.5 py-2.5 text-left font-mono font-semibold text-text-primary">
                        {s.replace('_USDT', ' / USDT')}
                      </th>
                      <td className="whitespace-nowrap px-3.5 py-2.5 text-right font-mono tabular-nums text-text-primary">
                        {w?.quote ? formatQuote(w.quote.lastPrice) : '—'}
                      </td>
                      <td className="whitespace-nowrap px-3.5 py-2.5">
                        <span className={`inline-flex items-center gap-1.5 rounded-md border px-2 py-0.5 text-[11px] tabular-nums font-medium ${toneClasses[quoteTone].badge}`}>
                          <span className={`size-1.5 rounded-full ${toneClasses[quoteTone].dot}`} aria-hidden="true" />
                          {age == null
                            ? 'Chưa có'
                            : `${Math.round(age)}s${w?.stale ? ' · cũ' : ''}`}
                        </span>
                      </td>
                      <td className="whitespace-nowrap px-3.5 py-2.5">
                        <span className={`inline-flex items-center gap-1.5 rounded-md border px-2 py-0.5 text-[11px] font-medium ${toneClasses[autoTone].badge}`}>
                          <span className={`size-1.5 rounded-full ${toneClasses[autoTone].dot}`} aria-hidden="true" />
                          {!a
                            ? '—'
                            : !a.worker_online
                              ? 'Worker off'
                              : a.enabled
                                ? 'Đang bật'
                                : 'Đang dừng'}
                        </span>
                      </td>
                      <td className="whitespace-nowrap px-3.5 py-2.5 text-right font-mono tabular-nums text-text-primary">
                        {w?.positions.length ?? '—'}
                      </td>
                      <td className={`min-w-48 max-w-sm px-3.5 py-2.5 break-words leading-relaxed ${note ? 'text-rose-300 font-medium' : 'text-text-muted'}`}>
                        {note ?? '—'}
                      </td>
                    </tr>
                  );
                })}
            </tbody>
          </table>
        </div>
      </section>

      {/* Đổi nguồn dữ liệu */}
      <section className="min-w-0 rounded-xl border border-border-base bg-surface-1 p-4 shadow-sm">
        <div className="mb-3 flex min-w-0 flex-wrap items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <Database className="size-4 shrink-0 text-text-muted" aria-hidden="true" />
            <h2 className="text-sm font-semibold text-text-primary">Đổi nguồn dữ liệu</h2>
          </div>
          <span className="rounded-md border border-amber-400/25 bg-amber-400/10 px-2 py-0.5 text-[11px] font-medium text-amber-200">
            Bot sẽ dừng khi chuyển nguồn
          </span>
        </div>
        <div className="mb-4 grid gap-3 lg:grid-cols-2">
          {(Object.keys(sources) as Source[]).map((source) => (
            <div
              key={source}
              className={`flex min-w-0 flex-wrap items-start justify-between gap-3 rounded-xl border p-4 transition-colors ${
                current === source
                  ? 'border-sky-400/30 bg-sky-400/5'
                  : 'border-border-base bg-surface-2/40 hover:bg-surface-2/60'
              }`}
            >
              <div className="min-w-0 flex-1 basis-52 space-y-1">
                <strong className="block text-sm font-semibold text-text-primary">{sources[source].name}</strong>
                <small className="block text-xs leading-relaxed text-text-muted">{sources[source].description}</small>
              </div>
              {current === source ? (
                <span className="inline-flex shrink-0 items-center gap-1.5 rounded-md border border-sky-400/25 bg-sky-400/10 px-2 py-1 text-[11px] font-medium text-sky-200">
                  <span className="size-1.5 rounded-full bg-sky-400" aria-hidden="true" />
                  Đang dùng
                </span>
              ) : (
                <button
                  type="button"
                  className="inline-flex shrink-0 items-center justify-center gap-1.5 rounded-lg border border-border-active bg-surface-2 px-3 py-1.5 text-xs font-medium text-text-secondary transition hover:bg-surface-3 hover:text-text-primary active:scale-95 disabled:pointer-events-none disabled:opacity-50"
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
          <p role="status" className="mb-3 rounded-lg border border-bull/25 bg-bull/10 p-3 text-xs leading-relaxed text-bull">
            {message}
          </p>
        )}
        <p className="text-xs leading-relaxed text-text-muted">
          MEXC không có sandbox API; Demo Trading trên web không phải môi trường khớp lệnh thử cho
          bot. Chưa thu thập API key hay bật quyền đặt lệnh.{' '}
          <a
            className="text-sky-300 underline-offset-4 hover:underline"
            href="https://www.mexc.com/mexc-api"
            target="_blank"
            rel="noreferrer"
          >
            FAQ API MEXC ↗
          </a>
        </p>
      </section>

      {/* Hoạt động hệ thống */}
      <section className="min-w-0 rounded-xl border border-border-base bg-surface-1 p-4 shadow-sm">
        <div className="mb-3 flex min-w-0 flex-wrap items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <Activity className="size-4 shrink-0 text-text-muted" aria-hidden="true" />
            <h2 className="text-sm font-semibold text-text-primary">Hoạt động hệ thống</h2>
          </div>
          <span className="rounded-md border border-border-subtle bg-surface-2 px-2 py-0.5 text-[11px] font-medium text-text-muted">
            {snapshot.events.length} sự kiện gần nhất
          </span>
        </div>
        {events.length ? (
          <ul className="max-h-96 divide-y divide-border-subtle overflow-y-auto rounded-lg border border-border-subtle bg-canvas/30">
            {events.map(({ event, count, first }) => (
              <li key={event.id} className="flex flex-wrap items-start gap-3 p-3 transition-colors hover:bg-surface-2/40 sm:flex-nowrap">
                <Activity size={15} className="mt-0.5 shrink-0 text-sky-400" aria-hidden="true" />
                <div className="min-w-0 flex-1 basis-48 space-y-1">
                  <strong className="block break-words text-xs font-medium leading-relaxed text-text-secondary">
                    {event.message}
                    {count > 1 && (
                      <span className="ml-2 inline-flex items-center rounded bg-amber-400/15 px-1.5 py-0.5 text-[11px] font-mono tabular-nums text-amber-200">
                        ×{count}
                      </span>
                    )}
                  </strong>
                  <small className="block text-[11px] leading-relaxed text-text-muted">
                    {formatTime(eventTime(event.time), true)}
                    {count > 1 &&
                    formatTime(eventTime(first), true) !== formatTime(eventTime(event.time), true)
                      ? ` (từ ${formatTime(eventTime(first), true, false)})`
                      : ''}
                  </small>
                </div>
                <span className="inline-flex shrink-0 items-center rounded-md border border-border-subtle bg-surface-2 px-2 py-0.5 text-[11px] font-medium text-text-secondary">
                  {kindLabels[event.kind] ?? event.kind}
                </span>
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
