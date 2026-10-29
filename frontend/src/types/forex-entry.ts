import type { LearnerAnalysis } from './exness-execution';

export interface ForexSignalChart {
  symbol: string;
  strategy: string;
  available_at: number;
  candle_time: number;
  expires_at: number;
  row: LearnerAnalysis['strategies'][string];
}

export interface ForexEntryIntent {
  id: string;
  setup_id?: string;
  strategy: string;
  side: 'long' | 'short';
  status: string;
  available_at?: number;
  created_at?: number;
  triggered_at?: number;
  ended_at?: number;
  reason?: string;
  reason_code?: string;
  reason_detail?: string;
  limit_price: number | null;
  stop_price: number | null;
  expires_at: number;
  winner?: 'limit' | 'stop' | 'market';
  trigger_quote?: { bid: number; ask: number; time: number };
  signal?: { stop?: number; target?: number };
  broker_fills?: { position_id: string; price: number; deviation: number | null; submitted_at?: number; filled_at?: number }[];
}

export interface ForexEntryPilot {
  enabled: boolean;
  status?: string;
  error?: string;
  pilot: {
    status: string;
    tree_count: number;
    since?: number;
    signals?: ForexRecordedSignal[];
    shadow_trades?: ForexShadowTrade[];
    counts?: Record<string, number>;
    comparison?: {
      matched_signals: number;
      resolved_pairs: number;
      market_states: Record<string, number>;
      hybrid_states: Record<string, number>;
      market_mean_r: number | null;
      hybrid_mean_r: number | null;
      delta_r: number | null;
    };
    error?: string;
    note?: string;
    generation?: number;
    bundle_count?: number;
    bundles?: Record<string, { matched_count: number; ready: boolean; mean_r: number | null; running_setups: number }>;
    policy_stats?: Record<string, { tree_count: number; resolved_count: number; mean_r: number | null }>;
    trees?: {
      id: string;
      bundle_id?: string | null;
      generation?: number;
      control_tree_id: string;
      entry_policy: string;
      entry_params: Record<string, number>;
      h1_setup: string;
      m15_trigger: string;
      exit_policy: string;
      samples_count: number;
      resolved_count: number;
      status_counts?: Record<string, number>;
      mean_r: number | null;
    }[];
  };
  intents: ForexEntryIntent[];
}

export interface ForexRecordedSignal {
  strategy: string;
  side: 'long' | 'short';
  available_at: number;
  status: string;
  live_assigned?: boolean;
  setup_id?: string | null;
  bundle_id?: string | null;
}

export interface ForexShadowTrade {
  id: string;
  symbol: string;
  strategy: string;
  side: 'long' | 'short';
  status: string;
  available_at: number;
  policy?: string;
  live_assigned?: boolean;
  reason?: string;
  resolution_seconds?: number;
  entry?: number | null;
  entry_time?: number | null;
  resolved_at?: number | null;
  net_r?: number | null;
  valuation?: {
    risk: number | null;
    risk_usd?: number | null;
    realized_r: number | null;
    slippage_bps: number | null;
    commission_per_unit: number | null;
    verified: boolean;
    gain_rate: number | null;
    loss_rate: number | null;
  };
  planned_levels?: { label: string; price: number }[];
  legs?: {
    role: string;
    open: boolean;
    weight?: number | null;
    volume?: number | null;
    stop?: number | null;
    target?: number | null;
    outcome?: string;
  }[];
  exits?: {
    time: number;
    price: number;
    role?: string;
    reason?: string;
  }[];
}
