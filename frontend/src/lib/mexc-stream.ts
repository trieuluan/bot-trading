import type { Candle } from '../types/trading';

export const MEXC_STREAM_URL = 'wss://contract.mexc.com/edge';

export function parseMexcCandle(raw: string, now = Date.now(), symbol = 'BTC_USDT'): Candle | null {
  try {
    const message = JSON.parse(raw);
    const data = message?.data;
    if (
      message?.channel !== 'push.kline' ||
      message.symbol !== symbol ||
      data?.symbol !== symbol ||
      data.interval !== 'Min15' ||
      typeof message.ts !== 'number' ||
      !Number.isFinite(message.ts) ||
      Math.abs(now - message.ts) > 30_000
    )
      return null;
    const candle: Candle = {
      time: data.t,
      open: data.o,
      high: data.h,
      low: data.l,
      close: data.c,
      volume: data.q,
    };
    if (
      !Object.values(candle).every(
        (value) => typeof value === 'number' && Number.isFinite(value),
      ) ||
      candle.time !== Math.floor(now / 900_000) * 900 ||
      Math.min(candle.open, candle.high, candle.low, candle.close) <= 0 ||
      candle.low > Math.min(candle.open, candle.close) ||
      candle.high < Math.max(candle.open, candle.close) ||
      candle.volume < 0
    )
      return null;
    return candle;
  } catch {
    return null;
  }
}

export function withLiveCandle(candles: Candle[], live: Candle | null): Candle[] {
  if (!live || (candles.at(-1)?.time ?? 0) > live.time) return candles;
  return [...candles.filter((candle) => candle.time < live.time), live];
}
