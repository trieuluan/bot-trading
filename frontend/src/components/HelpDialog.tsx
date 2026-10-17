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
          <li>Chọn workspace MEXC, mở Cài đặt & Kết nối để chọn nguồn MEXC Futures.</li>
          <li>
            Mở Giao dịch & Bot → lưu rủi ro và giới hạn ngày → Bật Auto Paper khi đã kiểm chứng
            chiến lược.
          </li>
          <li>
            Quan sát lệnh, SL/TP và nhật ký quyết định trên chart. Dừng auto ngừng vào lệnh mới.
          </li>
        </ol>
        <p>
          Mở Nghiên cứu → Backtest để kiểm chứng chiến lược. Nghiên cứu → Tín hiệu điều khiển bot
          phân tích riêng. Nguồn MEXC dùng dữ liệu thật; không gửi lệnh.
        </p>
        <button className="primary" onClick={onClose}>
          Đã hiểu <Check size={16} />
        </button>
      </section>
    </div>
  );
}
