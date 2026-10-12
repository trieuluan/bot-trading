import type { Snapshot, BacktestParameters, BacktestReport } from '../types/trading';

const REQUEST_TIMEOUT_MS = 5000;

export async function fetchDashboard(): Promise<Snapshot> {
  const response = await fetch('/api/dashboard', {
    signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
  });
  if (!response.ok) {
    throw new Error(`Dashboard request failed: ${response.status}`);
  }
  return response.json();
}

export async function setBotEnabled(enabled: boolean): Promise<void> {
  const response = await fetch('/api/bot/control', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ enabled }),
    signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
  });
  if (!response.ok) {
    throw new Error(`Bot control request failed: ${response.status}`);
  }
}

async function researchRequest(path: string, body?: object) {
  const response = await fetch(path, {
    method: body ? 'POST' : 'GET',
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
    signal: AbortSignal.timeout(180_000),
  });
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new Error(
      typeof data.detail === 'string' ? data.detail : `Yêu cầu thất bại (${response.status})`,
    );
  }
  return response.json();
}
export function selectMarket(source: 'synthetic' | 'mexc_futures'): Promise<void> {
  return researchRequest('/api/market/config', { source });
}
export function runBacktest(parameters: BacktestParameters): Promise<BacktestReport> {
  return researchRequest('/api/backtests', parameters);
}
export function fetchLatestBacktest(): Promise<BacktestReport | null> {
  return researchRequest('/api/backtests/latest');
}

export function fetchPaperWallet(): Promise<import('../types/paper').PaperWallet> {
  return paperRequest('/api/paper/wallet');
}
async function paperRequest(path: string, body?: object) {
  const response = await fetch(path, {
    method: body ? 'POST' : 'GET',
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
    signal: AbortSignal.timeout(10000),
  });
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new Error(
      typeof data.detail === 'string' ? data.detail : `Yêu cầu thất bại (${response.status})`,
    );
  }
  return response.json();
}
export function openPaperOrder(order: {
  request_id: string;
  side: 'long' | 'short';
  notional: number;
  stop: number;
  target: number;
}): Promise<{ trade_id: number }> {
  return paperRequest('/api/paper/orders', order);
}
export function closePaperPosition(id: number, requestId: string): Promise<{ trade_id: number }> {
  return paperRequest(`/api/paper/positions/${id}/close`, {
    request_id: requestId,
  });
}

export function fetchAutoPaper(): Promise<import('../types/paper').AutoPaperState> {
  return paperRequest('/api/paper/auto');
}
export function configureAutoPaper(
  config: import('../types/paper').AutoPaperConfig,
): Promise<import('../types/paper').AutoPaperState> {
  return paperRequest('/api/paper/auto/config', config);
}
export function setAutoPaperEnabled(
  enabled: boolean,
): Promise<import('../types/paper').AutoPaperState> {
  return paperRequest('/api/paper/auto/control', { enabled });
}

export function fetchPaperTradeCandles(id: number): Promise<import('../types/trading').Candle[]> {
  return paperRequest(`/api/paper/trades/${id}/candles`);
}
