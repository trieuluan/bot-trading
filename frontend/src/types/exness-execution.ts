export interface BrokerPosition {
  position_id: string;
  side: 'buy' | 'sell' | null;
  volume: number | null;
  open_time: number | null;
  open_price: number | null;
  close_time: number | null;
  close_price: number | null;
  stop?: number | null;
  target?: number | null;
  status: string;
  source: string;
  signal_time?: number;
  reason?: string;
  close_requested?: boolean;
  pnl: number | null;
  role?: 'tp1' | 'runner';
  management_stage?: string;
  management_status?: string;
  management_error?: string;
  close_reason?: string;
  partial_taken?: boolean;
  deals: {
    deal_id: string;
    order_id?: string;
    type: string;
    direction?: string;
    event_time: number;
    price?: string;
    volume?: string;
    commission?: string;
    profit?: { amount: string; currency?: string };
  }[];
}

export interface ExnessExecution {
  learning?: {
    mode: string;
    reason: string;
    updated_at?: number;
    processed_bars?: number;
    strategy_summary?: Record<string, { signals: number; closed: number; excluded: number; mean_r: number | null }>;
    validation?: { selected_closed: number; selected_mean_r: number | null; baseline_mean_r: number | null; eligible: boolean };
    latest?: { time: number; selected: string | null; mode?: string };
    broker_learning?: { closed_labels: number; fees_included: boolean; note: string };
    assumptions?: { slippage_bps_each_side: number; slippage_measurement?: { bps: number; fills: number; measured: boolean } };
  };
  configured: boolean;
  enabled: boolean;
  worker_online: boolean;
  heartbeat: number | null;
  account: string;
  strategy: string;
  lot: string;
  max_concurrent: number;
  planned_risk_ceiling_usd?: number;
  stop_management_available?: boolean;
  decision: string | null;
  error: string | null;
  synced_at: number | null;
  history_from: number | null;
  account_state: { balance: string; equity: string; used_margin: string } | null;
  positions: BrokerPosition[];
  open_count: number;
  closed_count: number;
  broker_profit: number;
  total: number;
  has_more: boolean;
  orders: {
    order_id: string;
    order_type: string;
    instrument: string;
    volume?: string;
    price?: string;
  }[];
  intents: {
    signal_time: number;
    status: string;
    reason: string;
    error?: string;
    ack?: { operation_id?: string };
  }[];
}
