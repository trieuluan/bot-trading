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
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  useEffect(() => {
    if (state) {
      setRisk(String(state.risk_percent));
      setMaxTrades(String(state.max_daily_trades));
      setMaxLoss(String(state.daily_loss_percent));
    }
  }, [state?.risk_percent, state?.max_daily_trades, state?.daily_loss_percent]);
  const dirty =
    !!state &&
    (Number(risk) !== state.risk_percent ||
      Number(maxTrades) !== state.max_daily_trades ||
      Number(maxLoss) !== state.daily_loss_percent);
  async function save(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError('');
    setMessage('');
    try {
      await configureAutoPaper({
        risk_percent: Number(risk),
        max_daily_trades: Number(maxTrades),
        daily_loss_percent: Number(maxLoss),
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
  return (
    <section className="panel prose auto-paper-panel">
      <div className="section-head">
        <div className="auto-title">
          <Bot size={22} />
          <div>
            <h2>Auto Paper</h2>
            <strong className="auto-strategy">
              {state?.auto_mode === 'paper_learning'
                ? 'Bot học · Paper thử nghiệm'
                : state?.auto_mode === 'learner'
                  ? 'Bot học · Chiến thuật đã duyệt'
                  : 'Structure Breakout'}
            </strong>
            <p>{getFuturesSymbol()} · 15m · Long / Short · nhiều vị thế</p>
          </div>
        </div>
        <span className={`pill ${state?.enabled ? 'green' : ''}`}>
          {state?.enabled ? 'TỰ VÀO LỆNH' : 'ĐÃ DỪNG AUTO'}
        </span>
      </div>
      <div className="auto-status" role="status">
        <span className={`status-dot ${!state?.worker_online ? 'stale-dot' : ''}`} />
        <strong>{state?.worker_online ? 'Worker online' : 'Worker offline'}</strong>
        <span>{state?.status ?? 'Đang tải trạng thái…'}</span>
      </div>
      {state?.auto_mode === 'paper_learning' && (
        <p className="micro auto-paper-safety">
          Bot tự chọn chiến thuật chưa qua duyệt để thử bằng tiền ảo. Không có tín hiệu thì đứng
          ngoài; không quay lại breakout cũ. Kết quả chưa chứng minh hiệu quả giao dịch.
        </p>
      )}
      <form className="research-form auto-paper-form" onSubmit={(event) => void save(event)}>
        <h3>Giới hạn giao dịch</h3>
        <label>
          Rủi ro / lệnh (%)
          <input
            type="number"
            min="0.1"
            max="5"
            step="0.1"
            required
            value={risk}
            disabled={busy || !!state?.enabled}
            onChange={(e) => setRisk(e.target.value)}
          />
        </label>
        <label>
          Số lệnh auto tối đa / ngày
          <input
            type="number"
            min="1"
            max="100"
            step="1"
            required
            value={maxTrades}
            disabled={busy || !!state?.enabled}
            onChange={(e) => setMaxTrades(e.target.value)}
          />
        </label>
        <label>
          Ngưỡng lỗ ngày (%)
          <input
            type="number"
            min="0.1"
            max="20"
            step="0.1"
            required
            value={maxLoss}
            disabled={busy || !!state?.enabled}
            onChange={(e) => setMaxLoss(e.target.value)}
          />
        </label>
        <div className="auto-paper-actions">
          <button
            className="secondary"
            type="submit"
            disabled={!state || !online || busy || state.enabled || !dirty}
          >
            <Save size={15} /> Lưu thông số
          </button>
          <button
            className="primary"
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
            {busy ? 'Đang xử lý…' : state?.enabled ? 'Dừng Auto Paper' : 'Bật Auto Paper'}
          </button>
        </div>
      </form>
      {dirty && <p className="micro">Lưu thông số trước khi bật Auto Paper.</p>}
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
      <div className="auto-stats">
        <div>
          <span>Nến kế tiếp đóng sau</span>
          <strong>{state ? countdown : '—'}</strong>
        </div>
        <div>
          <span>Lệnh auto hôm nay (UTC)</span>
          <strong>{state ? `${state.daily_trades} / ${state.max_daily_trades}` : '—'}</strong>
        </div>
        <div className="auto-loss-stat">
          <span>Lỗ theo equity đầu ngày</span>
          <strong>
            {state
              ? `${formatPrice(state.daily_loss)} USDT · ${state.daily_loss_used_percent.toFixed(2)}%`
              : '—'}
          </strong>
        </div>
      </div>
      <p className="micro auto-paper-safety">
        Dừng auto chỉ ngừng vào lệnh mới; worker vẫn quản lý SL/TP.
      </p>
      <FuturesHistoryPanel />
      <details className="auto-paper-guide">
        <summary>Cách vận hành & giới hạn ngày</summary>
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
          {state?.auto_mode === 'legacy'
            ? 'Nến đóng vượt đỉnh/đáy 20 nến → Long/Short. SL theo 5 nến trước, TP tham chiếu 2R từ giá tín hiệu.'
            : 'Bot học chọn tín hiệu theo bối cảnh thị trường và điểm UCB từ mẫu shadow đã đóng. SL/TP theo chiến thuật được chọn; lý do và chiến thuật lưu trong quyết định Auto.'}{' '}
          Vào bằng bid/ask mới sau khi nến đóng, trong tối đa 60 giây; bỏ qua nếu giá đã vượt SL/TP
          hoặc đang có vị thế. Khối lượng theo rủi ro còn lại, giới hạn 1× vốn và bước hợp đồng
          MEXC. Phí/trượt giá có thể khiến lỗ vượt ngân sách SL dự kiến.
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
