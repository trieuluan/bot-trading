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
          <li>Bật phân tích mô phỏng trong bảng bot.</li>
          <li>Xem tín hiệu và nhật ký; dừng bất cứ lúc nào.</li>
        </ol>
        <p>Dữ liệu là tổng hợp. Chưa có backtest hoặc giao dịch thật.</p>
        <button className="primary" onClick={onClose}>
          Đã hiểu <Check size={16} />
        </button>
      </section>
    </div>
  );
}
