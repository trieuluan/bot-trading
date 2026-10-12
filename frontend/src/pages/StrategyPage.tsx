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
          ['01', 'Điều kiện vào', 'Nến đã đóng có close lớn hơn high lớn nhất của 20 nến trước.'],
          ['02', 'Stop-loss tham chiếu', 'Đáy thấp nhất của 5 nến trước thời điểm vào.'],
          ['03', 'Mục tiêu tham chiếu', 'Entry + 2 × khoảng cách từ entry đến stop-loss.'],
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
        Backtest, phí, trượt giá và mô phỏng khớp lệnh là bước tiếp theo; hiện chưa có báo cáo hiệu
        suất.
      </div>
    </section>
  );
}
