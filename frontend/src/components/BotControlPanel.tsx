import { Bot, CircleStop, Play } from 'lucide-react';
import type { Snapshot } from '../types/trading';

interface BotControlPanelProps {
  snapshot: Snapshot;
  connected: boolean;
  busy: boolean;
  onToggle: () => void;
}
export function BotControlPanel({ snapshot, connected, busy, onToggle }: BotControlPanelProps) {
  return (
    <section className="panel bot-panel">
      <div className="section-head">
        <h2>Bot đang theo dõi</h2>
        <span className="muted">01</span>
      </div>
      <div className="bot-icon">
        <Bot size={27} />
      </div>
      <div className="bot-name">
        <h3>Structure Breakout</h3>
        <span className={`pill ${snapshot.bot.enabled ? 'green' : ''}`}>
          {snapshot.bot.enabled ? 'Đang phân tích' : 'Đã tạm dừng'}
        </span>
      </div>
      <p className="muted">Price action · Đóng nến vượt đỉnh</p>
      <dl>
        <div>
          <dt>Nguồn dữ liệu</dt>
          <dd>{snapshot.data_source === 'mexc_futures' ? 'MEXC Futures' : 'Demo replay'}</dd>
        </div>
        <div>
          <dt>Thị trường</dt>
          <dd>
            {snapshot.data_source === 'mexc_futures' ? 'BTC_USDT perpetual' : 'BTC/USDT'} · 15m
          </dd>
        </div>
        <div>
          <dt>Cửa sổ breakout</dt>
          <dd>20 nến</dd>
        </div>
        <div>
          <dt>Mục tiêu / rủi ro</dt>
          <dd>2 : 1</dd>
        </div>
        <div>
          <dt>Worker</dt>
          <dd className={snapshot.bot.online ? 'positive' : 'muted'}>
            {snapshot.bot.online ? 'Đang kết nối' : 'Chưa kết nối'}
          </dd>
        </div>
      </dl>
      <button
        className="primary full"
        onClick={onToggle}
        disabled={!connected || busy || (snapshot.bot.enabled === false && !snapshot.bot.online)}
      >
        {snapshot.bot.enabled ? <CircleStop size={16} /> : <Play size={16} />}{' '}
        {busy
          ? 'Đang xử lý…'
          : snapshot.bot.enabled
            ? 'Dừng tạo tín hiệu'
            : 'Bật theo dõi tín hiệu'}
      </button>
      <p className="micro">Chỉ tạo tín hiệu. Auto Paper có nút bật/dừng riêng tại Giao dịch & Bot.</p>
    </section>
  );
}
