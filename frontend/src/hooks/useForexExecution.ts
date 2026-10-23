import { useEffect, useState } from 'react';
import { subscribeForexStream } from '../lib/forex-stream';
import type { ExnessExecution } from '../types/exness-execution';

/** Trạng thái Exness qua SSE dùng chung với workspace. */
export function useForexExecution(symbol: string) {
  const [data, setData] = useState<ExnessExecution | null>(null);
  const [error, setError] = useState(false);
  useEffect(() => {
    setData(null);
    return subscribeForexStream(
      symbol,
      'execution',
      (value) => {
        const next = value as ExnessExecution;
        if (next.symbol === symbol) setData(next);
      },
      (online, connecting) => setError(!online && !connecting),
    );
  }, [symbol]);
  return { data: data?.symbol === symbol ? data : null, error };
}
