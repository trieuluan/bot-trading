import { useEffect, useRef, useState } from 'react';
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
  return <span className={`pair-intent is-${state.tone}`} title={`${count} lệnh · ${state.label}\n${title}`}
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
  const legendRef = useRef<HTMLDetailsElement>(null);
  useEffect(() => {
    const dismiss = (event: PointerEvent | KeyboardEvent) => {
      const legend = legendRef.current;
      if (!legend?.open) return;
      if (event instanceof KeyboardEvent) {
        if (event.key !== 'Escape') return;
        legend.open = false;
        legend.querySelector('summary')?.focus();
      } else if (event.target instanceof Node && !legend.contains(event.target)) {
        legend.open = false;
      }
    };
    document.addEventListener('pointerdown', dismiss);
    document.addEventListener('keydown', dismiss);
    return () => {
      document.removeEventListener('pointerdown', dismiss);
      document.removeEventListener('keydown', dismiss);
    };
  }, []);
  return <details className="forex-strip-legend" ref={legendRef}>
    <summary aria-label="Giải thích biểu tượng dải cặp tiền">
      <HelpCircle size={14} aria-hidden="true" /><span>Chú giải</span>
    </summary>
    <div className="forex-strip-legend-popover">
      <header><strong>Đọc nhanh dải cặp tiền</strong><span>Biểu tượng & trạng thái</span></header>
      <div className="legend-row"><span className="legend-sessions" aria-hidden="true"><i className="dot dot-open" /><i className="dot dot-closed" /><i className="dot dot-unknown" /></span><span><strong>Phiên dự kiến</strong><small>Xanh: mở · xám: đóng/nghỉ · vàng: chưa rõ. Không xác nhận giá trực tiếp.</small></span></div>
      <div className="legend-row"><span className="badge-sample is-signal"><Zap size={12} />4</span><span><strong>Tín hiệu M15</strong><small>Số cây còn tín hiệu, gồm mô phỏng; không phải lệnh khớp.</small></span></div>
      <div className="legend-row"><span className="badge-sample is-position"><Layers size={12} />1</span><span><strong>Setup có vị thế mở</strong><small>Mỗi setup có thể gồm nhiều ticket TP1 / Runner. Không phải nhãn tài khoản live.</small></span></div>
      <h4>Nhánh lệnh</h4>
      <div className="legend-states">
        {[
          { label: 'Canh giá', tone: 'waiting', Icon: Crosshair, text: 'Nhánh ảo, chờ kích hoạt.' },
          { label: 'Kích / đang gửi', tone: 'triggered', Icon: Zap, text: 'Đã kích hoạt hoặc đang gửi broker.' },
          { label: 'Đã gửi', tone: 'triggered', Icon: Zap, text: 'Chưa có xác nhận khớp từ broker.' },
          { label: 'Đã khớp', tone: 'filled', Icon: Check, text: 'Broker đã xác nhận fill.' },
          { label: 'Đối soát', tone: 'uncertain', Icon: CircleAlert, text: 'Chưa rõ kết quả, cần kiểm tra.' },
          { label: 'Hủy / hết hạn', tone: 'cancelled', Icon: X, text: 'Dừng canh hoặc quá hạn.' },
          { label: 'Kết thúc', tone: 'ended', Icon: Hourglass, text: 'Vòng đời đã xong; có hoặc không có fill.' },
        ].map(({ label, tone, Icon, text }) => <div className="legend-state" key={label}>
          <span className={`pair-intent is-${tone}`}><Icon size={12} aria-hidden="true" />{label}</span><small>{text}</small>
        </div>)}
      </div>
      <footer>Số cạnh icon chỉ đếm nhánh cùng trạng thái đang hiện, không phải tổng nhánh. Gạch chân xanh: cặp đang chọn. Vòng xoay: đang tải. “Giá có thể đã cũ”: mất kết nối feed.</footer>
    </div>
  </details>;
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
        className={`forex-markets is-compact${stuck ? ' is-stuck' : ''}${loading ? ' is-loading' : ''}`}
        aria-label="Các cặp Forex"
      >
        {loading && (
          <span className="route-progress" role="status" aria-label={`Đang tải ${symbol}`} />
        )}
        <div
          ref={stripRef}
          className={`forex-market-strip${edges.left ? ' fade-left' : ''}${edges.right ? ' fade-right' : ''}`}
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
                <span className="forex-strip-heading">
                  <strong>{market.symbol}</strong>
                  <span
                    className={`forex-strip-session is-${session.status}`}
                    role="img"
                    aria-label={`Phiên: ${session.label}`}
                    title={session.tooltip}
                  />
                </span>
                <span>{formatPrice(market)}</span>
                {loading && market.symbol === symbol && (
                  <span className="pair-loading" aria-hidden="true" />
                )}
                <span className="forex-strip-side">
                {signals > 0 && (
                  <span
                    className="pair-signal"
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
                    className="forex-strip-positions"
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
          <span role="alert" className="negative forex-strip-error">
            Giá có thể đã cũ
          </span>
        )}
      </section>
    );

  const rows = data?.markets ?? [];
  const head =
    variant === 'table' ? (
      <tr>
        <th>Cặp</th>
        <th>Phiên dự kiến</th>
        <th className="num">Giá bid</th>
        <th>Độ mới</th>
        <th className="num">Vị thế</th>
        <th className="num" title="Cây được phép vào lệnh / đủ chuẩn tiến hoá">
          Cây vào lệnh · tiến hoá
        </th>
        <th>Entry</th>
      </tr>
    ) : (
      <tr>
        <th>Cặp</th>
        <th>Giá · độ mới</th>
        <th>Feed 15M</th>
        <th>Feed 1H</th>
        <th>Feed 4H</th>
        <th>Thông số</th>
      </tr>
    );

  return (
    <section className="panel forex-pairs-panel" aria-label="Các cặp Forex">
      <div className="forex-pairs-head">
        <h2>{variant === 'table' ? 'Các cặp giao dịch' : 'Sức khỏe feed 11 cặp'}</h2>
        <span className="muted">
          {data
            ? `${data.account_setups ?? data.account_positions} setup · ${data.account_positions}/${data.max_positions} ticket toàn tài khoản · tối đa ${data.max_positions_per_symbol} ticket/cặp`
            : 'Đang tải…'}
        </span>
      </div>
      {error && (
        <p role="alert" className="feedback">
          Không tải được danh sách cặp; số liệu có thể đã cũ.
        </p>
      )}
      <div className="table-scroll">
        <table className="forex-pairs-table">
          <thead>{head}</thead>
          <tbody>
            {!data &&
              Array.from({ length: 6 }, (_, index) => (
                <tr key={index} className="skeleton-row" aria-hidden="true">
                  <td colSpan={variant === 'table' ? 7 : 6}>
                    <span className="skeleton-line" />
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
                  className={active ? 'is-active' : ''}
                  onClick={() => select(market.symbol)}
                >
                  <th scope="row">
                    <button
                      type="button"
                      className="forex-pair-link"
                      aria-current={active ? 'true' : undefined}
                      onClick={(event) => {
                        event.stopPropagation();
                        select(market.symbol);
                      }}
                    >
                      {market.symbol}
                    </button>
                    {active && <span className="forex-pair-badge">Đang xem</span>}
                  </th>
                  {variant === 'table' ? (
                    <>
                      <td>
                        <ForexSessionStatus session={market.market_session} now={now} />
                      </td>
                      <td className="num font-mono">{formatPrice(market)}</td>
                      <td>
                        <span
                          className={`dot-label tone-${closed ? 'idle' : fresh ? 'ok' : 'warn'}`}
                          title={formatTime(market.price_at, true)}
                        >
                          {market.age_seconds == null
                            ? 'Chưa có giá'
                            : closed
                              ? `Giá cuối phiên · ${formatTime(market.price_at)}`
                              : `${Math.floor(market.age_seconds)}s · ${market.price_source === 'tick' ? 'tick' : 'nến đóng'}`}
                        </span>
                      </td>
                      <td className="num">
                        {market.positions}/{data?.max_positions_per_symbol ?? '—'}
                      </td>
                      <td className="num">
                        {market.execution?.entry_allowed_count ?? '—'}
                        <span className="muted">
                          {' '}
                          · {market.execution?.evolution_ready_count ?? '—'}
                        </span>
                      </td>
                      <td>
                        <span
                          className={`status-chip tone-${!closed && blocked ? 'warn' : 'idle'}`}
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
                        <span className="font-mono">{formatPrice(market)}</span>{' '}
                        <small className={!closed && fresh ? 'positive' : 'muted'}>
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
                              className={`dot-label tone-${feedTone(feed)}`}
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
                          className={`status-chip tone-${market.specs === 'partial' ? 'ok' : 'warn'}`}
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
