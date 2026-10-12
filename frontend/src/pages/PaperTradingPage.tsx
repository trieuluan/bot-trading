import { useEffect, useRef, useState, type FormEvent } from 'react';
import { Wallet, RefreshCw } from 'lucide-react';
import { PaperChart } from '../components/PaperChart';
import { formatPrice } from '../lib/format';
import { fetchPaperWallet, openPaperOrder, closePaperPosition } from '../lib/api';
import type { PaperWallet } from '../types/paper';

const exitLabels: Record<string, string> = {
  manual: 'Đóng thủ công',
  stop_loss: 'Stop-loss',
  take_profit: 'Take-profit',
};
const timeLabel = (time: number) => new Date(time * 1000).toLocaleString('vi-VN');

export function PaperTradingPage() {
  const [wallet, setWallet] = useState<PaperWallet | null>(null);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const [online, setOnline] = useState(false);
  const [busy, setBusy] = useState(false);
  const [side, setSide] = useState<'long' | 'short'>('long');
  const [notional, setNotional] = useState('1000');
  const [stop, setStop] = useState('');
  const [target, setTarget] = useState('');
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [clock, setClock] = useState(Date.now());
  const latest = useRef<PaperWallet | null>(null);
  const chartSection = useRef<HTMLElement | null>(null);
  function viewTrade(id: number) {
    setSelectedId(id);
    chartSection.current?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }
  // Reuse a command ID after an ambiguous timeout; the API returns its original result.
  const pending = useRef<{ key: string; id: string } | null>(null);
  function commandId(key: string) {
    if (pending.current?.key !== key) pending.current = { key, id: crypto.randomUUID() };
    return pending.current!.id;
  }
  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const result = await fetchPaperWallet();
        if (active) {
          latest.current = result;
          setWallet(result);
          setOnline(true);
        }
      } catch {
        if (active) setOnline(false);
      }
      if (active) timer = setTimeout(() => void poll(), 2000);
    }
    void poll();
    const ticker = setInterval(() => setClock(Date.now()), 1000);
    return () => {
      active = false;
      clearTimeout(timer);
      clearInterval(ticker);
    };
  }, []);
  async function refresh() {
    const result = await fetchPaperWallet();
    latest.current = result;
    setWallet(result);
    setOnline(true);
  }
  function suggestLevels(nextSide = side) {
    const quote = latest.current?.quote;
    if (!quote) return;
    const price = nextSide === 'long' ? quote.ask1 : quote.bid1;
    setStop((price * (nextSide === 'long' ? 0.99 : 1.01)).toFixed(1));
    setTarget((price * (nextSide === 'long' ? 1.02 : 0.98)).toFixed(1));
  }
  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError('');
    setMessage('');
    const order = { side, notional: Number(notional), stop: Number(stop), target: Number(target) };
    try {
      const result = await openPaperOrder({
        ...order,
        request_id: commandId(JSON.stringify(order)),
      });
      pending.current = null;
      setSelectedId(result.trade_id);
      setMessage(`Đã mở vị thế mô phỏng #${result.trade_id}.`);
      await refresh();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Không mở được lệnh.');
    } finally {
      setBusy(false);
    }
  }
  async function close() {
    if (!wallet?.position) return;
    setBusy(true);
    setError('');
    setMessage('');
    try {
      await closePaperPosition(wallet.position.id, commandId(`close-${wallet.position.id}`));
      pending.current = null;
      setMessage('Đã đóng vị thế mô phỏng.');
      await refresh();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Không đóng được vị thế.');
    } finally {
      setBusy(false);
    }
  }
  const quote = wallet?.quote;
  const stale =
    !online ||
    !quote ||
    wallet?.stale ||
    clock / 1000 - quote.received_at > 20 ||
    Math.abs(clock / 1000 - quote.time) > 30;
  const selected =
    wallet?.trades.find((trade) => trade.id === selectedId) ??
    wallet?.position ??
    wallet?.trades[0] ??
    null;
  return (
    <>
      <section className="panel prose paper-intro">
        <div className="section-head">
          <h2>
            <Wallet size={19} /> Ví mô phỏng
          </h2>
          <span className="pill amber">10.000 USDT ẢO · TỐI ĐA 1×</span>
        </div>
        <p>
          Giá MEXC thật, tiền và lệnh mô phỏng nội bộ. Đặt Market Long/Short thủ công để kiểm tra
          luồng giao dịch; bot tín hiệu chưa tự đặt lệnh.
        </p>
        <div className="paper-feed">
          <span className={`status-dot ${stale ? 'stale-dot' : ''}`} />
          <strong>{stale ? 'Giá chậm / mất kết nối' : 'Đang nhận giá MEXC'}</strong>
          <span>{quote ? `Cập nhật ${timeLabel(quote.received_at)}` : 'Đang chờ worker'}</span>
          <span>Lấy giá khoảng mỗi 5 giây</span>
        </div>
        {stale && (
          <p role="status" className="negative">
            Tạm khóa đặt/đóng lệnh khi giá chậm. SL/TP chỉ xử lý khi worker nhận lại giá hợp lệ.{' '}
            {wallet?.error}
          </p>
        )}
        {wallet?.funding_error && (
          <p role="status" className="negative">
            Funding đang chờ đồng bộ: {wallet.funding_error}. PnL hiện tại còn tạm tính.
          </p>
        )}
        {error && (
          <p role="alert" className="negative">
            {error}
          </p>
        )}
        {message && (
          <p role="status" className="positive">
            {message}
          </p>
        )}
      </section>
      <div className="research-metrics paper-metrics">
        {[
          ['Số dư ví', wallet?.balance],
          ['Equity (theo fair price)', wallet?.equity],
          ['PnL chưa chốt', wallet?.unrealized_pnl],
          ['Vốn đang giữ', wallet?.used_margin],
          ['Khả dụng', wallet?.available],
        ].map(([label, value]) => (
          <div key={String(label)}>
            <span className="muted">{label}</span>
            <strong>{typeof value === 'number' ? `${formatPrice(value)} USDT` : '—'}</strong>
          </div>
        ))}
      </div>
      <div className="paper-terminal">
        <section ref={chartSection} className="panel prose paper-market">
          <div className="section-head">
            <div>
              <h2>
                BTC / USDT <span className="muted">Perpetual</span>
              </h2>
              <p>
                Last {quote ? formatPrice(quote.lastPrice) : '—'} · Fair{' '}
                {quote ? formatPrice(quote.fairPrice) : '—'}
              </p>
            </div>
            <span className="pill">PAPER FILLS</span>
          </div>
          <PaperChart candles={wallet?.candles ?? []} trade={selected} />
          <div className="paper-book">
            <span>
              Bid <strong>{quote ? formatPrice(quote.bid1) : '—'}</strong>
            </span>
            <span>
              Ask <strong>{quote ? formatPrice(quote.ask1) : '—'}</strong>
            </span>
          </div>
          <p className="chart-attribution">
            TradingView Lightweight Charts™ · Copyright (с) 2025 TradingView, Inc. ·{' '}
            <a href="https://www.tradingview.com/" target="_blank" rel="noreferrer">
              TradingView
            </a>
          </p>
        </section>
        <section className="panel prose paper-order">
          <div className="section-head">
            <h2>Đặt lệnh mô phỏng</h2>
            <span className="pill">MARKET</span>
          </div>
          <form onSubmit={(event) => void submit(event)}>
            <div className="period-tabs" role="group" aria-label="Chiều lệnh">
              <button
                type="button"
                className={side === 'long' ? 'secondary selected' : 'secondary'}
                onClick={() => {
                  setSide('long');
                  if (stop || target) suggestLevels('long');
                }}
              >
                Long
              </button>
              <button
                type="button"
                className={side === 'short' ? 'secondary selected' : 'secondary'}
                onClick={() => {
                  setSide('short');
                  if (stop || target) suggestLevels('short');
                }}
              >
                Short
              </button>
            </div>
            <label>
              Giá trị lệnh (USDT)
              <input
                type="number"
                min="1"
                max="100000"
                step="0.01"
                required
                value={notional}
                onChange={(e) => setNotional(e.target.value)}
              />
            </label>
            <label>
              Stop-loss (USDT)
              <input
                type="number"
                min="0.1"
                step="0.1"
                required
                value={stop}
                onChange={(e) => setStop(e.target.value)}
              />
            </label>
            <label>
              Take-profit (USDT)
              <input
                type="number"
                min="0.1"
                step="0.1"
                required
                value={target}
                onChange={(e) => setTarget(e.target.value)}
              />
            </label>
            <button
              type="button"
              className="text-button"
              disabled={stale || busy}
              onClick={() => suggestLevels()}
            >
              <RefreshCw size={13} /> Điền SL 1% / TP 2% để thử
            </button>
            <button
              type="submit"
              className="primary"
              disabled={stale || busy || !!wallet?.position}
            >
              {busy ? 'Đang xử lý…' : `Mở ${side.toUpperCase()} mô phỏng`}
            </button>
            <p className="micro">
              Khối lượng làm tròn xuống theo hợp đồng MEXC. Phí {wallet?.fee_bps ?? 5} bps và trượt
              giá {wallet?.slippage_bps ?? 2} bps mỗi chiều là giả định, chưa gồm độ sâu sổ lệnh.
            </p>
          </form>
        </section>
      </div>
      <section className="panel prose">
        <div className="section-head">
          <h2>Vị thế đang mở</h2>
          {wallet?.position && (
            <button className="secondary" disabled={busy || stale} onClick={() => void close()}>
              Đóng vị thế mô phỏng
            </button>
          )}
        </div>
        {wallet?.position ? (
          <div className="selected-trade-summary">
            <strong>
              #{wallet.position.id} {wallet.position.side.toUpperCase()}
            </strong>
            <span>
              {wallet.position.contracts} hợp đồng · {wallet.position.quantity} BTC
            </span>
            <span>Entry {formatPrice(wallet.position.entry)}</span>
            <span className="negative">SL {formatPrice(wallet.position.stop)}</span>
            <span className="positive">TP {formatPrice(wallet.position.target)}</span>
            <button className="text-button" onClick={() => viewTrade(wallet.position!.id)}>
              Xem trên chart
            </button>
          </div>
        ) : (
          <p>Chưa có vị thế. Chọn Long hoặc Short và đặt một lệnh thử bằng USDT ảo.</p>
        )}
        <p className="micro">
          Số dư = vốn ban đầu + PnL đã chốt − phí − funding. Equity cộng PnL chưa chốt theo fair
          price, chưa trừ phí đóng dự kiến. Funding thực tế được tính trễ khi nến fair-price tại kỳ
          quyết toán đã đóng.
        </p>
      </section>
      <section className="panel prose">
        <div className="section-head">
          <h2>Lịch sử lệnh mô phỏng</h2>
          <span className="muted">100 lệnh gần nhất · click để xem mức giá</span>
        </div>
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>Lệnh / thời gian</th>
                <th>Chiều</th>
                <th>Entry → Exit</th>
                <th>Phí / Funding</th>
                <th>Trạng thái</th>
                <th>PnL ròng</th>
              </tr>
            </thead>
            <tbody>
              {wallet?.trades.map((trade) => (
                <tr key={trade.id}>
                  <td>
                    <button className="text-button" onClick={() => viewTrade(trade.id)}>
                      #{trade.id} · {timeLabel(trade.entry_time)}
                    </button>
                  </td>
                  <td>{trade.side.toUpperCase()}</td>
                  <td>
                    {formatPrice(trade.entry)} →{' '}
                    {trade.exit === null ? '—' : formatPrice(trade.exit)}
                  </td>
                  <td>
                    {formatPrice(trade.entry_fee + trade.exit_fee)} /{' '}
                    {formatPrice(trade.funding_cost)}
                  </td>
                  <td>{trade.exit_reason ? exitLabels[trade.exit_reason] : 'Đang mở'}</td>
                  <td className={(trade.net_pnl ?? 0) >= 0 ? 'positive' : 'negative'}>
                    {trade.net_pnl === null ? 'Chưa chốt' : formatPrice(trade.net_pnl)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {!wallet?.trades.length && <p>Chưa có lệnh mô phỏng.</p>}
        <details>
          <summary>Giả định và giới hạn mô phỏng</summary>
          <p>
            Market fill tại bid/ask quan sát được cộng trượt giá cố định; toàn bộ khối lượng khớp
            ngay. SL/TP kích hoạt theo bid/ask lấy mẫu khoảng 5 giây và khớp tại giá quan sát kế
            tiếp, có thể vượt mức đặt. Không tái dựng tick bị bỏ lỡ khi mất kết nối. Chưa mô phỏng
            lệnh Limit, khớp một phần, liquidation, margin tiers hay ADL. Một vị thế, notional + phí
            tối đa số dư ví. Funding dùng rate lịch sử và fair-price open 15m xấp xỉ, cập nhật cả
            lệnh đã đóng khi dữ liệu tới trễ. Không có API key hoặc lệnh gửi tới MEXC.
          </p>
        </details>
      </section>
    </>
  );
}
