import type { Candle } from './trading';

export type PaperTrade = {
  audit: {
    strategy: string;
    reason: string;
    signal_time: number;
    signal_close: number;
    range_start: number;
    range_end: number;
    range_high: number;
    range_low: number;
    risk_percent: number;
    risk_budget: number;
  } | null;
  entry_reason: string | null;
  id: number;
  origin: 'manual' | 'auto';
  signal_time: number | null;
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

export type AutoPaperConfig = {
  risk_percent: number;
  max_daily_trades: number;
  daily_loss_percent: number;
};
export type AutoDecision = {
  candle_time: number;
  evaluated_at: string;
  action: 'opened' | 'skipped' | 'no_setup';
  reason: string;
  setup: {
    side: string;
    entry: number;
    stop: number;
    target: number;
    reason: string;
  } | null;
  trade_id: number | null;
  parameters: AutoPaperConfig;
};
export type AutoPaperState = AutoPaperConfig & {
  enabled: boolean;
  last_candle: number | null;
  day: string;
  day_start_equity: number;
  daily_trades: number;
  daily_loss: number;
  daily_loss_used_percent: number;
  status: string;
  worker_online: boolean;
  next_close_time: number;
  decisions: AutoDecision[];
};
