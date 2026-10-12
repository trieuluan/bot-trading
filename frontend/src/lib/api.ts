import type { Snapshot } from '../types/trading';

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
