import { useMemo, useState } from 'react';
import { ChevronLeft, ChevronRight, LocateFixed } from 'lucide-react';
import type { BacktestPeriod, Candle } from '../types/trading';
import { formatPrice } from '../lib/format';
import { TradeCandlestickChart } from './TradeCandlestickChart';

const exitLabels: Record<string, string> = {
  stop: 'Chạm stop-loss',
  stop_gap: 'Gap qua stop-loss',
  target: 'Chạm take-profit',
  end_of_period: 'Đóng cuối kỳ',
};

export function TradeReview({ candles, result }: { candles: Candle[]; result: BacktestPeriod }) {
  const [selectedIndex, setSelectedIndex] = useState(Math.max(0, result.trades.length - 1));
  const [focusRevision, setFocusRevision] = useState(0);
  const selected = result.trades[selectedIndex];
  const periodCandles = useMemo(
    () =>
      candles.filter(
        (candle) => candle.time >= result.start_time - 20 * 900 && candle.time < result.end_time,
      ),
    [candles, result.start_time, result.end_time],
  );
  return (
    <section className="trade-review" aria-labelledby="trade-review-title">
      <div className="section-head">
        <div>
          <h3 id="trade-review-title">Quan sát lệnh trên chart</h3>
          <p>Nến MEXC thật · khớp lệnh backtest mô phỏng · không phải lệnh trên sàn</p>
        </div>
        <span className="pill amber">BACKTEST FILLS</span>
      </div>
      <div className="trade-review-controls">
        <label>
          Chọn lệnh
          <select
            aria-label="Chọn lệnh để xem trên chart"
            value={selectedIndex}
            disabled={!result.trades.length}
            onChange={(event) => setSelectedIndex(Number(event.target.value))}
          >
            {result.trades.length ? (
              result.trades.map((trade, index) => (
                <option key={`${trade.entry_time}-${index}`} value={index}>
                  #{index + 1} · {trade.side.toUpperCase()} ·{' '}
                  {new Date(trade.entry_time * 1000).toISOString().slice(0, 16).replace('T', ' ')}{' '}
                  UTC
                </option>
              ))
            ) : (
              <option value={0}>Không có lệnh trong khoảng này</option>
            )}
          </select>
        </label>
        <div className="trade-review-buttons">
          <button
            className="secondary"
            aria-label="Lệnh trước"
            disabled={selectedIndex <= 0 || !selected}
            onClick={() => setSelectedIndex(selectedIndex - 1)}
          >
            <ChevronLeft size={16} />
          </button>
          <button
            className="secondary"
            aria-label="Lệnh tiếp theo"
            disabled={selectedIndex >= result.trades.length - 1 || !selected}
            onClick={() => setSelectedIndex(selectedIndex + 1)}
          >
            <ChevronRight size={16} />
          </button>
          <button
            className="secondary"
            disabled={!selected}
            onClick={() => setFocusRevision((revision) => revision + 1)}
          >
            <LocateFixed size={16} /> Về lệnh đã chọn
          </button>
        </div>
      </div>
      {selected && (
        <div className="selected-trade-summary" role="status">
          <strong>
            Lệnh #{selectedIndex + 1} · {selected.side.toUpperCase()}
          </strong>
          <span className={selected.net_pnl >= 0 ? 'positive' : 'negative'}>
            {formatPrice(selected.net_pnl)} USDT ròng
          </span>
          <span>{exitLabels[selected.exit_reason] ?? selected.exit_reason}</span>
          <span>{selected.contracts} hợp đồng</span>
          <span>
            Phí {formatPrice(selected.fees)} · Funding {formatPrice(selected.funding_cost)} USDT
          </span>
        </div>
      )}
      <TradeCandlestickChart
        candles={periodCandles}
        trades={result.trades}
        selectedIndex={selectedIndex}
        onSelect={setSelectedIndex}
        focusRevision={focusRevision}
      />
      {selected && (
        <div className="trade-execution-details">
          <span>
            Signal:{' '}
            {new Date(selected.signal_time * 1000).toISOString().slice(0, 16).replace('T', ' ')} UTC
          </span>
          <span>
            Entry: {formatPrice(selected.entry)} · SL: {formatPrice(selected.stop)} · TP:{' '}
            {formatPrice(selected.target)}
          </span>
          <span>
            Exit: {formatPrice(selected.exit)} ·{' '}
            {new Date(selected.exit_time * 1000).toISOString().slice(0, 16).replace('T', ' ')} UTC
          </span>
        </div>
      )}
      <p className="micro">
        SL/TP hiển thị là kế hoạch của lệnh đã chọn; entry/exit bao gồm trượt giá. Đường mức giá kéo
        ngang để tham chiếu. Khi cùng nến chạm cả SL và TP, backtest xử lý SL trước; không thể xác
        định thứ tự intrabar từ nến 15m.
      </p>
      <p className="chart-attribution">
        <a href="https://www.tradingview.com/" target="_blank" rel="noreferrer">
          TradingView Lightweight Charts™
        </a>{' '}
        · Copyright (с) 2025 TradingView, Inc. · Dữ liệu: MEXC
      </p>
    </section>
  );
}
