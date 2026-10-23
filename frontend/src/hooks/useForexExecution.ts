import { useEffect, useState } from 'react';
import type { ExnessExecution } from '../types/exness-execution';

/** Trạng thái thực thi Exness (trang 1 lệnh) cho Tổng quan / Kết nối sàn, poll 15s. */
export function useForexExecution(symbol: string) {
  const [data, setData] = useState<ExnessExecution | null>(null);
  const [error, setError] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    let inflight = false;
    setData(null);
    const refresh = async () => {
      if (inflight) return;
      inflight = true;
      try {
        const response = await fetch(
          `/api/workspaces/exness_forex/execution?symbol=${encodeURIComponent(symbol)}&limit=1`,
          { signal: AbortSignal.any([controller.signal, AbortSignal.timeout(10000)]) },
        );
        if (!response.ok) throw new Error();
        const next: ExnessExecution = await response.json();
        if (!controller.signal.aborted) {
          setData(next);
          setError(false);
        }
      } catch {
        if (!controller.signal.aborted) setError(true);
      } finally {
        inflight = false;
      }
    };
    void refresh();
    const timer = window.setInterval(() => {
      if (!document.hidden) void refresh();
    }, 15000);
    return () => {
      controller.abort();
      window.clearInterval(timer);
    };
  }, [symbol]);
  return { data, error };
}
