import { useCallback, useEffect, useState } from 'react';
import type { Snapshot } from '../types/trading';
import { fetchDashboard, setBotEnabled } from '../lib/api';
import { createPreviewSnapshot } from '../lib/demo';

const POLL_INTERVAL_MS = 5000;

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
    void refresh();
    const interval = setInterval(() => void refresh(), POLL_INTERVAL_MS);
    return () => clearInterval(interval);
  }, [refresh]);

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
