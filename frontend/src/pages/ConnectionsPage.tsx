import { useState } from 'react';
import type { Snapshot } from '../types/trading';
import { selectMarket } from '../lib/api';

export function ConnectionsPage({
  snapshot,
  connected,
  onRefresh,
}: {
  snapshot: Snapshot;
  connected: boolean;
  onRefresh: () => Promise<void>;
}) {
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  async function choose(source: 'synthetic' | 'mexc_futures') {
    setBusy(true);
    setMessage('');
    try {
      await selectMarket(source);
      await onRefresh();
      setMessage(
        'Đã đổi nguồn dữ liệu. Bot được dừng khi chuyển nguồn; bật lại ở Nghiên cứu → Tín hiệu. Auto Paper có nút bật/dừng riêng tại Giao dịch & Bot.',
      );
    } catch (cause) {
      setMessage(cause instanceof Error ? cause.message : 'Không kết nối được nguồn dữ liệu.');
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="panel prose">
      <h2>Nguồn dữ liệu thị trường</h2>
      <p>
        MEXC Futures public API: BTC_USDT perpetual · 15 phút. Chart nhận nến đang chạy qua
        WebSocket; worker lưu nến đã đóng qua REST. Không cần API key. Bot lấy bù nến sau khi mất
        kết nối và kiểm tra khoảng trống trước khi tạo tín hiệu.
      </p>
      <div className="exchange-grid">
        {[
          {
            source: 'mexc_futures' as const,
            name: 'MEXC Futures',
            description: 'Chart WebSocket live · nến đóng REST 15 giây · khớp Paper REST 5 giây',
          },
          {
            source: 'synthetic' as const,
            name: 'Demo replay',
            description: 'Dữ liệu tổng hợp · chạy offline · phát lại tăng tốc',
          },
        ].map(({ source, name, description }) => (
          <div className="exchange-card" key={source}>
            <div className="exchange-icon">{name[0]}</div>
            <h3>{name}</h3>
            <p>{description}</p>
            <button
              className={snapshot.data_source === source ? 'primary' : 'secondary'}
              disabled={!connected || busy || snapshot.data_source === source}
              onClick={() => void choose(source)}
            >
              {snapshot.data_source === source
                ? 'Đang sử dụng'
                : busy
                  ? 'Đang kết nối…'
                  : 'Sử dụng nguồn này'}
            </button>
          </div>
        ))}
      </div>
      {message && <p role="status">{message}</p>}
      {snapshot.data_source === 'mexc_futures' && (
        <p>
          Lần đồng bộ:{' '}
          {snapshot.market.last_sync
            ? new Date(snapshot.market.last_sync).toLocaleString('vi-VN')
            : 'Chưa có'}{' '}
          · {snapshot.market.stale ? 'Dữ liệu chậm cập nhật' : 'Đang cập nhật'}
          {snapshot.market.error && ` · ${snapshot.market.error}`}
        </p>
      )}
      <div className="rule">
        MEXC hiện không cung cấp sandbox cho API. Demo Trading trên web và endpoint kiểm tra lệnh
        Spot không phải môi trường khớp lệnh thử cho bot.
      </div>
      <p>
        Chưa thu thập API key hoặc bật quyền đặt lệnh. Các sàn khác sẽ được thêm qua adapter riêng.
      </p>
      <a href="https://www.mexc.com/mexc-api" target="_blank" rel="noreferrer">
        FAQ API chính thức của MEXC ↗
      </a>
    </section>
  );
}
