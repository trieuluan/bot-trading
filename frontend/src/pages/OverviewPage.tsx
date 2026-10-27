import type { ReactNode } from 'react';
import {
  ArrowUpRight,
  Bot,
  Check,
  ChevronDown,
  Link2,
  Radio,
  ShieldCheck,
  TrendingUp,
} from 'lucide-react';
import type { Snapshot } from '../types/trading';
import { formatPrice } from '../lib/format';
import { CandlestickChart } from '../components/CandlestickChart';
import { MetricCard } from '../components/MetricCard';
import { SignalsTable } from '../components/SignalsTable';

export function OverviewPage({
  snapshot,
  botPanel,
  filter,
  setFilter,
}: {
  snapshot: Snapshot;
  botPanel: ReactNode;
  filter: string;
  setFilter: (value: string) => void;
}) {
  const realMarket = snapshot.data_source === 'mexc_futures';
  if (!snapshot.candles.length)
    return (
      <section className="panel prose min-w-0 rounded-lg border border-border-base bg-surface-1 p-4 space-y-3 text-sm leading-relaxed text-text-secondary">
        <h2>Chờ dữ liệu nến</h2>
        <p>Chưa có nến đã đóng. Xem trạng thái nguồn dữ liệu tại Kết nối sàn.</p>
        {botPanel}
      </section>
    );
  const last = snapshot.candles.at(-1)!;
  const first = snapshot.candles[0];
  const change = ((last.close - first.open) / first.open) * 100;
  const entries = snapshot.signals.filter((signal) => filter === 'all' || signal.side === filter);
  return (
    <>
      <div className="metrics grid grid-cols-2 gap-3 lg:grid-cols-4">
        <MetricCard
          title={realMarket ? 'GIÁ ĐÓNG CỬA FUTURES' : 'GIÁ ĐÓNG CỬA MẪU'}
          value={'$' + formatPrice(last.close)}
          detail={`${change >= 0 ? '+' : ''}${change.toFixed(2)}% trong cửa sổ nến`}
          icon={<TrendingUp size={17} />}
          positive
        />
        <MetricCard
          title="TÍN HIỆU ĐÃ GHI NHẬN"
          value={String(snapshot.signal_count).padStart(2, '0')}
          detail="Lưu từ worker vào database"
          icon={<Radio size={17} />}
        />
        <MetricCard
          title="BOT PHÂN TÍCH"
          value={snapshot.bot.enabled ? '01' : '00'}
          detail={snapshot.bot.enabled ? 'Đang theo dõi breakout' : 'Sẵn sàng khi worker kết nối'}
          icon={<Bot size={17} />}
        />
        <MetricCard
          title="LỆNH THỰC ĐÃ GỬI"
          value="00"
          detail="Thực thi live chưa được hỗ trợ"
          icon={<ShieldCheck size={17} />}
        />
      </div>
      <div className="workspace-grid mt-4 grid min-w-0 gap-4 xl:grid-cols-[minmax(0,2fr)_minmax(260px,1fr)]">
        <section className="panel chart-panel min-w-0 rounded-lg border border-border-base bg-surface-1 p-4 min-w-0 space-y-3">
          <div className="section-head mb-3 flex min-w-0 flex-wrap items-center justify-between gap-2 [&_p]:text-xs [&_p]:text-text-muted">
            <h2>Thị trường đang theo dõi</h2>
            <span className="pill inline-flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 text-xs font-medium bg-surface-3 text-text-secondary">{realMarket ? 'MEXC PERPETUAL' : 'SYNTHETIC REPLAY'}</span>
          </div>
          <div className="chart-heading flex flex-wrap items-baseline gap-2 font-mono text-xl font-semibold">
            <div className="asset-icon inline-flex size-9 shrink-0 items-center justify-center rounded-md bg-surface-3 text-bull">₿</div>
            <div>
              <h3>
                Bitcoin <span>{realMarket ? 'BTC_USDT PERP' : 'BTC / USDT'}</span>
              </h3>
              <div className="price flex flex-wrap items-baseline gap-2 font-mono text-xl font-semibold">
                {formatPrice(last.close)}{' '}
                <span className={change >= 0 ? "positive text-bull" : "negative text-bear"}>
                  {change >= 0 ? '+' : ''}
                  {change.toFixed(2)}% <ArrowUpRight size={14} />
                </span>
              </div>
            </div>
            <div className="timeframe flex flex-wrap items-center gap-1 [&>button]:rounded [&>button]:px-2 [&>button]:py-1 [&>button]:text-xs [&>button]:text-text-secondary [&>button:hover]:bg-surface-3 [&>button[aria-pressed=true]]:bg-surface-3 [&>button[aria-pressed=true]]:text-bull border-b border-border-subtle py-1">
              15m <ChevronDown size={12} />
            </div>
          </div>
          <div className="ohlc flex flex-wrap gap-3 font-mono text-xs text-text-muted">
            <span>
              O <b>{formatPrice(last.open)}</b>
            </span>
            <span>
              H <b>{formatPrice(last.high)}</b>
            </span>
            <span>
              L <b>{formatPrice(last.low)}</b>
            </span>
            <span>
              C <b>{formatPrice(last.close)}</b>
            </span>
          </div>
          <CandlestickChart
            candles={snapshot.candles}
            label={realMarket ? 'Biểu đồ nến MEXC BTC_USDT perpetual 15 phút' : undefined}
          />
          <div className="chart-footer flex flex-wrap items-center justify-between gap-3 border-t border-border-subtle py-2 text-xs">
            <span>
              <span className="legend-dot inline-block size-1.5 rounded-full bg-bull" /> Nến đóng cửa · UTC
            </span>
            <span>
              {realMarket
                ? 'Nến mới khi đóng · đồng bộ mỗi 15 giây'
                : '1 nến replay / 5 giây khi bật bot'}
            </span>
          </div>
        </section>
        {botPanel}
      </div>
      <SignalsTable entries={entries} filter={filter} setFilter={setFilter} />
      <div className="bottom-grid mt-4 grid min-w-0 gap-4 xl:grid-cols-[minmax(0,2fr)_minmax(260px,1fr)]">
        <section className="panel strategy-preview min-w-0 rounded-lg border border-border-base bg-surface-1 p-4 space-y-1">
          <div className="section-head mb-3 flex min-w-0 flex-wrap items-center justify-between gap-2 [&_p]:text-xs [&_p]:text-text-muted">
            <h2>Chiến lược của workspace</h2>
          </div>
          <div className="strategy-row flex flex-wrap items-center justify-between gap-3 border-t border-border-subtle py-2 text-xs">
            <div className="strategy-symbol">
              <TrendingUp size={25} />
            </div>
            <div>
              <h3>
                Structure Breakout <span className="pill inline-flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 text-xs font-medium bg-surface-3 text-text-secondary">v0.1</span>
              </h3>
              <p>Breakout 20 nến · SL 5 nến · TP 2R</p>
            </div>
            <span className="pill amber inline-flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 text-xs font-medium bg-surface-3 text-text-secondary text-amber-300">Có thể backtest</span>
          </div>
        </section>
        <section className="panel min-w-0 rounded-lg border border-border-base bg-surface-1 p-4">
          <div className="section-head mb-3 flex min-w-0 flex-wrap items-center justify-between gap-2 [&_p]:text-xs [&_p]:text-text-muted">
            <h2>Kết nối sàn</h2>
            <Link2 size={16} />
          </div>
          <div className="connection-row flex flex-wrap items-center justify-between gap-3 border-t border-border-subtle py-2 text-xs">
            <div className="exchange-icon inline-flex size-9 shrink-0 items-center justify-center rounded-md bg-surface-3 text-bull">D</div>
            <div>
              <h3>{realMarket ? 'MEXC Futures adapter' : 'Demo adapter'}</h3>
              <p>
                {realMarket
                  ? 'Public market data · không cần API key'
                  : 'Dữ liệu tổng hợp · không cần API key'}
              </p>
            </div>
            <Check size={19} className="positive text-bull" />
          </div>
        </section>
      </div>
    </>
  );
}
