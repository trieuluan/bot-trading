import type { Candle } from './trading';

export type PaperTrade = {
  id: number;
  side: 'long' | 'short';
  entry_time: number;
  entry: number;
  quantity: number;
  contracts: number;
  stop: number;
  target: number;
  entry_fee: number;
  exit_time: number | null;
  exit: number | null;
  exit_fee: number;
  exit_reason: string | null;
  funding_cost: number;
  net_pnl: number | null;
};
export type PaperWallet = {
  initial_balance: number;
  balance: number;
  equity: number;
  unrealized_pnl: number;
  used_margin: number;
  available: number;
  fee_bps: number;
  slippage_bps: number;
  quote: {
    lastPrice: number;
    bid1: number;
    ask1: number;
    fairPrice: number;
    time: number;
    received_at: number;
  } | null;
  stale: boolean;
  error: string | null;
  funding_error: string | null;
  funding_cursor: number;
  position: PaperTrade | null;
  trades: PaperTrade[];
  candles: Candle[];
};
