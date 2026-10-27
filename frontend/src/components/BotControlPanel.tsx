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
    <section className="panel bot-panel min-w-0 rounded-lg border border-border-base bg-surface-1 p-4 space-y-3">
      <div className="section-head mb-3 flex min-w-0 flex-wrap items-center justify-between gap-2 [&_p]:text-xs [&_p]:text-text-muted">
        <h2>Bot đang theo dõi</h2>
        <span className="muted text-text-muted">01</span>
      </div>
      <div className="bot-icon inline-flex size-9 shrink-0 items-center justify-center rounded-md bg-surface-3 text-bull">
        <Bot size={27} />
      </div>
      <div className="bot-name font-semibold text-text-primary">
        <h3>Structure Breakout</h3>
        <span className={`pill ${snapshot.bot.enabled ? 'green' : ''} inline-flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 text-xs font-medium bg-surface-3 text-text-secondary`}>
          {snapshot.bot.enabled ? 'Đang phân tích' : 'Đã tạm dừng'}
        </span>
      </div>
      <p className="muted text-text-muted">Price action · Đóng nến vượt đỉnh</p>
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
          <dd className={snapshot.bot.online ? "positive text-bull" : "muted text-text-muted"}>
            {snapshot.bot.online ? 'Đang kết nối' : 'Chưa kết nối'}
          </dd>
        </div>
      </dl>
      <button
        className="primary full inline-flex items-center justify-center gap-2 rounded-md bg-bull px-3 py-2 text-xs font-semibold text-canvas hover:bg-emerald-300 w-full"
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
      <p className="micro text-xs leading-relaxed text-text-muted">
        Chỉ tạo tín hiệu. Auto Paper có nút bật/dừng riêng tại Giao dịch & Bot.
      </p>
    </section>
  );
}
