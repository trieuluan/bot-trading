export type Candle = {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
};
export type Signal = {
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
  candles: Candle[];
  signal_count: number;
  signals: Signal[];
  events: { id: number; time: string; kind: string; message: string }[];
};
