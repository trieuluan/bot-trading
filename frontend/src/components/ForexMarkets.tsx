import { useEffect, useState } from 'react';
import { setRoute } from '../lib/router';
import { prefetchSymbol } from '../lib/forex-cache';

interface Market {
  symbol: string;
  price: number | null;
  age_seconds: number | null;
  price_source: string;
  positions: number;
  specs: string;
  blocked_reason: string | null;
  feed: { error: string | null; remaining_intervals: number | null; worker_online: boolean }[];
}

const statusText: Record<string, string> = {
  v2_execution_disabled: 'Execution V2 đang tắt',
  exness_execution_disabled: 'Execution Exness đang tắt',
  paused: 'Tài khoản tạm dừng entry',
  no_eligible_tree: 'Chưa có cây đủ chuẩn',
  v2_ledger_unavailable: 'Chưa có ledger hợp lệ',
  symbol_position_limit: 'Đủ vị thế mỗi cặp',
  account_position_limit: 'Đủ vị thế toàn tài khoản',
  specs_unavailable_or_stale: 'Specs thiếu hoặc đã cũ',
  commission_unverified: 'Commission chưa xác minh',
};

export function ForexMarkets({ symbol, compact = false }: { symbol: string; compact?: boolean }) {
  const [data, setData] = useState<{
    markets: Market[];
    account_positions: number;
    max_positions: number;
    max_positions_per_symbol: number;
  }>();
  const [error, setError] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    let busy = false;
    const refresh = async () => {
      if (busy) return;
      busy = true;
      try {
        const response = await fetch('/api/workspaces/exness_forex/markets', {
          signal: AbortSignal.any([controller.signal, AbortSignal.timeout(10000)]),
        });
        if (!response.ok) throw new Error();
        const next = await response.json();
        if (!controller.signal.aborted) {
          setData(next);
          setError(false);
        }
      } catch {
        if (!controller.signal.aborted) setError(true);
      } finally {
        busy = false;
      }
    };
    void refresh();
    const timer = setInterval(() => void refresh(), 10000);
    return () => {
      controller.abort();
      clearInterval(timer);
    };
  }, []);
  return (
    <section className={`forex-markets ${compact ? 'is-compact' : ''}`} aria-label="Các cặp Forex">
      <div className="forex-market-strip">
        {data?.markets.map((market) => (
          <button
            key={market.symbol}
            type="button"
            aria-pressed={market.symbol === symbol}
            onPointerEnter={() => prefetchSymbol(market.symbol)}
            onFocus={() => prefetchSymbol(market.symbol)}
            onClick={() => setRoute({ symbol: market.symbol })}
          >
            <strong>{market.symbol}</strong>
            <span>
              {market.price == null
                ? '—'
                : market.price.toLocaleString('en-US', { maximumFractionDigits: 5 })}
            </span>
            <small
              className={
                error || market.age_seconds == null || market.age_seconds > 30
                  ? 'muted'
                  : 'positive'
              }
            >
              {market.age_seconds == null
                ? 'Chưa có giá'
                : `${Math.floor(market.age_seconds)}s · ${market.price_source === 'tick' ? 'tick' : 'nến đóng'}`}
              {' · '}
              {market.positions}/{data.max_positions_per_symbol} vị thế
            </small>
          </button>
        ))}
      </div>
      {error && <p role="alert">Không tải được tổng quan; giá hiển thị có thể đã cũ.</p>}
      {data && (
        <details>
          <summary>
            {data.markets.length} cặp · {data.account_positions}/{data.max_positions} vị thế toàn
            tài khoản · Trạng thái từng cặp
          </summary>
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>Cặp</th>
                  <th>Thông số</th>
                  <th>Feed</th>
                  <th>Vị thế</th>
                  <th>Chặn entry</th>
                </tr>
              </thead>
              <tbody>
                {data.markets.map((market) => (
                  <tr key={market.symbol}>
                    <td>{market.symbol}</td>
                    <td>
                      {market.specs === 'partial'
                        ? 'Đã có · chưa swap'
                        : market.specs === 'stale'
                          ? 'Đã cũ'
                          : 'Chưa có'}
                    </td>
                    <td>
                      {!market.feed.length
                        ? 'Chờ dữ liệu'
                        : market.feed.some((frame) => frame.error)
                          ? 'Lỗi đồng bộ'
                          : market.feed.some((frame) => !frame.worker_online)
                            ? 'Worker offline'
                            : market.feed.some((frame) => frame.remaining_intervals !== 0)
                              ? 'Đang đồng bộ'
                              : 'Đã đồng bộ'}
                    </td>
                    <td>{market.positions}</td>
                    <td>
                      {market.blocked_reason
                        ? (statusText[market.blocked_reason] ?? market.blocked_reason)
                        : 'Không có khoá; vẫn kiểm tra trước submit'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </details>
      )}
    </section>
  );
}
