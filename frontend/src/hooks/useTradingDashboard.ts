import { useCallback, useEffect, useState } from 'react';
import type { Snapshot } from '../types/trading';
import { fetchDashboard, setBotEnabled } from '../lib/api';
import { createPreviewSnapshot } from '../lib/demo';
import { subscribeFuturesStream } from '../lib/futures-stream';

export function useTradingDashboard() {
  const [snapshot, setSnapshot] = useState<Snapshot>(createPreviewSnapshot);
  const [connected, setConnected] = useState(false);
  const [connecting, setConnecting] = useState(true);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');

  const refresh = useCallback(async () => {
    try {
      setSnapshot(await fetchDashboard());
      setConnected(true);
    } catch {
      setConnected(false);
    } finally {
      setConnecting(false);
    }
  }, []);

  useEffect(() => {
    return subscribeFuturesStream(
      'parts=dashboard',
      { dashboard: (data) => setSnapshot(data as Snapshot) },
      (ok) => {
        setConnected(ok);
        setConnecting(false);
      },
      async () => ({ dashboard: await fetchDashboard() }),
    );
  }, []);

  async function toggle() {
    const enabled = !snapshot.bot.enabled;
    setBusy(true);
    setMessage('');
    try {
      await setBotEnabled(enabled);
      await refresh();
      setMessage(
        enabled ? 'Đã bật theo dõi tín hiệu. Không gửi lệnh tới sàn.' : 'Đã dừng tạo tín hiệu mới.',
      );
    } catch {
      setMessage('Không gửi được yêu cầu. Kiểm tra API rồi thử lại.');
    } finally {
      setBusy(false);
    }
  }

  return { snapshot, connected, connecting, busy, message, setMessage, refresh, toggle };
}
