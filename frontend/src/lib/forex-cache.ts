import type { ForexDashboard } from '../App';

// Cache trong bộ nhớ theo cặp để đổi cặp/khung hiển thị ngay dữ liệu đã có,
// rồi mới làm mới từ API (stale-while-revalidate). Không lưu xuống storage.

export interface CachedCandle {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  tick_volume?: number | null;
}

const dashboards = new Map<string, ForexDashboard>();
const dashboardRequests = new Map<string, Promise<ForexDashboard>>();
const candles = new Map<string, { rows: CachedCandle[]; hasMore: boolean }>();
const candleRequests = new Set<string>();

export const candleKey = (symbol: string, frame: string, side: string) =>
  `${symbol}:${frame}:${side}`;

export const cachedDashboard = (symbol: string) => dashboards.get(symbol);

export function fetchDashboard(symbol: string): Promise<ForexDashboard> {
  const pending = dashboardRequests.get(symbol);
  if (pending) return pending;
  const request = fetch(
    `/api/workspaces/exness_forex/dashboard?symbol=${encodeURIComponent(symbol)}`,
    { signal: AbortSignal.timeout(10000) },
  )
    .then(async (response) => {
      if (!response.ok) throw new Error('Không tải được workspace Exness.');
      const data = (await response.json()) as ForexDashboard;
      if (data.workspace.id !== 'exness_forex') throw new Error('Sai nguồn workspace.');
      dashboards.set(symbol, data);
      return data;
    })
    .finally(() => dashboardRequests.delete(symbol));
  dashboardRequests.set(symbol, request);
  return request;
}

export const cachedCandles = (key: string) => candles.get(key);

export function storeCandles(key: string, rows: CachedCandle[], hasMore: boolean) {
  candles.set(key, { rows, hasMore });
}

/** Tải trước khi rê chuột lên cặp: dashboard + nến mặc định của chart Bot (15m bid). */
export function prefetchSymbol(symbol: string) {
  if (!dashboards.has(symbol)) void fetchDashboard(symbol).catch(() => {});
  const key = candleKey(symbol, '15m', 'bid');
  if (candles.has(key) || candleRequests.has(key)) return;
  candleRequests.add(key);
  void fetch(
    `/api/workspaces/exness_forex/candles?symbol=${encodeURIComponent(symbol)}&timeframe=15m&price_type=bid&limit=500`,
    { signal: AbortSignal.timeout(10000) },
  )
    .then((response) => (response.ok ? response.json() : null))
    .then((data) => {
      if (
        data?.workspace?.id === 'exness_forex' &&
        data.timeframe === '15m' &&
        data.price_type === 'bid' &&
        !candles.has(key)
      )
        storeCandles(key, data.candles ?? [], !!data.has_more);
    })
    .catch(() => {})
    .finally(() => candleRequests.delete(key));
}
