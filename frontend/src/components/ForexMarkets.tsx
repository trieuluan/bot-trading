import { useEffect, useId, useRef, useState } from 'react';
import { Check, Crosshair, HelpCircle, Hourglass, CircleAlert, Layers, X, Zap } from 'lucide-react';
import type { ForexEntryIntent } from '../types/forex-entry';
import { entryLifecycle, visibleEntryIntents } from '../lib/forex-entry-lifecycle';
import { ForexSessionStatus } from './ForexSessionStatus';
import { subscribeForexStream } from '../lib/forex-stream';
import { useRoute, setRoute } from '../lib/router';
import { useStickyBar } from '../hooks/useStickyBar';
import {
  blockLabel,
  feedErrorText,
  forexSignalCount,
  marketSessionDisplay,
  priceDigits,
  type ForexMarketSession,
  type Tone,
} from '../lib/forex-labels';
import { relativeTime, formatTime } from '../lib/time-format';

interface Feed {
  timeframe: string;
  bars_per_side?: number;
  last_candle?: number | null;
  last_sync?: string | null;
  error: string | null;
  retry_at?: string | null;
  remaining_intervals: number | null;
  worker_online: boolean;
}

interface Market {
  symbol: string;
  price: number | null;
  price_at: number | null;
  age_seconds: number | null;
  price_source: string;
  positions: number;
  setup_positions?: number;
  specs: string;
  blocked_reason: string | null;
  market_session?: ForexMarketSession;
  execution?: {
    entry_allowed_count?: number;
    evolution_ready_count?: number;
    signal_count?: number;
    signal_counts?: { v3: number; v4: number };
    signal_available_at?: number | null;
    signal_expires_at?: number | null;
  };
  feed: Feed[];
  entry_intents?: ForexEntryIntent[];
}

export function ForexIntentBadge({ intents, now }: { intents: ForexEntryIntent[]; now: number }) {
  const visible = visibleEntryIntents(intents, now / 1000);
  const priority = ['uncertain', 'waiting', 'triggered', 'filled', 'cancelled', 'ended'];
  const first = [...visible].sort(
    (left, right) => priority.indexOf(entryLifecycle(left).tone) - priority.indexOf(entryLifecycle(right).tone),
  )[0];
  if (!first) return null;
  const state = entryLifecycle(first);
  const count = visible.filter((intent) => entryLifecycle(intent).tone === state.tone).length;
  const Icon = { waiting: Crosshair, triggered: Zap, filled: Check, uncertain: CircleAlert,
    cancelled: X, ended: Hourglass }[state.tone] ?? Crosshair;
  const title = visible.map((intent) =>
    `${intent.strategy} · ${intent.side === 'long' ? 'Mua' : 'Bán'} · ${entryLifecycle(intent).label}${intent.reason ? ` · ${intent.reason}` : ''}`,
  ).join('\n');
  return <span className={`pair-intent is-${state.tone} inline-flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 text-xs font-medium [&.is-waiting]:bg-tone-waiting-bg [&.is-waiting]:text-tone-waiting [&.is-triggered]:bg-tone-triggered-bg [&.is-triggered]:text-tone-triggered [&.is-filled]:bg-tone-filled-bg [&.is-filled]:text-tone-filled [&.is-uncertain]:bg-tone-uncertain-bg [&.is-uncertain]:text-tone-uncertain [&.is-cancelled]:bg-tone-cancelled-bg [&.is-cancelled]:text-tone-cancelled [&.is-ended]:bg-tone-ended-bg [&.is-ended]:text-tone-ended`} title={`${count} lệnh · ${state.label}\n${title}`}
    aria-label={`${count} lệnh: ${state.label}. ${title}`}>
    <Icon size={10} aria-hidden="true" />{count}
  </span>;
}

interface MarketsData {
  markets: Market[];
  account_positions: number;
  account_setups?: number;
  max_positions: number;
  max_positions_per_symbol: number;
}

export function ForexStripLegend() {
  const id = useId();
  const [position, setPosition] = useState({ top: 8, left: 8, maxHeight: 600 });
  useEffect(() => {
    const dismiss = () => document.getElementById(id)?.hidePopover();
    window.addEventListener('resize', dismiss);
    return () => window.removeEventListener('resize', dismiss);
  }, [id]);
  return <div className="forex-strip-legend relative shrink-0 border-0 bg-transparent p-0 shadow-none">
    <button type="button" aria-label="Chú giải biểu tượng" aria-haspopup="dialog" popoverTarget={id}
      className="inline-flex size-8 cursor-pointer items-center justify-center border-0 bg-transparent p-0 text-text-muted hover:text-bull"
      onClick={(event) => {
        const rect = event.currentTarget.getBoundingClientRect();
        const maxHeight = Math.min(600, window.innerHeight - 32);
        setPosition({
          top: Math.max(8, Math.min(rect.bottom + 8, window.innerHeight - maxHeight - 8)),
          left: Math.max(8, Math.min(rect.right - 360, window.innerWidth - 368)),
          maxHeight,
        });
      }}>
      <HelpCircle size={15} aria-hidden="true" />
    </button>
    <div id={id} popover="auto" role="dialog" aria-label="Chú giải dải cặp tiền" style={position} className="forex-strip-legend-popover fixed inset-auto z-50 m-0 flex w-[min(360px,calc(100vw-16px))] flex-col gap-3 overflow-y-auto rounded-lg border border-border-active bg-surface-2 p-3 text-xs text-text-secondary shadow-lg [&:not(:popover-open)]:hidden [&_header]:flex [&_header]:items-center [&_header]:justify-between [&_header]:border-b [&_header]:border-border-base [&_header]:pb-2 [&_strong]:text-text-primary [&_small]:text-xs [&_small]:text-text-secondary [&_h4]:m-0 [&_h4]:text-xs [&_h4]:text-text-muted [&_footer]:border-t [&_footer]:border-border-base [&_footer]:pt-2 [&_footer]:text-xs [&_footer]:leading-relaxed [&_footer]:text-text-secondary">
      <header><strong>Đọc nhanh dải cặp tiền</strong><button type="button" popoverTarget={id} popoverTargetAction="hide" aria-label="Đóng chú giải" className="inline-flex size-8 shrink-0 items-center justify-center border-0 bg-transparent p-0 text-text-muted hover:text-text-primary"><X size={16} aria-hidden="true" /></button></header>
      <div className="legend-row grid grid-cols-[36px_minmax(0,1fr)] items-start gap-2 [&>span:last-child]:flex [&>span:last-child]:flex-col [&>span:last-child]:gap-1"><span className="legend-sessions flex items-center gap-1 pt-1" aria-hidden="true"><i className="dot dot-open inline-block size-1.5 shrink-0 rounded-full bg-bull" /><i className="dot dot-closed inline-block size-1.5 shrink-0 rounded-full bg-slate-500" /><i className="dot dot-unknown inline-block size-1.5 shrink-0 rounded-full bg-amber-400" /></span><span><strong>Phiên dự kiến</strong><small>Xanh: mở · xám: đóng/nghỉ · vàng: chưa rõ. Không xác nhận giá trực tiếp.</small></span></div>
      <div className="legend-row grid grid-cols-[36px_minmax(0,1fr)] items-start gap-2 [&>span:last-child]:flex [&>span:last-child]:flex-col [&>span:last-child]:gap-1"><span className="badge-sample is-signal inline-flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 text-xs font-medium bg-bull/10 text-bull"><Zap size={12} aria-hidden="true" />N</span><span><strong>Tín hiệu M15</strong><small>N là số cây còn tín hiệu, gồm mô phỏng; không phải lệnh khớp. Đây là chú giải, không phải số liệu trực tiếp.</small></span></div>
      <div className="legend-row grid grid-cols-[36px_minmax(0,1fr)] items-start gap-2 [&>span:last-child]:flex [&>span:last-child]:flex-col [&>span:last-child]:gap-1"><span className="badge-sample is-position inline-flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 text-xs font-medium bg-amber-400/10 text-amber-300"><Layers size={12} aria-hidden="true" />N</span><span><strong>Setup có vị thế mở</strong><small>N là số setup có vị thế mở. Mỗi setup có thể gồm nhiều ticket TP1 / Runner. Không phải nhãn tài khoản live.</small></span></div>
      <h4>Nhánh lệnh</h4>
      <div className="legend-states space-y-2 border-t border-border-base pt-2">
        {[
          { label: 'Canh giá', tone: 'waiting', Icon: Crosshair, text: 'Nhánh ảo, chờ kích hoạt.' },
          { label: 'Kích / đang gửi', tone: 'triggered', Icon: Zap, text: 'Đã kích hoạt hoặc đang gửi broker.' },
          { label: 'Đã gửi', tone: 'triggered', Icon: Zap, text: 'Chưa có xác nhận khớp từ broker.' },
          { label: 'Đã khớp', tone: 'filled', Icon: Check, text: 'Broker đã xác nhận fill.' },
          { label: 'Đối soát', tone: 'uncertain', Icon: CircleAlert, text: 'Chưa rõ kết quả, cần kiểm tra.' },
          { label: 'Hủy / hết hạn', tone: 'cancelled', Icon: X, text: 'Dừng canh hoặc quá hạn.' },
          { label: 'Kết thúc', tone: 'ended', Icon: Hourglass, text: 'Vòng đời đã xong; có hoặc không có fill.' },
        ].map(({ label, tone, Icon, text }) => <div className="legend-state grid grid-cols-[112px_minmax(0,1fr)] items-start gap-2 [&>.pair-intent]:justify-start" key={label}>
          <span className={`pair-intent is-${tone} inline-flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 text-xs font-medium [&.is-waiting]:bg-tone-waiting-bg [&.is-waiting]:text-tone-waiting [&.is-triggered]:bg-tone-triggered-bg [&.is-triggered]:text-tone-triggered [&.is-filled]:bg-tone-filled-bg [&.is-filled]:text-tone-filled [&.is-uncertain]:bg-tone-uncertain-bg [&.is-uncertain]:text-tone-uncertain [&.is-cancelled]:bg-tone-cancelled-bg [&.is-cancelled]:text-tone-cancelled [&.is-ended]:bg-tone-ended-bg [&.is-ended]:text-tone-ended`}><Icon size={12} aria-hidden="true" />{label}</span><small>{text}</small>
        </div>)}
      </div>
      <footer>Số cạnh icon chỉ đếm nhánh cùng trạng thái đang hiện, không phải tổng nhánh. Gạch chân xanh: cặp đang chọn. Vòng xoay: đang tải. “Giá có thể đã cũ”: mất kết nối feed.</footer>
    </div>
  </div>;
}

export function useForexMarkets() {
  const [route] = useRoute();
  const symbol = route.symbol ?? 'XAUUSD';
  const [shared, setShared] = useState<{ data?: MarketsData; error: boolean }>({ error: false });
  useEffect(
    () =>
      subscribeForexStream(
        symbol,
        'markets',
        (value) => {
          setShared({ data: value as MarketsData, error: false });
        },
        (online, connecting) =>
          setShared((previous) => ({ ...previous, error: !online && !connecting })),
      ),
    [symbol],
  );
  return shared;
}

const formatPrice = (market: Market) =>
  market.price == null
    ? '—'
    : market.price.toLocaleString('en-US', {
        minimumFractionDigits: priceDigits(market.symbol),
        maximumFractionDigits: priceDigits(market.symbol),
      });

const specsText = (specs: string) =>
  specs === 'partial' ? 'Đã có · chưa swap' : specs === 'stale' ? 'Đã cũ' : 'Chưa có';

export function feedTone(feed: Feed | undefined): Tone {
  if (!feed) return 'idle';
  if (!feed.worker_online) return 'bad';
  if (feed.error) return 'warn';
  if (feed.remaining_intervals !== 0) return 'warn';
  return 'ok';
}

export function feedText(feed: Feed | undefined, now = Date.now()) {
  if (!feed) return 'Chờ dữ liệu';
  if (!feed.worker_online) return 'Worker offline';
  if (feed.error) {
    const retry =
      feed.retry_at && new Date(feed.retry_at).getTime() > now
        ? ` · thử lại ${relativeTime(feed.retry_at, now)}`
        : '';
    return `${feedErrorText(feed.error)}${retry}`;
  }
  if (feed.remaining_intervals === null) return 'Chưa khởi tạo';
  if (feed.remaining_intervals > 0) return `Đang đồng bộ · còn ${feed.remaining_intervals} khoảng`;
  return 'Đã đồng bộ';
}

function useNow(interval = 15000) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), interval);
    return () => window.clearInterval(id);
  }, [interval]);
  return now;
}

const select = (symbol: string) => setRoute({ symbol });

export function ForexMarkets({
  symbol,
  variant = 'strip',
  loading = false,
}: {
  symbol: string;
  variant?: 'strip' | 'table' | 'health';
  /** Cặp `symbol` đã chọn nhưng dữ liệu cặp đó chưa về. */
  loading?: boolean;
}) {
  const { data, error } = useForexMarkets();
  const now = useNow(1000);
  const [barRef, stuck] = useStickyBar<HTMLElement>('--forex-strip-h');
  const stripRef = useRef<HTMLDivElement>(null);
  const [edges, setEdges] = useState({ left: false, right: false });
  const updateEdges = () => {
    const strip = stripRef.current;
    if (!strip) return;
    const left = strip.scrollLeft > 2;
    const right = strip.scrollLeft + strip.clientWidth < strip.scrollWidth - 2;
    setEdges((prev) => (prev.left === left && prev.right === right ? prev : { left, right }));
  };
  const marketCount = data?.markets.length ?? 0;
  // Cặp đang chọn luôn nằm trong vùng nhìn thấy của dải (kể cả khi mở bằng link).
  useEffect(() => {
    const strip = stripRef.current;
    strip
      ?.querySelector<HTMLElement>('button[aria-pressed="true"]')
      ?.scrollIntoView({ block: 'nearest', inline: 'nearest' });
    updateEdges();
  }, [symbol, marketCount]);
  useEffect(() => {
    window.addEventListener('resize', updateEdges);
    return () => window.removeEventListener('resize', updateEdges);
  }, []);

  if (variant === 'strip')
    return (
      <section
        ref={barRef}
        className={`forex-markets is-compact${stuck ? ' is-stuck' : ''}${loading ? ' is-loading' : ''} relative z-30 m-0 min-w-0 [&.is-compact]:sticky [&.is-compact]:top-0 [&.is-compact]:flex [&.is-compact]:items-center [&.is-compact]:gap-2 [&.is-compact]:border-b [&.is-compact]:border-border-base [&.is-compact]:bg-canvas [&.is-compact]:px-2 [&.is-compact]:py-1 [&.is-compact]:flex-nowrap`}
        aria-label="Các cặp Forex"
      >
        {loading && (
          <span className="route-progress pointer-events-none absolute inset-x-0 bottom-0 h-0.5 animate-pulse bg-bull" role="status" aria-label={`Đang tải ${symbol}`} />
        )}
        <div
          ref={stripRef}
          className={`forex-market-strip${edges.left ? ' fade-left' : ''}${edges.right ? ' fade-right' : ''} flex min-w-0 flex-1 gap-1 overflow-x-auto overscroll-x-contain [&>button]:relative [&>button]:flex [&>button]:shrink-0 [&>button]:min-w-28 [&>button]:max-w-48 [&>button]:flex-wrap [&>button]:items-center [&>button]:gap-x-2 [&>button]:gap-y-1 [&>button]:border-b-2 [&>button]:border-transparent [&>button]:bg-transparent [&>button]:text-text-secondary [&>button]:px-3 [&>button]:py-2 [&>button]:font-mono [&>button]:text-xs [&>button:hover]:bg-surface-2 [&>button[aria-pressed=true]]:border-bull [&>button[aria-pressed=true]]:bg-surface-2 [&>button[aria-pressed=true]]:text-text-primary`}
          onScroll={updateEdges}
        >
          {data?.markets.map((market) => {
            const session = marketSessionDisplay(market.market_session, now);
            const signals =
              !error && market.feed.some((feed) => feed.worker_online)
                ? forexSignalCount(market.execution, now)
                : 0;
            return (
              <button
                key={market.symbol}
                type="button"
                aria-pressed={market.symbol === symbol}
                aria-busy={loading && market.symbol === symbol}
                onClick={(event) => {
                  select(market.symbol);
                  if ((market.symbol === symbol && (signals > 0 || !!market.entry_intents?.length)) ||
                      (event.target as HTMLElement).closest('.pair-signal, .pair-intent')) {
                    setRoute({ page: 'Bot', symbol: market.symbol });
                    window.location.hash = 'forex-signals';
                    window.dispatchEvent(new Event('hashchange'));
                    document.getElementById('forex-signals')?.scrollIntoView({ block: 'center' });
                  }
                }}
                title={`${market.symbol} · ${session.tooltip} · Giá ${formatTime(market.price_at, true)} · ${signals} cây có tín hiệu · ${market.positions} vị thế mở. Tín hiệu không đồng nghĩa đã vào lệnh.`}
              >
                <span className="forex-strip-heading flex basis-full items-center gap-1.5 font-sans text-xs">
                  <strong>{market.symbol}</strong>
                  <span
                    className={`forex-strip-session is-${session.status} inline-block size-1.5 rounded-full bg-amber-400 [&.is-open]:bg-bull [&.is-closed]:bg-slate-500`}
                    role="img"
                    aria-label={`Phiên: ${session.label}`}
                    title={session.tooltip}
                  />
                </span>
                <span>{formatPrice(market)}</span>
                {loading && market.symbol === symbol && (
                  <span className="pair-loading inline-block size-3 animate-spin rounded-full border border-bull border-t-transparent" aria-hidden="true" />
                )}
                <span className="forex-strip-side inline-flex items-center gap-1.5">
                {signals > 0 && (
                  <span
                    className="pair-signal inline-flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 text-xs font-medium bg-bull/10 text-bull"
                    aria-label={`${signals} cây có tín hiệu`}
                    title={`Tín hiệu M15 mới: V3 ${market.execution?.signal_counts?.v3 ?? signals} · V4 ${market.execution?.signal_counts?.v4 ?? 0}. Gồm tín hiệu shadow; không phải lệnh broker.`}
                  >
                    <Zap size={10} aria-hidden="true" />
                    {signals}
                  </span>
                )}
                <ForexIntentBadge intents={market.entry_intents ?? []} now={now} />
                {market.positions > 0 && (
                  <span
                    className="forex-strip-positions inline-flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 text-xs font-medium bg-amber-400/10 text-amber-300"
                    aria-label={`${market.setup_positions ?? market.positions} setup, ${market.positions} ticket đang mở`}
                    title={`${market.setup_positions ?? market.positions} setup · ${market.positions} ticket broker; giới hạn vẫn tính theo ticket`}
                  >
                    <Layers size={10} aria-hidden="true" />
                    {market.setup_positions ?? market.positions}
                  </span>
                )}
                </span>
              </button>
            );
          })}
        </div>
        <ForexStripLegend />
        {error && (
          <span role="alert" className="negative forex-strip-error text-bear shrink-0 px-1 text-xs text-bear">
            Giá có thể đã cũ
          </span>
        )}
      </section>
    );

  const rows = data?.markets ?? [];
  const head =
    variant === 'table' ? (
      <tr className="border-b border-border-base bg-surface-2/80 text-[11px] font-medium uppercase tracking-wider text-text-muted">
        <th className="py-2.5 px-3 text-left">Cặp</th>
        <th className="py-2.5 px-3 text-left">Phiên dự kiến</th>
        <th className="py-2.5 px-3 text-right font-mono tabular-nums">Giá bid</th>
        <th className="py-2.5 px-3 text-left">Độ mới</th>
        <th className="py-2.5 px-3 text-right font-mono tabular-nums">Vị thế</th>
        <th className="py-2.5 px-3 text-right font-mono tabular-nums" title="Cây được phép vào lệnh / đủ chuẩn tiến hoá">
          Cây vào lệnh · tiến hoá
        </th>
        <th className="py-2.5 px-3 text-left">Entry</th>
      </tr>
    ) : (
      <tr className="border-b border-border-base bg-surface-2/80 text-[11px] font-medium uppercase tracking-wider text-text-muted">
        <th className="py-2.5 px-3 text-left">Cặp</th>
        <th className="py-2.5 px-3 text-left">Giá · độ mới</th>
        <th className="py-2.5 px-3 text-left">Feed 15M</th>
        <th className="py-2.5 px-3 text-left">Feed 1H</th>
        <th className="py-2.5 px-3 text-left">Feed 4H</th>
        <th className="py-2.5 px-3 text-left">Thông số</th>
      </tr>
    );

  return (
    <section
      className="panel forex-pairs-panel min-w-0 rounded-2xl border border-border-base bg-surface-1 p-4 lg:p-5 shadow-xs"
      aria-label="Các cặp Forex"
    >
      <div className="forex-pairs-head flex min-w-0 flex-wrap items-center gap-2 mb-3.5 justify-between">
        <h2 className="text-base font-bold text-text-primary">{variant === 'table' ? 'Các cặp giao dịch' : 'Sức khỏe feed 11 cặp'}</h2>
        <span className="text-xs text-text-muted font-mono">
          {data
            ? `${data.account_setups ?? data.account_positions} setup · ${data.account_positions}/${data.max_positions} ticket toàn tài khoản · tối đa ${data.max_positions_per_symbol} ticket/cặp`
            : 'Đang tải…'}
        </span>
      </div>
      {error && (
        <p role="alert" className="feedback my-2 flex flex-wrap items-center justify-between gap-2 rounded-md border border-amber-400/25 bg-amber-400/5 px-3 py-2 text-xs text-amber-200 [&_p]:basis-full">
          Không tải được danh sách cặp; số liệu có thể đã cũ.
        </p>
      )}
      <div className="table-scroll min-w-0 max-w-full overflow-x-auto rounded-xl border border-border-subtle/80 overscroll-x-contain" role="region" aria-label="Bảng các cặp Forex" tabIndex={0}>
        <table className="forex-pairs-table w-full border-collapse whitespace-nowrap text-xs">
          <thead>{head}</thead>
          <tbody className="divide-y divide-border-subtle/40">
            {!data &&
              Array.from({ length: 6 }, (_, index) => (
                <tr key={index} className="skeleton-row min-h-5 animate-pulse rounded bg-surface-3" aria-hidden="true">
                  <td colSpan={variant === 'table' ? 7 : 6}>
                    <span className="skeleton-line min-h-5 animate-pulse rounded bg-surface-3" />
                  </td>
                </tr>
              ))}
            {rows.map((market) => {
              const active = market.symbol === symbol;
              const session = marketSessionDisplay(market.market_session, now);
              const closed = session.status === 'closed';
              const fresh = market.age_seconds != null && market.age_seconds <= 30;
              const blocked = blockLabel(market.blocked_reason);
              return (
                <tr
                  key={market.symbol}
                  className={`cursor-pointer transition-colors ${active ? 'bg-bull/10 hover:bg-bull/15' : 'hover:bg-surface-2/60'}`}
                  onClick={() => select(market.symbol)}
                >
                  <th scope="row" className="bg-transparent py-2.5 px-3 text-left">
                    <div className="inline-flex items-center gap-2">
                      <button
                        type="button"
                      className="forex-pair-link inline-flex min-h-9 items-center font-semibold text-text-primary hover:text-bull transition-colors"
                        aria-current={active ? 'true' : undefined}
                        onClick={(event) => {
                          event.stopPropagation();
                          select(market.symbol);
                        }}
                      >
                        {market.symbol}
                      </button>
                      {active && (
                        <span className="inline-flex items-center rounded-md bg-bull/15 px-1.5 py-0.5 text-[10px] font-semibold text-bull border border-bull/20">
                          Đang xem
                        </span>
                      )}
                    </div>
                  </th>
                  {variant === 'table' ? (
                    <>
                      <td className="max-w-52 whitespace-normal leading-relaxed">
                        <ForexSessionStatus session={market.market_session} now={now} />
                      </td>
                      <td className="font-mono tabular-nums text-right text-text-primary">{formatPrice(market)}</td>
                      <td>
                        <span
                          className={`inline-flex items-center gap-1.5 text-xs ${closed ? 'text-text-muted' : fresh ? 'text-bull' : 'text-amber-200'}`}
                          title={formatTime(market.price_at, true)}
                        >
                          {market.age_seconds == null
                            ? 'Chưa có giá'
                            : closed
                              ? `Giá cuối phiên · ${formatTime(market.price_at)}`
                              : `${relativeTime(market.price_at, now)} · ${market.price_source === 'tick' ? 'tick' : 'nến đóng'}`}
                        </span>
                      </td>
                      <td className="num font-mono tabular-nums text-right">
                        {market.positions}/{data?.max_positions_per_symbol ?? '—'}
                      </td>
                      <td className="num font-mono tabular-nums text-right">
                        {market.execution?.entry_allowed_count ?? '—'}
                        <span className="muted text-text-muted">
                          {' '}
                          · {market.execution?.evolution_ready_count ?? '—'}
                        </span>
                      </td>
                      <td className="max-w-56 whitespace-normal">
                        <span
                          className={`inline-flex rounded-md border px-2 py-1 text-[11px] leading-relaxed ${!closed && blocked ? 'border-amber-400/15 bg-amber-400/5 text-amber-200' : 'border-border-base bg-surface-2 text-text-secondary'}`}
                          title={blocked ?? undefined}
                        >
                          {closed
                            ? 'Chờ mở phiên · không mở lệnh mới'
                            : (blocked ?? 'Chờ tín hiệu · qua kiểm tra execution')}
                        </span>
                      </td>
                    </>
                  ) : (
                    <>
                      <td>
                        <span className="font-mono font-mono tabular-nums">{formatPrice(market)}</span>{' '}
                        <small className={!closed && fresh ? "positive text-bull" : "muted text-text-muted"}>
                          {market.age_seconds == null
                            ? ''
                            : closed
                              ? `Giá cuối phiên · ${formatTime(market.price_at)}`
                              : `${Math.floor(market.age_seconds)}s`}
                        </small>
                      </td>
                      {['15m', '1h', '4h'].map((frame) => {
                        const feed = market.feed.find((row) => row.timeframe === frame);
                        return (
                          <td key={frame}>
                            <span
                              className={`dot-label tone-${feedTone(feed)} inline-flex items-center gap-1.5 text-xs text-text-muted [&.offline]:text-bear [&.ok]:text-bull [&.bad]:text-bear [&.warn]:text-amber-300`}
                              title={
                                feed?.last_candle
                                  ? `Nến cuối ${formatTime(feed.last_candle)}`
                                  : undefined
                              }
                            >
                              {feedText(feed, now)}
                            </span>
                          </td>
                        );
                      })}
                      <td>
                        <span
                          className={`status-chip tone-${market.specs === 'partial' ? 'ok' : 'warn'} inline-flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 text-xs font-medium bg-surface-3 text-text-secondary`}
                        >
                          {specsText(market.specs)}
                        </span>
                      </td>
                    </>
                  )}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}
