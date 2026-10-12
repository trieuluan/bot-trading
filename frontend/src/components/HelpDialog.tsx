import { Check, X } from 'lucide-react';

export function HelpDialog({ onClose }: { onClose: () => void }) {
  return (
    <div className="modal-backdrop">
      <section role="dialog" aria-modal="true" aria-labelledby="help-title" className="modal">
        <button className="icon-button close" aria-label="Đóng hướng dẫn" onClick={onClose}>
          <X />
        </button>
        <div className="eyebrow">GETTING STARTED</div>
        <h2 id="help-title">Bắt đầu với Signal Desk</h2>
        <ol>
          <li>Chạy Docker Compose hoặc API và worker cục bộ.</li>
          <li>Chờ trạng thái API và worker kết nối.</li>
          <li>Chọn MEXC Futures tại Kết nối sàn, rồi bật theo dõi tín hiệu.</li>
          <li>Mở Ví mô phỏng → lưu rủi ro và giới hạn ngày → Bật Auto Paper.</li>
          <li>
            Quan sát lệnh, SL/TP và nhật ký quyết định trên chart. Dừng auto ngừng vào lệnh mới.
          </li>
        </ol>
        <p>Mở Backtest để kiểm chứng chiến lược. Nguồn MEXC dùng dữ liệu thật; không gửi lệnh.</p>
        <button className="primary" onClick={onClose}>
          Đã hiểu <Check size={16} />
        </button>
      </section>
    </div>
  );
}
