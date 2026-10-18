import { getFuturesSymbol } from '../lib/futures-symbol';
import { useEffect, useState, type FormEvent } from 'react';
import { Bot, CircleStop, Play, Save } from 'lucide-react';
import { configureAutoPaper, setAutoPaperEnabled } from '../lib/api';
import { formatPrice } from '../lib/format';
import type { AutoPaperState } from '../types/paper';
import { FuturesHistoryPanel } from './FuturesHistoryPanel';

interface Props {
  state: AutoPaperState | null;
  stale: boolean;
  online: boolean;
  clock: number;
  onUpdated: () => Promise<void>;
}

export function AutoPaperPanel({ state, stale, online, clock, onUpdated }: Props) {
  const [risk, setRisk] = useState('1');
  const [maxTrades, setMaxTrades] = useState('5');
  const [maxLoss, setMaxLoss] = useState('3');
  const [leverage, setLeverage] = useState(1);
  const [capEnabled, setCapEnabled] = useState(false);
  const [cap, setCap] = useState('5');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  useEffect(() => {
    if (state) {
      setRisk(String(state.risk_percent));
      setMaxTrades(String(state.max_daily_trades));
      setMaxLoss(String(state.daily_loss_percent));
      setLeverage(state.leverage);
      setCapEnabled(state.max_portfolio_risk_percent != null);
      setCap(String(state.max_portfolio_risk_percent ?? 5));
    }
  }, [
    state?.risk_percent,
    state?.max_daily_trades,
    state?.daily_loss_percent,
    state?.leverage,
    state?.max_portfolio_risk_percent,
  ]);
  const minLeverage = state?.min_leverage ?? 1;
  const maxLeverage = state?.max_leverage ?? 1;
  const setClamped = (value: number) =>
    setLeverage(Math.min(maxLeverage, Math.max(minLeverage, Math.round(value) || minLeverage)));
  const dirty =
    !!state &&
    (Number(risk) !== state.risk_percent ||
      Number(maxTrades) !== state.max_daily_trades ||
      Number(maxLoss) !== state.daily_loss_percent ||
      leverage !== state.leverage ||
      (capEnabled ? Number(cap) : null) !== (state.max_portfolio_risk_percent ?? null));
  async function save(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError('');
    setMessage('');
    try {
      await configureAutoPaper({
        risk_percent: Number(risk),
        leverage,
        max_daily_trades: Number(maxTrades),
        daily_loss_percent: Number(maxLoss),
        max_portfolio_risk_percent: capEnabled ? Number(cap) : null,
      });
      await onUpdated();
      setMessage('Đã lưu thông số Auto Paper.');
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Không lưu được thông số.');
    } finally {
      setBusy(false);
    }
  }
  async function toggle() {
    if (!state) return;
    setBusy(true);
    setError('');
    setMessage('');
    try {
      await setAutoPaperEnabled(!state.enabled);
      await onUpdated();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Không đổi được trạng thái Auto Paper.');
    } finally {
      setBusy(false);
    }
  }
  const remaining = Math.max(0, Math.ceil((state?.next_close_time ?? 0) - clock / 1000));
  const countdown = `${Math.floor(remaining / 60)
    .toString()
    .padStart(2, '0')}:${(remaining % 60).toString().padStart(2, '0')}`;
  const modeLabel =
    state?.auto_mode === 'paper_learning'
      ? 'Bot học · Paper thử nghiệm'
      : state?.auto_mode === 'learner'
        ? 'Bot học · Chiến thuật đã duyệt'
        : 'Structure Breakout';
  const locked = busy || !!state?.enabled;
  const riskUsed =
    state?.portfolio_capital && state.portfolio_capital > 0
      ? ((state.portfolio_used_risk ?? 0) / state.portfolio_capital) * 100
      : null;
  return (
    <section className="panel prose auto-paper-panel">
      <header className="ap-head">
        <div className="auto-title">
          <Bot size={22} />
          <div>
            <h2>Auto Paper</h2>
            <p>
              <strong className="auto-strategy">{modeLabel}</strong>
              <span>{getFuturesSymbol()} · 15m · Cross</span>
            </p>
          </div>
        </div>
        <button
          className={`ap-toggle ${state?.enabled ? 'on' : ''}`}
          type="button"
          onClick={() => void toggle()}
          disabled={
            !state ||
            !online ||
            busy ||
            (!state.enabled && (stale || !state.worker_online || dirty))
          }
        >
          {state?.enabled ? <CircleStop size={15} /> : <Play size={15} />}
          {busy ? 'Đang xử lý…' : state?.enabled ? 'Dừng Auto' : 'Bật Auto'}
        </button>
      </header>
      <div className="auto-status" role="status">
        <span className={`status-dot ${!state?.worker_online ? 'stale-dot' : ''}`} />
        <strong>{state?.worker_online ? 'Worker online' : 'Worker offline'}</strong>
        <span title={state?.status}>{state?.status ?? 'Đang tải trạng thái…'}</span>
      </div>
      <div className="ap-kpis">
        <div>
          <span>Nến đóng sau</span>
          <strong>{state ? countdown : '—'}</strong>
        </div>
        <div>
          <span>Lệnh hôm nay</span>
          <strong>{state ? `${state.daily_trades}/${state.max_daily_trades}` : '—'}</strong>
        </div>
        <div className={state && state.daily_loss > 0 ? 'bad' : ''}>
          <span>Lỗ ngày</span>
          <strong>
            {state ? `${state.daily_loss_used_percent.toFixed(2)}%` : '—'}
            <small>{state ? ` / ${state.daily_loss_percent}%` : ''}</small>
          </strong>
        </div>
        <div>
          <span>Rủi ro SL mở</span>
          <strong>
            {riskUsed === null ? '—' : `${riskUsed.toFixed(2)}%`}
            <small>
              {state?.max_portfolio_risk_percent == null
                ? ' / ∞'
                : ` / ${state.max_portfolio_risk_percent}%`}
            </small>
          </strong>
        </div>
      </div>
      <div className="ap-chips" aria-label="Thông số đang áp dụng">
        <span>Đòn bẩy <b>{state?.leverage ?? '—'}x</b></span>
        <span>Rủi ro/lệnh <b>{state?.risk_percent ?? '—'}%</b></span>
        <span>Tối đa <b>{state?.max_daily_trades ?? '—'}</b>/ngày</span>
        <span>Lỗ ngày <b>{state?.daily_loss_percent ?? '—'}%</b></span>
        <span>Trần SL <b>{state?.max_portfolio_risk_percent == null ? '∞' : `${state.max_portfolio_risk_percent}%`}</b></span>
      </div>
      <details className="ap-config" open={!state?.enabled || undefined}>
        <summary>{state?.enabled ? 'Thông số (khoá khi Auto chạy)' : 'Chỉnh thông số'}</summary>
      <form className="auto-paper-form ap-form" onSubmit={(event) => void save(event)}>
        <section className="ap-card">
          <h3>Kích thước lệnh</h3>
          <div className="ap-grid">
            <label>
              Rủi ro / lệnh (%)
              <input
                type="number"
                min="0.1"
                max="5"
                step="0.1"
                required
                value={risk}
                disabled={locked}
                onChange={(e) => setRisk(e.target.value)}
              />
            </label>
            <label>
              Lệnh Auto tối đa / ngày
              <input
                type="number"
                min="1"
                max="100"
                step="1"
                required
                value={maxTrades}
                disabled={locked}
                onChange={(e) => setMaxTrades(e.target.value)}
              />
            </label>
          </div>
          <fieldset className="leverage-box" disabled={locked || !state?.max_leverage}>
            <legend>
              Đòn bẩy <b>{leverage}x</b> · Cross
            </legend>
            <div className="leverage-stepper">
              <button
                type="button"
                aria-label="Giảm đòn bẩy"
                onClick={() => setClamped(leverage - 1)}
              >
                −
              </button>
              <input
                type="number"
                aria-label="Đòn bẩy"
                min={minLeverage}
                max={maxLeverage}
                step="1"
                value={leverage}
                onChange={(e) => setClamped(Number(e.target.value))}
              />
              <span>x</span>
              <button
                type="button"
                aria-label="Tăng đòn bẩy"
                onClick={() => setClamped(leverage + 1)}
              >
                +
              </button>
            </div>
            <div className="leverage-presets">
              {[10, 20, 50, 100, 200]
                .filter((value) => value <= maxLeverage)
                .map((value) => (
                  <button
                    type="button"
                    key={value}
                    className={leverage === value ? 'active' : ''}
                    onClick={() => setClamped(value)}
                  >
                    {value}x
                  </button>
                ))}
            </div>
            <input
              className="leverage-slider"
              type="range"
              aria-label="Thanh trượt đòn bẩy"
              min={minLeverage}
              max={maxLeverage}
              step="1"
              value={leverage}
              onChange={(e) => setClamped(Number(e.target.value))}
            />
            <p className="micro">
              {state?.max_leverage
                ? `Tối đa ${state.max_leverage}x theo MEXC cho ${getFuturesSymbol()}. Ký quỹ = giá trị lệnh ÷ ${leverage}.`
                : 'Chờ MEXC trả thông số đòn bẩy của cặp này.'}
            </p>
          </fieldset>
        </section>
        <section className="ap-card">
          <h3>Giới hạn ví chung · 10 cặp</h3>
          <div className="ap-grid">
            <label>
              Ngưỡng lỗ ngày (%)
              <input
                type="number"
                min="0.1"
                max="20"
                step="0.1"
                required
                value={maxLoss}
                disabled={locked}
                onChange={(e) => setMaxLoss(e.target.value)}
              />
            </label>
            <label>
              Trần rủi ro SL (% vốn)
              <input
                type="number"
                min="0.5"
                max="50"
                step="0.5"
                required={capEnabled}
                value={capEnabled ? cap : ''}
                placeholder="Không giới hạn"
                disabled={locked || !capEnabled}
                onChange={(event) => setCap(event.target.value)}
              />
            </label>
          </div>
          <label className="ap-switch">
            <input
              type="checkbox"
              checked={capEnabled}
              disabled={locked}
              onChange={(event) => setCapEnabled(event.target.checked)}
            />
            Giới hạn tổng rủi ro SL của mọi lệnh đang mở
          </label>
          <p className="micro">
            Đang dùng {formatPrice(state?.portfolio_used_risk ?? 0)} USDT. Ký quỹ một lệnh luôn ≤
            10% vốn. Đổi giới hạn ví chung cần dừng Auto trên mọi cặp.
          </p>
        </section>
        <div className="ap-actions">
          <button
            className="secondary"
            type="submit"
            disabled={!state || !online || busy || state.enabled || !dirty}
          >
            <Save size={15} /> Lưu thông số
          </button>
          {dirty && <span className="micro">Lưu trước khi bật Auto.</span>}
        </div>
      </form>
      </details>
      {error && (
        <p className="negative" role="alert">
          {error}
        </p>
      )}
      {message && (
        <p className="positive" role="status">
          {message}
        </p>
      )}
      <details className="auto-paper-guide">
        <summary>Học từ lịch sử Futures</summary>
        <FuturesHistoryPanel />
      </details>
      <details className="auto-paper-guide">
        <summary>Cách vận hành & giới hạn</summary>
        <p className="micro">
          Mốc equity ngày {state?.day || 'đang chờ'} (UTC):{' '}
          {state ? formatPrice(state.day_start_equity) : '—'} USDT. Lỗ tính trên toàn bộ ví, số lệnh
          chỉ tính lệnh auto.
        </p>
        <p className="micro">
          Bật auto sẽ chờ nến mới, không đặt bù tín hiệu cũ. Dừng auto chỉ ngừng vào lệnh mới; SL/TP
          vẫn được worker quản lý. Tắt nút theo dõi tín hiệu ở Tổng quan không dừng Auto Paper.
        </p>
        <p>
          Bot học chọn tín hiệu theo bối cảnh thị trường và điểm UCB từ mẫu shadow đã đóng. SL/TP theo chiến thuật được chọn; lý do và chiến thuật lưu trong quyết định Auto.{' '}
          Vào bằng bid/ask mới sau khi nến đóng, trong tối đa 60 giây; bỏ qua nếu giá đã vượt SL/TP
          hoặc đang có vị thế. Khối lượng theo rủi ro còn lại; tự động chặn nếu ký quỹ một lệnh vượt
          10% vốn ví chung. Trần tổng rủi ro SL danh mục theo cấu hình ví chung ở trên. Bước hợp
          đồng MEXC. Phí/trượt giá có thể khiến lỗ vượt ngân sách SL dự kiến.
        </p>
        <p>
          Ngày tính theo UTC; mốc equity là lần quan sát đầu tiên trong ngày, gồm PnL chưa chốt.
          Chạm ngưỡng lỗ hoặc số lệnh sẽ dừng vào lệnh mới, không cưỡng chế đóng vị thế. Giá nhảy
          hoặc mất kết nối có thể làm lỗ vượt ngưỡng. Bot không tự bật lại vào ngày mới; cần bật thủ
          công. Khởi động lại worker giữ cấu hình và vị thế nhưng bỏ qua nến cũ.
        </p>
      </details>
    </section>
  );
}
