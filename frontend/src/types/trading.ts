export type Candle = {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
};
export type Signal = {
  source: string;
  symbol: string;
  timeframe: string;
  id: number;
  time: number;
  side: string;
  entry: number;
  stop: number;
  target: number;
  reason: string;
};
export type Snapshot = {
  mode: string;
  data_source: string;
  symbol: string;
  timeframe: string;
  bot: { enabled: boolean; online: boolean; heartbeat: string | null; last_bar: number };
  market: {
    last_sync: string | null;
    last_closed_time: number | null;
    error: string | null;
    retry_at: string | null;
    stale: boolean;
  };
  candles: Candle[];
  signal_count: number;
  signals: Signal[];
  events: { id: number; time: string; kind: string; message: string }[];
};

export type BacktestTrade = {
  stop: number;
  target: number;
  side: string;
  signal_time: number;
  entry_time: number;
  exit_time: number;
  entry: number;
  exit: number;
  contracts: number;
  quantity: number;
  net_pnl: number;
  fees: number;
  funding_cost: number;
  exit_reason: string;
};
export type BacktestPeriod = {
  start_time: number;
  end_time: number;
  bars: number;
  initial_capital: number;
  final_equity: number;
  net_pnl: number;
  return_percent: number;
  max_drawdown_percent: number;
  trade_count: number;
  win_rate_percent: number;
  profit_factor: number | null;
  total_fees: number;
  funding_cost: number;
  trades: BacktestTrade[];
  equity_curve: { time: number; equity: number }[];
};
export type BacktestParameters = {
  days: number;
  initial_capital: number;
  risk_percent: number;
  fee_bps: number;
  slippage_bps: number;
};
export type BacktestReport = {
  id: number;
  created_at: string;
  source: string;
  result: {
    symbol: string;
    days: number;
    parameters: Omit<BacktestParameters, 'days'>;
    assumptions: string[];
    candles: Candle[];
    full: BacktestPeriod;
    in_sample: BacktestPeriod;
    out_of_sample: BacktestPeriod;
  };
};
