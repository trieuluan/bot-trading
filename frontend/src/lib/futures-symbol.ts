export const FUTURES_SYMBOLS = [
  'BTC_USDT', 'ETH_USDT', 'SOL_USDT', 'XRP_USDT', 'DOGE_USDT',
  'AVAX_USDT', 'SUI_USDT', 'NEAR_USDT', 'BNB_USDT', 'LINK_USDT',
] as const;

export type FuturesSymbol = (typeof FUTURES_SYMBOLS)[number];
let selected: FuturesSymbol = 'BTC_USDT';

export function getFuturesSymbol() {
  return selected;
}

export function setFuturesSymbol(symbol: FuturesSymbol) {
  selected = symbol;
}

export function futuresHeaders() {
  return { 'X-Futures-Symbol': selected };
}
