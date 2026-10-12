import { FlaskConical } from 'lucide-react';

export function StrategyPage() {
  return (
    <section className="panel prose">
      <div className="section-head">
        <h2>Structure Breakout</h2>
        <span className="pill amber">Nghiên cứu · chưa kiểm chứng lợi nhuận</span>
      </div>
      <p>Một chiến lược có quy tắc rõ để kiểm tra pipeline dữ liệu và tín hiệu.</p>
      <div className="rules-grid">
        {[
          [
            '01',
            'Điều kiện vào',
            'LONG khi close vượt đỉnh 20 nến; SHORT khi phá đáy 20 nến (Futures).',
          ],
          ['02', 'Stop-loss tham chiếu', 'LONG: đáy 5 nến trước. SHORT: đỉnh 5 nến trước.'],
          [
            '03',
            'Mục tiêu tham chiếu',
            'Cách giá tín hiệu 2R theo chiều lệnh. Backtest vào ở nến kế tiếp.',
          ],
        ].map(([step, title, description]) => (
          <div className="rule-card" key={step}>
            <span>{step}</span>
            <h3>{title}</h3>
            <p>{description}</p>
          </div>
        ))}
      </div>
      <div className="rule">
        <FlaskConical size={20} />
        Mở trang Backtest để đánh giá trên dữ liệu MEXC Futures với phí, trượt giá và funding. Kết
        quả lịch sử chưa xác nhận hiệu quả tương lai.
      </div>
    </section>
  );
}
