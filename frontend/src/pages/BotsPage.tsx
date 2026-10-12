import type { ReactNode } from 'react';
import { ShieldCheck } from 'lucide-react';
import type { Snapshot } from '../types/trading';

export function BotsPage({ snapshot, botPanel }: { snapshot: Snapshot; botPanel: ReactNode }) {
  return (
    <div className="detail-grid">
      {botPanel}
      <section className="panel prose">
        <h2>Tiến trình bot độc lập</h2>
        <p>Worker đọc trạng thái từ database mỗi 5 giây. Đóng trình duyệt không dừng worker.</p>
        <p>Dừng bot chỉ ngừng phân tích nến mới. Các tín hiệu đã ghi nhận vẫn được giữ lại.</p>
        <div className="rule">
          <ShieldCheck size={20} />
          Không có chức năng gửi lệnh tới sàn trong phiên bản này.
        </div>
        <h3>Trạng thái replay</h3>
        <p>Đã xử lý {snapshot.bot.last_bar} nến trên tối đa 2.000 nến mẫu.</p>
      </section>
    </div>
  );
}
