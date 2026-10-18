import type { Candle } from '../types/trading';

export const TIMEFRAMES = { '15m': 900, '1h': 3600, '4h': 14400, '1D': 86400 } as const;
export type ChartTimeframe = keyof typeof TIMEFRAMES;

export function aggregateCandles(bars: Candle[], seconds: number, now = Date.now() / 1000): Candle[] {
  if (!Object.values(TIMEFRAMES).includes(seconds as 900 | 3600 | 14400 | 86400)) {
    throw new Error('Unsupported chart timeframe');
  }
  if (seconds === 900) return bars;
  const groups = new Map<number, Candle[]>();
  for (const bar of bars) {
    const bucket = Math.floor(bar.time / seconds) * seconds;
    const group = groups.get(bucket) ?? [];
    group.push(bar);
    groups.set(bucket, group);
  }
  return [...groups.entries()]
    .sort(([first], [second]) => first - second)
    .filter(([bucket, group]) =>
      group[0].time === bucket &&
      group.every((bar, index) => bar.time === bucket + index * 900) &&
      (group.length === seconds / 900 || (
        bucket + seconds > now && group.at(-1)!.time === bars.at(-1)!.time &&
        group.at(-1)!.time >= Math.floor(now / 900) * 900 - 900
      )),
    )
    .map(([bucket, group]) => ({
      time: bucket,
      open: group[0].open,
      high: Math.max(...group.map((bar) => bar.high)),
      low: Math.min(...group.map((bar) => bar.low)),
      close: group[group.length - 1].close,
      volume: group.reduce((sum, bar) => sum + bar.volume, 0),
    }));
}
