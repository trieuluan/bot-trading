import type { ReactNode } from 'react';
import {
  ArrowRight,
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
  onOpenStrategy,
}: {
  snapshot: Snapshot;
  botPanel: ReactNode;
  filter: string;
  setFilter: (value: string) => void;
  onOpenStrategy: () => void;
}) {
  const realMarket = snapshot.data_source === 'mexc_futures';
  if (!snapshot.candles.length)
    return (
      <section className="panel prose">
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
      <div className="metrics">
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
      <div className="workspace-grid">
        <section className="panel chart-panel">
          <div className="section-head">
            <h2>Thị trường đang theo dõi</h2>
            <span className="pill">{realMarket ? 'MEXC PERPETUAL' : 'SYNTHETIC REPLAY'}</span>
          </div>
          <div className="chart-heading">
            <div className="asset-icon">₿</div>
            <div>
              <h3>
                Bitcoin <span>{realMarket ? 'BTC_USDT PERP' : 'BTC / USDT'}</span>
              </h3>
              <div className="price">
                {formatPrice(last.close)}{' '}
                <span className={change >= 0 ? 'positive' : 'negative'}>
                  {change >= 0 ? '+' : ''}
                  {change.toFixed(2)}% <ArrowUpRight size={14} />
                </span>
              </div>
            </div>
            <div className="timeframe">
              15m <ChevronDown size={12} />
            </div>
          </div>
          <div className="ohlc">
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
          <div className="chart-footer">
            <span>
              <span className="legend-dot" /> Nến đóng cửa · UTC
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
      <div className="bottom-grid">
        <section className="panel strategy-preview">
          <div className="section-head">
            <h2>Chiến lược của workspace</h2>
            <button className="text-button" onClick={onOpenStrategy}>
              Chi tiết <ArrowRight size={15} />
            </button>
          </div>
          <div className="strategy-row">
            <div className="strategy-symbol">
              <TrendingUp size={25} />
            </div>
            <div>
              <h3>
                Structure Breakout <span className="pill">v0.1</span>
              </h3>
              <p>Breakout 20 nến · SL 5 nến · TP 2R</p>
            </div>
            <span className="pill amber">Có thể backtest</span>
          </div>
        </section>
        <section className="panel">
          <div className="section-head">
            <h2>Kết nối sàn</h2>
            <Link2 size={16} />
          </div>
          <div className="connection-row">
            <div className="exchange-icon">D</div>
            <div>
              <h3>{realMarket ? 'MEXC Futures adapter' : 'Demo adapter'}</h3>
              <p>
                {realMarket
                  ? 'Public market data · không cần API key'
                  : 'Dữ liệu tổng hợp · không cần API key'}
              </p>
            </div>
            <Check size={19} className="positive" />
          </div>
        </section>
      </div>
    </>
  );
}
