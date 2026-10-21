import { useEffect, useState, useSyncExternalStore } from 'react';
import { setRoute } from '../lib/router';
import { prefetchSymbol } from '../lib/forex-cache';
import { blockLabel, feedErrorText, priceDigits, type Tone } from '../lib/forex-labels';
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
  execution?: { entry_allowed_count?: number; evolution_ready_count?: number };
  feed: Feed[];
}

interface MarketsData {
  markets: Market[];
  account_positions: number;
  max_positions: number;
  max_positions_per_symbol: number;
}

// Một vòng poll /markets dùng chung cho mọi nơi đang hiển thị (API này chậm ~0.5s).
let shared: { data?: MarketsData; error: boolean } = { error: false };
const listeners = new Set<() => void>();
let timer: number | undefined;
let inflight = false;

async function poll() {
  if (inflight) return;
  inflight = true;
  try {
    const response = await fetch('/api/workspaces/exness_forex/markets', {
      signal: AbortSignal.timeout(10000),
    });
    if (!response.ok) throw new Error();
    shared = { data: await response.json(), error: false };
  } catch {
    shared = { ...shared, error: true };
  } finally {
    inflight = false;
    listeners.forEach((listener) => listener());
  }
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  if (listeners.size === 1) {
    void poll();
    timer = window.setInterval(() => void poll(), 10000);
  }
  return () => {
    listeners.delete(listener);
    if (!listeners.size) window.clearInterval(timer);
  };
}

export function useForexMarkets() {
  return useSyncExternalStore(subscribe, () => shared);
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
}: {
  symbol: string;
  variant?: 'strip' | 'table' | 'health';
}) {
  const { data, error } = useForexMarkets();
  const now = useNow();

  if (variant === 'strip')
    return (
      <section className="forex-markets is-compact" aria-label="Các cặp Forex">
        <div className="forex-market-strip">
          {data?.markets.map((market) => (
            <button
              key={market.symbol}
              type="button"
              aria-pressed={market.symbol === symbol}
              onPointerEnter={() => prefetchSymbol(market.symbol)}
              onFocus={() => prefetchSymbol(market.symbol)}
              onClick={() => select(market.symbol)}
            >
              <strong>{market.symbol}</strong>
              <span>{formatPrice(market)}</span>
              {market.positions > 0 && (
                <small className="forex-strip-positions">{market.positions} mở</small>
              )}
            </button>
          ))}
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
                  onPointerEnter={() => prefetchSymbol(market.symbol)}
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
