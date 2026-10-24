import type { Candle } from './trading';
import type { Genome } from '../lib/futures-strategy-info';

export type PaperTrade = {
  monitoring: {
    status: 'watching' | 'history_pending' | 'recovered';
    next_bar: number;
    exit_policy?: string;
    original_quantity?: number;
    original_entry_fee?: number;
    realized_net?: number;
    partial_exits?: {
      time: number;
      price: number;
      quantity: number;
      entry_fee: number;
      exit_fee: number;
      gross: number;
    }[];
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
    genome?: Genome | null;
    generation?: number;
    parents?: string[];
    exit_policy?: string;
    strategy_source?: 'entry_snapshot' | 'registry' | 'missing';
    initial_stop?: number;
    initial_target?: number;
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
export type PaperPendingOrder = {
  id: number;
  symbol: string;
  signal_time: number;
  created_at: number;
  expires_at: number;
  next_bar: number;
  side: 'long' | 'short';
  entry_policy: string;
  strategy: string;
  level: number;
  stop: number;
  target: number;
  status: 'pending' | 'filled' | 'cancelled' | 'expired';
  reason: string | null;
  trade_id: number | null;
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
  open_interest?: {
    contracts: number;
    change_24h_pct: number | null;
  } | null;
  quote: {
    lastPrice: number;
    bid1: number;
    ask1: number;
    fairPrice: number;
    fundingRate?: number;
    nextFundingTime?: number;
    time: number;
    received_at: number;
    source?: 'websocket' | 'rest';
  } | null;
  stale: boolean;
  error: string | null;
  funding_error: string | null;
  funding_cursor: number;
  position: PaperTrade | null;
  positions: PaperTrade[];
  pending_orders?: PaperPendingOrder[];
  trades: PaperTrade[];
  candles: Candle[];
};

export type AutoPaperConfig = {
  risk_percent: number;
  leverage: number;
  max_daily_trades: number;
  daily_loss_percent: number;
  max_portfolio_risk_percent?: number | null;
};
export type AutoDecision = {
  candle_time: number;
  evaluated_at: string;
  action: 'opened' | 'skipped' | 'no_setup' | 'pending';
  reason: string;
  setup: {
    side: string;
    entry: number;
    stop: number;
    target: number;
    reason: string;
  } | null;
  trade_id: number | null;
  parameters: AutoPaperConfig & { audit?: PaperTrade['audit'] };
};
export type FuturesStrategyObservation = {
  strategy: string;
  genome?: Genome | null;
  status: string;
  side: string | null;
  setup?: AutoDecision['setup'];
  levels?: { title: string; price: number; from?: number; to?: number }[];
  curves?: { title: string; points: { time: number; value: number }[] }[];
  markers?: { time: number; side: 'long' | 'short'; title: string }[];
  overlay_details?: {
    feature: string;
    frame: string;
    label: string;
    matched: string[];
    drawn: boolean;
    message: string;
  }[];
  note?: string;
};
export type AutoPaperState = AutoPaperConfig & {
  portfolio_capital?: number;
  portfolio_used_risk?: number;
  auto_mode: 'learner' | 'paper_learning';
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
  coverage?: {
    window_candles: number;
    end_time: number;
    start_time: number;
    tracked_candles: number;
    evaluated: number;
    unrecorded: number;
    opened: number;
    skipped: number;
    no_setup: number;
    missed: number;
    pending?: number;
  };
  timeline?: {
    candle_time: number;
    action: 'opened' | 'skipped' | 'no_setup' | 'missed' | 'pending';
    reason: string;
    trade_id: number | null;
    side: 'long' | 'short' | null;
  }[];
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
