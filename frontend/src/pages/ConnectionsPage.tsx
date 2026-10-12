export function ConnectionsPage() {
  return (
    <section className="panel prose">
      <h2>Một lõi, nhiều kết nối</h2>
      <p>
        Lớp MarketDataAdapter tách nguồn dữ liệu khỏi chiến lược. Hiện chỉ Demo adapter hoạt động.
      </p>
      <div className="exchange-grid">
        {[
          ['Demo', 'Đang sử dụng', 'Synthetic replay · BTC/USDT'],
          ['Binance', 'Chưa tích hợp', 'Dự kiến: dữ liệu public spot'],
          ['OKX', 'Chưa tích hợp', 'Dự kiến: adapter crypto'],
          ['Forex / MT5', 'Chưa tích hợp', 'Cần connector riêng'],
        ].map(([name, status, description]) => (
          <div className="exchange-card" key={name}>
            <div className="exchange-icon">{name[0]}</div>
            <h3>{name}</h3>
            <p>{description}</p>
            <span className={`pill ${name === 'Demo' ? 'green' : ''}`}>{status}</span>
          </div>
        ))}
      </div>
      <p>Chưa thu thập hoặc lưu API key trong bản khung này.</p>
    </section>
  );
}
