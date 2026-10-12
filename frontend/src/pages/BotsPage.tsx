import type { ReactNode } from 'react';
import { ShieldCheck } from 'lucide-react';
import type { Snapshot } from '../types/trading';

export function BotsPage({
  snapshot,
  botPanel,
  onOpenPaper,
}: {
  snapshot: Snapshot;
  botPanel: ReactNode;
  onOpenPaper: () => void;
}) {
  return (
    <div className="detail-grid">
      {botPanel}
      <section className="panel prose">
        <h2>Tiến trình bot độc lập</h2>
        <p>Auto Paper tự vào lệnh trong ví mô phỏng, dùng nút bật/dừng riêng.</p>
        <button className="secondary" onClick={onOpenPaper}>
          Mở Auto Paper và chart
        </button>
        <p>Worker đọc trạng thái từ database mỗi 5 giây. Đóng trình duyệt không dừng worker.</p>
        <p>Dừng bot chỉ ngừng phân tích nến mới. Các tín hiệu đã ghi nhận vẫn được giữ lại.</p>
        <div className="rule">
          <ShieldCheck size={20} />
          Không có chức năng gửi lệnh tới sàn trong phiên bản này.
        </div>
        <h3>Trạng thái dữ liệu</h3>
        <p>
          {snapshot.data_source === 'mexc_futures'
            ? `MEXC Futures · nến đóng mới nhất: ${snapshot.market.last_closed_time ? new Date(snapshot.market.last_closed_time * 1000).toISOString() : 'đang chờ'}`
            : `Đã xử lý ${snapshot.bot.last_bar} nến trên tối đa 2.000 nến mẫu.`}
        </p>
      </section>
    </div>
  );
}
