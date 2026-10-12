import type { Snapshot } from '../types/trading';
export function createPreviewSnapshot(): Snapshot {
  let price = 62500;
  const candles = Array.from({ length: 65 }, (_, i) => {
    const open = price;
    price += Math.sin(i * 1.7) * 120 + Math.cos(i / 6) * 70 + 35;
    return {
      time: 1791763200 + i * 900,
      open,
      close: price,
      high: Math.max(open, price) + 90 + Math.sin(i) * 35,
      low: Math.min(open, price) - 80,
      volume: 100 + Math.abs(Math.sin(i)) * 250,
    };
  });
  return {
    mode: 'paper',
    data_source: 'synthetic',
    symbol: 'BTC/USDT',
    timeframe: '15m',
    bot: { enabled: false, online: false, heartbeat: null, last_bar: 65 },
    candles,
    signal_count: 0,
    signals: [],
    events: [],
  };
}
