import { Check, X } from 'lucide-react';

export function HelpDialog({ onClose }: { onClose: () => void }) {
  return (
    <div className="modal-backdrop fixed inset-0 z-[70] bg-black/65 backdrop-blur-sm">
      <section role="dialog" aria-modal="true" aria-labelledby="help-title" className="modal fixed left-1/2 top-1/2 z-[80] max-h-[85dvh] w-[min(560px,calc(100vw-32px))] -translate-x-1/2 -translate-y-1/2 overflow-y-auto rounded-lg border border-border-active bg-surface-1 p-6 shadow-lg [&_p]:my-3 [&_p]:leading-relaxed">
        <button className="icon-button close inline-flex size-8 shrink-0 items-center justify-center rounded text-text-muted hover:text-text-primary absolute right-3 top-3" aria-label="Đóng hướng dẫn" onClick={onClose}>
          <X />
        </button>
        <div className="eyebrow text-xs font-semibold uppercase tracking-widest text-bull">GETTING STARTED</div>
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
        <p>Nguồn MEXC dùng dữ liệu thật; không gửi lệnh.</p>
        <button className="primary inline-flex items-center justify-center gap-2 rounded-md bg-bull px-3 py-2 text-xs font-semibold text-canvas hover:bg-emerald-300" onClick={onClose}>
          Đã hiểu <Check size={16} />
        </button>
      </section>
    </div>
  );
}
