import { useEffect, useRef, useState } from 'react';
import { Zap } from 'lucide-react';
import { subscribeForexStream } from '../lib/forex-stream';
import { useRoute, setRoute } from '../lib/router';
import { useStickyBar } from '../hooks/useStickyBar';
import {
  blockLabel,
  feedErrorText,
  forexSignalCount,
  priceDigits,
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
  age_seconds: number | null;
  price_source: string;
  positions: number;
  specs: string;
  blocked_reason: string | null;
  execution?: {
    entry_allowed_count?: number;
    evolution_ready_count?: number;
    signal_count?: number;
    signal_available_at?: number | null;
    signal_expires_at?: number | null;
  };
  feed: Feed[];
}

interface MarketsData {
  markets: Market[];
  account_positions: number;
  max_positions: number;
  max_positions_per_symbol: number;
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
                onClick={() => select(market.symbol)}
                title={`${market.symbol} · ${signals} cây có tín hiệu · ${market.positions} vị thế mở. Tín hiệu không đồng nghĩa đã vào lệnh.`}
              >
                <strong>{market.symbol}</strong>
                <span>{formatPrice(market)}</span>
                {loading && market.symbol === symbol && (
                  <span className="pair-loading" aria-hidden="true" />
                )}
                {signals > 0 && (
                  <span
                    className="pair-signal"
                    aria-label={`${signals} cây có tín hiệu`}
                    title="Tín hiệu M15 mới, còn trong cửa sổ vào lệnh; vẫn phải qua kiểm tra execution"
                  >
                    <Zap size={10} aria-hidden="true" />
                    {signals}
                  </span>
                )}
                {market.positions > 0 && (
                  <span
                    className="forex-strip-positions"
                    aria-label={`${market.positions} vị thế đang mở`}
                    title={`${market.positions} vị thế broker đang mở; mỗi leg tính một vị thế`}
                  >
                    {market.positions} mở
                  </span>
                )}
              </button>
            );
          })}
        </div>
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
            ? `${data.account_positions}/${data.max_positions} vị thế toàn tài khoản · tối đa ${data.max_positions_per_symbol}/cặp`
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
                  <td colSpan={6}>
                    <span className="skeleton-line" />
                  </td>
                </tr>
              ))}
            {rows.map((market) => {
              const active = market.symbol === symbol;
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
                      <td className="num font-mono">{formatPrice(market)}</td>
                      <td>
                        <span className={`dot-label tone-${fresh ? 'ok' : 'warn'}`}>
                          {market.age_seconds == null
                            ? 'Chưa có giá'
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
                        <span className={`status-chip tone-${blocked ? 'warn' : 'ok'}`}>
                          {blocked ?? 'Sẵn sàng'}
                        </span>
                      </td>
                    </>
                  ) : (
                    <>
                      <td>
                        <span className="font-mono">{formatPrice(market)}</span>{' '}
                        <small className={fresh ? 'positive' : 'muted'}>
                          {market.age_seconds == null ? '' : `${Math.floor(market.age_seconds)}s`}
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
