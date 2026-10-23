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

const candles = new Map<string, { rows: CachedCandle[]; hasMore: boolean }>();

export const candleKey = (symbol: string, frame: string, side: string) =>
  `${symbol}:${frame}:${side}`;

export const cachedCandles = (key: string) => candles.get(key);

export function storeCandles(key: string, rows: CachedCandle[], hasMore: boolean) {
  candles.set(key, { rows, hasMore });
}
