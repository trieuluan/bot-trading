import type { Candle } from './trading';

export type PaperTrade = {
  monitoring: {
    status: 'watching' | 'history_pending' | 'recovered';
    next_bar: number;
    trigger_bar?: number;
    ambiguous?: boolean;
    fill_basis?: 'level' | 'gap_open';
    execution_model?: 'historical_levels_v1';
    recovered_at?: number;
    time_basis?: 'bar_open' | 'bar_end_proxy';
  } | null;
  audit: {
    strategy: string;
    reason: string;
    signal_time: number;
    signal_close: number;
    range_start?: number;
    range_end?: number;
    range_high?: number;
    range_low?: number;
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
  leverage?: number;
  margin_mode?: 'cross';
  initial_margin?: number;
};
export type PaperWallet = {
  initial_balance: number;
  balance: number;
  equity: number;
  unrealized_pnl: number;
  pair_unrealized_pnl: number;
  portfolio_stale_symbols?: string[];
  wallet_scope?: 'shared';
  used_margin: number;
  available: number;
  min_leverage?: number;
  max_leverage?: number | null;
  margin_mode?: 'cross';
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
  positions: PaperTrade[];
  trades: PaperTrade[];
  candles: Candle[];
};

export type AutoPaperConfig = {
  risk_percent: number;
  leverage: number;
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
export type FuturesStrategyObservation = {
  strategy: string;
  status: string;
  side: string | null;
  setup?: AutoDecision['setup'];
  levels?: { title: string; price: number; from?: number; to?: number }[];
  curves?: { title: string; points: { time: number; value: number }[] }[];
  note?: string;
};
export type AutoPaperState = AutoPaperConfig & {
  auto_mode: 'legacy' | 'learner' | 'paper_learning';
  min_leverage?: number;
  max_leverage?: number | null;
  margin_mode?: 'cross';
  learner_enabled: boolean;
  learner_error: string | null;
  learner_decision: {
    time: number;
    regime: string;
    candidates: number;
    mode: string;
    auto_mode: string;
    selected: string | null;
    selection_reason: string;
    setup: AutoDecision['setup'];
    strategies?: FuturesStrategyObservation[];
  } | null;
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
export interface FuturesHistoryStats {
  resolved: number;
  mean_r: number | null;
  profit_factor: number | null;
  no_losses: boolean;
  signals: number;
  excluded: number;
}

export interface FuturesHistoryJob {
  id: string;
  status: 'running' | 'completed' | 'failed';
  phase: string;
  days: number;
  completed: number;
  total: number;
  error: string | null;
  report: {
    start_time: number;
    end_time: number;
    train_before: number;
    eligible_priors: number;
    strategies: {
      strategy: string;
      train: FuturesHistoryStats;
      validation: FuturesHistoryStats;
      prior_eligible: boolean;
    }[];
  } | null;
}
