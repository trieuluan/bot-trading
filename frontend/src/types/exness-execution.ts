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
  tp_reason?: string;
  exit_variant?: string;
  exit_variant_mode?: string;
  sl_buffer_k?: number;
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
    engine?: string;
    execution_v2?: {
      enabled: boolean;
      v2_enabled: boolean;
      exness_enabled: boolean;
      account_enabled: boolean;
      blocked_reason: string | null;
      /** Cây được phép vào lệnh (ungated: cả quần thể). */
      entry_allowed_count: number;
      entry_allowed_ids?: string[];
      /** Cây đủ chuẩn tiến hoá/làm bố mẹ: >=10 mẫu forward và mean R > 0. */
      evolution_ready_count: number;
      selected: string | null;
    };
    scalping?: {
      status: string;
      strategy?: string;
      rule?: string;
      executor?: string;
      replay?: {
        candles: number;
        from: number | null;
        to: number | null;
        train: ScalpStats;
        validation: ScalpStats;
        ambiguous_stop_first: number;
      } | null;
      forward?: ScalpStats;
      gate?: string[];
      train_candidate?: string | null;
      variants?: {
        id: string;
        label: string;
        train: ScalpStats;
        validation: ScalpStats;
        forward: ScalpStats;
        gate: string[];
      }[];
    };
    exit_learning?: {
      default_variant: string;
      costs_verified: boolean;
      sample_count: number;
      contexts: Record<string, { selected: string; recommended: string; eligible: boolean }>;
    };
    mode?: string;
    reason?: string;
    updated_at?: number;
    processed_bars?: number;
    evolution?: {
      cap: number;
      families: Record<string, number>;
      shadow: number;
      gate: string;
    };
    promoted?: string[];
    shadow_details?: {
      name: string;
      status: 'shadow' | 'promoted' | 'retired';
      train: number;
      validation: number;
      lower_r_train: number | null;
      lower_r_validation: number | null;
      reasons: string[];
    }[];
    retired_genomes?: string[];
    htf_ast2_shadow?: {
      status: string;
      generation: number;
      last_capture: { time: number; at: number; status: string } | null;
      last_evolution: { at: number; generation: number } | null;
      next_evolution_at?: number | null;
      tree_count: number;
      since: number | null;
      pending: number;
      resolved: number;
      excluded: number;
      mean_r: number | null;
      note: string;
      excluded_reasons?: Record<string, number>;
      rejections?: Record<string, number>;
      management_comparison?: {
        paired_count: number;
        h1_mean_r: number | null;
        m15_mean_r: number | null;
      };
      trees: {
        id: string;
        description: string;
        h1_setup: string;
        m15_trigger: string;
        exit_policy: string;
        exit_params?: Record<string, number>;
        generation?: number;
        parents?: string[];
        eligible?: boolean;
        signals: number;
        pending: number;
        resolved: number;
        excluded: number;
        mean_r: number | null;
        lower_r: number | null;
        last_levels: { stop: number; target: number; reason: string; time: number } | null;
      }[];
    };
    forward_shadow?: {
      since: number | null;
      updated_at?: number;
      signals: number;
      strategies: {
        strategy: string;
        version: string;
        signals: number;
        open: number;
        resolved: number;
        excluded: number;
        costs_verified: boolean;
        mean_r: number | null;
        lower_r: number | null;
      }[];
      recent: {
        strategy: string;
        strategy_version: string;
        side: string;
        confirm_time: number;
        status: string;
        outcome?: string;
        net_r_ex_swap?: number;
        costs: {
          slippage_bps: number;
          commission_per_unit: number | null;
          verified: boolean;
        };
      }[];
      last_capture?: {
        time: number;
        at: number;
        status: string;
        selected?: string | null;
        mode?: string;
        confirmed?: number;
      };
    };
    strategy_summary?: Record<
      string,
      {
        signals: number;
        closed: number;
        excluded: number;
        mean_r: number | null;
      }
    >;
    validation?: {
      selected_closed: number;
      selected_mean_r: number | null;
      baseline_mean_r: number | null;
      eligible: boolean;
    };
    latest?: { time: number; selected: string | null; mode?: string };
    broker_learning?: {
      closed_labels: number;
      fees_included: boolean;
      note: string;
    };
    assumptions?: {
      slippage_bps_each_side: number;
      slippage_measurement?: { bps: number; fills: number; measured: boolean };
    };
  };
  configured: boolean;
  enabled: boolean;
  worker_online: boolean;
  heartbeat: number | null;
  account: string;
  strategy: string;
  lot: string;
  max_concurrent: number;
  max_concurrent_per_symbol: number;
  symbol_open_count: number;
  planned_risk_ceiling_usd?: number;
  stop_management_available?: boolean;
  decision: string | null;
  error: string | null;
  synced_at: number | null;
  history_from: number | null;
  account_state: {
    balance: string;
    equity: string;
    used_margin: string;
  } | null;
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
  journal?: {
    time: number;
    at: number;
    evaluated: boolean;
    outcome: string;
    reason?: string;
    mode?: string;
    selected?: string | null;
    confirmed: { strategy: string; side?: string; shadow?: boolean }[];
    strategies?: { strategy: string; status: string; reason?: string }[];
    events?: { at: number; outcome: string }[];
  }[];
  funnel?: {
    candles: number;
    evaluated: number;
    with_signal: number;
    selected: number;
    orders: number;
    outcomes: Record<string, number>;
    by_strategy: Record<string, number>;
  };
  intents: {
    signal_time: number;
    status: string;
    reason: string;
    error?: string;
    ack?: { operation_id?: string };
  }[];
}

interface ScalpStats {
  signals: number;
  resolved: number;
  excluded: number;
  mean_r: number | null;
  lower_r: number | null;
  win_rate: number | null;
}

export interface LearnerAnalysis {
  candle_time: number;
  features: {
    trend: string;
    utc_session: number;
    spread: number;
    body: number;
    range: number;
  };
  levels: {
    m15_donchian: { high: number; low: number } | null;
    m15_ema20: number | null;
    h1_ema20: number | null;
    h1_ema50: number | null;
    barriers: { price: number; side: 'long' | 'short'; reason: string }[];
  };
  strategies: Record<
    string,
    {
      status: string;
      side?: string | null;
      reason?: string | null;
      stop?: number | null;
      level?: number | null;
      chart?: {
        levels: { label: string; price: number; from: number; to: number }[];
        oscillator: {
          label: string;
          low: number;
          high: number;
          points: { time: number; value: number }[];
        } | null;
        note: string | null;
        gap?: boolean;
        parameters: Record<string, unknown>;
        as_of: number;
        price_type: 'bid';
      };
    }
  >;
  selected_signal: {
    strategy: string;
    side: 'long' | 'short';
    reason: string;
    stop: number;
    projected_entry: number | null;
    projected_stop: number | null;
    projected_tp: number | null;
    tp_reason?: string;
    exit_variant?: string;
    sl_buffer_k?: number;
    tp_mode?: string;
    projection_error?: string | null;
  } | null;
  policy_mode: string;
  status: 'ready' | 'stale';
  source: string;
  issues: string[];
  available_at: number;
  policy_updated_at: number | null;
  scores: unknown[];
}
