import { useEffect, useState, type FormEvent } from 'react';
import { FlaskConical, Play } from 'lucide-react';
import { fetchLatestBacktest, runBacktest } from '../lib/api';
import { TradeReview } from '../components/TradeReview';
import { EquityChart } from '../components/EquityChart';
import { formatPrice } from '../lib/format';
import type { BacktestReport } from '../types/trading';

const periods = { full: 'Toàn bộ', in_sample: '70% đầu', out_of_sample: '30% kiểm chứng' } as const;

export function BacktestPage({ connected }: { connected: boolean }) {
  const [report, setReport] = useState<BacktestReport | null>(null);
  const [period, setPeriod] = useState<keyof typeof periods>('out_of_sample');
  const [strategy, setStrategy] = useState<'breakout' | 'retest'>('breakout');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [days, setDays] = useState(30);
  const [capital, setCapital] = useState(10000);
  const [risk, setRisk] = useState(1);
  const [fee, setFee] = useState(5);
  const [slippage, setSlippage] = useState(2);

  useEffect(() => {
    let active = true;
    fetchLatestBacktest()
      .then((result) => {
        if (active) setReport(result);
      })
      .catch(() => {
        if (active) setError('Chưa tải được báo cáo đã lưu. Kiểm tra kết nối API.');
      });
    return () => {
      active = false;
    };
  }, []);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError('');
    try {
      setReport(
        await runBacktest({
          days,
          initial_capital: capital,
          risk_percent: risk,
          fee_bps: fee,
          slippage_bps: slippage,
        }),
      );
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Không chạy được backtest.');
    } finally {
      setBusy(false);
    }
  }

  const result = strategy === 'retest' ? report?.result.retest?.[period] : report?.result[period];
  return (
    <>
      <section className="panel prose">
        <div className="section-head">
          <h2>Kiểm chứng trên MEXC Futures</h2>
          <span className="pill">BTC_USDT · 15m · LONG / SHORT</span>
        </div>
        <p>
          Dữ liệu nến hợp đồng và funding thật từ MEXC. Lệnh vào/thoát được mô phỏng nội bộ theo
          lịch sử; không gửi lệnh tới sàn. Quy tắc cố định, chia thời gian 70/30; không tối ưu trên
          tập kiểm chứng.
        </p>
        <form className="research-form" onSubmit={(event) => void submit(event)}>
          <label>
            Khoảng dữ liệu
            <select
              value={days}
              onChange={(event) => setDays(Number(event.target.value))}
              disabled={busy}
            >
              <option value={7}>7 ngày gần nhất</option>
              <option value={30}>30 ngày gần nhất</option>
              <option value={90}>90 ngày gần nhất</option>
            </select>
          </label>
          <label>
            Vốn (USDT)
            <input
              type="number"
              min="100"
              max="100000000"
              required
              value={capital}
              onChange={(event) => setCapital(Number(event.target.value))}
              disabled={busy}
            />
          </label>
          <label>
            Rủi ro / lệnh (%)
            <input
              type="number"
              min="0.1"
              max="5"
              step="0.1"
              required
              value={risk}
              onChange={(event) => setRisk(Number(event.target.value))}
              disabled={busy}
            />
          </label>
          <label>
            Phí mỗi chiều (bps)
            <input
              type="number"
              min="0"
              max="100"
              step="0.1"
              required
              value={fee}
              onChange={(event) => setFee(Number(event.target.value))}
              disabled={busy}
            />
          </label>
          <label>
            Trượt giá mỗi chiều (bps)
            <input
              type="number"
              min="0"
              max="100"
              step="0.1"
              required
              value={slippage}
              onChange={(event) => setSlippage(Number(event.target.value))}
              disabled={busy}
            />
          </label>
          <button className="primary" type="submit" disabled={busy || !connected}>
            <Play size={16} />
            {busy ? 'Đang tải dữ liệu và backtest…' : 'Chạy backtest'}
          </button>
        </form>
        <p className="micro">
          1 bps = 0,01%. Phí là giả định có thể chỉnh, không phải mức phí tài khoản. Notional giới
          hạn 1× vốn; chưa mô phỏng liquidation, ADL hay khớp một phần. Funding dùng fair-price open
          làm giá xấp xỉ tại kỳ quyết toán.
        </p>
        {error && (
          <p role="alert" className="negative">
            {error}
          </p>
        )}
      </section>
      {!report && !busy && (
        <section className="panel prose">
          <FlaskConical size={24} />
          <h2>Chưa có báo cáo</h2>
          <p>
            Chạy backtest để lưu báo cáo và bộ nến đầu vào. Thao tác này không bật bot hoặc gửi lệnh
            tới sàn.
          </p>
        </section>
      )}
      {report && result && (
        <section className="panel prose backtest-results">
          <div className="section-head">
            <div>
              <h2>Báo cáo #{report.id}</h2>
              <p>
                {new Date(report.created_at).toLocaleString('vi-VN')} · {report.result.days} ngày ·{' '}
                {report.result.symbol}
              </p>
            </div>
            <a
              className="text-button"
              href={`/api/backtests/${report.id}`}
              target="_blank"
              rel="noreferrer"
            >
              Mở JSON + dữ liệu gốc ↗
            </a>
          </div>
          <p className="micro">
            Thông số báo cáo: vốn {formatPrice(report.result.parameters.initial_capital)} USDT · rủi
            ro {report.result.parameters.risk_percent}% · phí {report.result.parameters.fee_bps} bps
            · trượt giá {report.result.parameters.slippage_bps} bps.
          </p>
          <div className="period-tabs" role="group" aria-label="Chiến lược">
            <button
              className={strategy === 'breakout' ? 'secondary selected' : 'secondary'}
              onClick={() => setStrategy('breakout')}
            >
              Breakout (đối chiếu)
            </button>
            <button
              disabled={!report.result.retest}
              className={strategy === 'retest' ? 'secondary selected' : 'secondary'}
              onClick={() => setStrategy('retest')}
            >
              Breakout → Retest
            </button>
            {!report.result.retest && (
              <span className="micro">Chạy lại backtest để có kết quả retest.</span>
            )}
          </div>
          <p className="micro">
            Retest: chờ tối đa 10 nến sau breakout; chạm vùng rồi nến xác nhận cùng chiều giữ vùng;
            SL theo retest, TP 2R. Giả thuyết kiểm chứng, không đảm bảo lợi nhuận tốt hơn.
          </p>
          <div className="period-tabs" role="group" aria-label="Khoảng đánh giá">
            {Object.entries(periods).map(([key, label]) => (
              <button
                key={key}
                className={period === key ? 'secondary selected' : 'secondary'}
                onClick={() => setPeriod(key as keyof typeof periods)}
              >
                {label}
              </button>
            ))}
          </div>
          <p className="micro">
            {new Date(result.start_time * 1000).toISOString()} →{' '}
            {new Date(result.end_time * 1000).toISOString()} · {result.bars} nến đánh giá. Mỗi
            khoảng bắt đầu lại với cùng vốn; vị thế đóng ở cuối khoảng.
          </p>
          <div className="research-metrics">
            {[
              ['Lãi / lỗ ròng', `${formatPrice(result.net_pnl)} USDT`],
              ['Lợi nhuận', `${result.return_percent.toFixed(2)}%`],
              ['Drawdown tối đa', `${result.max_drawdown_percent.toFixed(2)}%`],
              ['Số lệnh / thắng', `${result.trade_count} / ${result.win_rate_percent.toFixed(1)}%`],
              ['Tổng phí', `${formatPrice(result.total_fees)} USDT`],
              ['Funding trả (+) / nhận (−)', `${formatPrice(result.funding_cost)} USDT`],
            ].map(([label, value]) => (
              <div key={label}>
                <span className="muted">{label}</span>
                <strong>{value}</strong>
              </div>
            ))}
          </div>
          <EquityChart points={result.equity_curve} />
          <TradeReview
            key={`${report.id}-${strategy}-${period}`}
            candles={report.result.candles}
            result={result}
          />
          <h3>Các lệnh gần nhất trong khoảng đánh giá</h3>
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>Vào lệnh (UTC)</th>
                  <th>Chiều</th>
                  <th>Entry → Exit</th>
                  <th>Hợp đồng</th>
                  <th>Lý do thoát</th>
                  <th>P&L ròng</th>
                </tr>
              </thead>
              <tbody>
                {result.trades.slice(-20).map((trade, index) => (
                  <tr key={`${trade.entry_time}-${index}`}>
                    <td>
                      {new Date(trade.entry_time * 1000)
                        .toISOString()
                        .slice(0, 16)
                        .replace('T', ' ')}
                    </td>
                    <td>{trade.side.toUpperCase()}</td>
                    <td>
                      {formatPrice(trade.entry)} → {formatPrice(trade.exit)}
                    </td>
                    <td>{trade.contracts}</td>
                    <td>{trade.exit_reason}</td>
                    <td className={trade.net_pnl >= 0 ? 'positive' : 'negative'}>
                      {formatPrice(trade.net_pnl)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {!result.trades.length && <p>Không có lệnh đáp ứng quy tắc trong khoảng này.</p>}
          <details>
            <summary>Giả định mô hình và giới hạn</summary>
            <ul>
              {report.result.assumptions.map((assumption) => (
                <li key={assumption}>{assumption}</li>
              ))}
            </ul>
          </details>
        </section>
      )}
    </>
  );
}
