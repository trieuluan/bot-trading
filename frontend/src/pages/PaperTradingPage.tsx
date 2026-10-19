import { useEffect, useLayoutEffect, useRef, useState, type FormEvent } from 'react';
import { Bot, Clock3, RefreshCw, ShieldCheck } from 'lucide-react';
import { FuturesStrategyMenu } from '../components/FuturesStrategyMenu';
import { AutoPaperPanel } from '../components/AutoPaperPanel';
import { AutoDecisionList } from '../components/AutoDecisionList';
import { FuturesLearningPage } from './FuturesLearningPage';
import { PaperTradeDetails } from '../components/PaperTradeDetails';
import type { Candle } from '../types/trading';
import { PaperChart } from '../components/PaperChart';
import { formatPrice, formatQuote } from '../lib/format';
import { chartStrategyText, tradeStrategy } from '../lib/futures-strategy-info';
import {
  fetchPaperWallet,
  fetchPaperTradeCandles,
  fetchMexcCandles,
  fetchAutoPaper,
  setAutoPaperEnabled,
  openPaperOrder,
  closePaperPosition,
} from '../lib/api';
import type { AutoPaperState, PaperWallet } from '../types/paper';
import { useMexcLiveCandle } from '../hooks/useMexcLiveCandle';
import { withLiveCandle } from '../lib/mexc-stream';
import { getFuturesSymbol, type FuturesSymbol } from '../lib/futures-symbol';
import {
  AllPairsPanel,
  PairStrip,
  PositionsTable,
  TradeHistoryTable,
  type Board,
} from '../components/FuturesBoard';

function mergeBars(history: Candle[], recent: Candle[]): Candle[] {
  const bars = new Map<number, Candle>();
  for (const bar of [...history, ...recent]) bars.set(bar.time, bar);
  return [...bars.values()].sort((a, b) => a.time - b.time);
}

function fundingCountdown(seconds: number) {
  const s = Math.max(0, Math.floor(seconds));
  return [Math.floor(s / 3600), Math.floor(s / 60) % 60, s % 60]
    .map((n) => String(n).padStart(2, '0'))
    .join(':');
}

export function PaperTradingPage({
  board,
  onSelect,
  chartTrade,
}: {
  board: Board;
  onSelect: (s: FuturesSymbol, tradeId?: number | null) => void;
  chartTrade: number | null;
}) {
  const symbol = getFuturesSymbol();
  const base = symbol.split('_')[0];
  const [orderMode, setOrderMode] = useState<'manual' | 'auto'>('manual');
  const [activityTab, setActivityTab] = useState('position');
  const [allPairs, setAllPairs] = useState(true);
  const stream = useMexcLiveCandle();
  const [older, setOlder] = useState<Candle[]>([]);
  const [hasMore, setHasMore] = useState(true);
  const [olderError, setOlderError] = useState('');
  const loadingOlder = useRef(false);
  const [auto, setAuto] = useState<AutoPaperState | null>(null);
  const [wallet, setWallet] = useState<PaperWallet | null>(null);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const [online, setOnline] = useState(false);
  const [busy, setBusy] = useState(false);
  const [side, setSide] = useState<'long' | 'short'>('long');
  const [notional, setNotional] = useState('1000');
  const [stop, setStop] = useState('');
  const [target, setTarget] = useState('');
  const [history, setHistory] = useState<{
    id: number;
    candles: Candle[];
  } | null>(null);
  const [historyError, setHistoryError] = useState('');
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [detailOpen, setDetailOpen] = useState(false);
  const [chartStrategies, setChartStrategies] = useState<string[]>([]);
  const [chartAuto, setChartAuto] = useState(true);
  const [clock, setClock] = useState(Date.now());
  const latest = useRef<PaperWallet | null>(null);
  const chartSection = useRef<HTMLElement | null>(null);
  const activitySection = useRef<HTMLDivElement | null>(null);
  function viewTrade(id: number) {
    setSelectedId(id);
    setDetailOpen(true);
    chartSection.current?.scrollIntoView({
      behavior: 'smooth',
      block: 'start',
    });
  }
  useEffect(() => {
    if (!detailOpen) return;
    const onKey = (event: KeyboardEvent) => event.key === 'Escape' && setDetailOpen(false);
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [detailOpen]);
  useEffect(() => {
    if (chartTrade !== null) viewTrade(chartTrade);
  }, [chartTrade]);
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
        const [result, autoState] = await Promise.all([fetchPaperWallet(), fetchAutoPaper()]);
        if (active) {
          latest.current = result;
          setWallet(result);
          setAuto(autoState);
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
  async function loadOlder(m15Bars = 1) {
    const before = Math.min(older[0]?.time ?? Infinity, wallet?.candles[0]?.time ?? Infinity);
    if (loadingOlder.current || !hasMore || !Number.isFinite(before)) return;
    loadingOlder.current = true;
    setOlderError('');
    try {
      const page = await fetchMexcCandles(before, Math.min(5000, 50 * m15Bars));
      setOlder((current) => [...page.candles, ...current]);
      setHasMore(page.has_more);
    } catch (cause) {
      setOlderError(
        cause instanceof Error ? cause.message : 'Không tải được nến cũ; kéo trái để thử lại.',
      );
    } finally {
      loadingOlder.current = false;
    }
  }
  async function refresh() {
    const [result, autoState] = await Promise.all([fetchPaperWallet(), fetchAutoPaper()]);
    latest.current = result;
    setWallet(result);
    setAuto(autoState);
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
    const order = {
      side,
      notional: Number(notional),
      stop: Number(stop),
      target: Number(target),
    };
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
  async function close(pauseAuto = false, tradeId = wallet?.position?.id) {
    if (tradeId === undefined) return;
    setBusy(true);
    setError('');
    setMessage('');
    try {
      if (pauseAuto) await setAutoPaperEnabled(false);
      await closePaperPosition(tradeId, commandId(`close-${tradeId}`));
      pending.current = null;
      setMessage('Đã đóng vị thế mô phỏng.');
      await refresh();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Không đóng được vị thế.');
    } finally {
      setBusy(false);
    }
  }
  const positions = wallet?.positions ?? [];
  const quote = wallet?.quote;
  const stale =
    !online ||
    !quote ||
    wallet?.stale ||
    clock / 1000 - quote.received_at > 20 ||
    Math.abs(clock / 1000 - quote.time) > 30;
  const selected =
    selectedId === -1
      ? null
      : (positions.find((trade) => trade.id === selectedId) ??
        wallet?.trades.find((trade) => trade.id === selectedId) ??
        wallet?.position ??
        null);
  const historicalId = selected?.exit_time != null ? selected.id : null;
  const exitTime = selected?.exit_time;
  useLayoutEffect(() => {
    const section = chartSection.current;
    if (!section) return;
    function resize() {
      const canvas = section!.querySelector<HTMLElement>('.trade-candlestick-chart');
      if (!canvas) return;
      const top = canvas.getBoundingClientRect().top + window.scrollY;
      section!.style.setProperty(
        '--chart-height',
        `${Math.max(240, window.innerHeight - top - 72)}px`,
      );
    }
    const observer = new ResizeObserver(resize);
    for (const element of [
      section.querySelector('.desk-chart-toolbar'),
      section.querySelector('.chart-ohlc'),
      section.querySelector('.desk-overlay-note'),
      section.closest('.futures-desk')?.querySelector('.desk-sticky-header'),
    ]) {
      if (element) observer.observe(element);
    }
    window.addEventListener('resize', resize);
    resize();
    return () => {
      observer.disconnect();
      window.removeEventListener('resize', resize);
    };
  }, [historicalId, history?.id]);
  useEffect(() => {
    let active = true;
    setHistory(null);
    setHistoryError('');
    if (historicalId !== null) {
      fetchPaperTradeCandles(historicalId).then(
        (candles) => {
          if (active) setHistory({ id: historicalId, candles });
        },
        (cause) => {
          if (active)
            setHistoryError(cause instanceof Error ? cause.message : 'Không tải được nến lịch sử.');
        },
      );
    }
    return () => {
      active = false;
    };
  }, [historicalId, exitTime]);
  const chartCandles =
    historicalId === null
      ? withLiveCandle(mergeBars(older, wallet?.candles ?? []), stream.candle)
      : history?.id === historicalId
        ? history.candles
        : [];
  const learningDecision = auto?.learner_decision;
  const strategyRows = learningDecision?.strategies ?? [];
  const decisionFresh =
    learningDecision?.auto_mode === auto?.auto_mode &&
    learningDecision?.time === Math.floor(clock / 900000) * 900 - 900;
  const botFresh =
    historicalId === null &&
    online &&
    !stale &&
    auto?.worker_online &&
    !auto?.learner_error &&
    decisionFresh;
  // "Theo bot": every strategy holding an open position, else the one the bot picked / first signal.
  // Tab #id focuses that position's strategy only.
  const openNames = [
    ...new Set(
      (selected && !selected.exit_time ? [selected] : positions)
        .map((trade) => trade.audit?.strategy)
        .filter(
          (name): name is string => !!name && strategyRows.some((row) => row.strategy === name),
        ),
    ),
  ];
  const picked = (
    strategyRows.find((row) => row.strategy === learningDecision?.selected) ??
    strategyRows.find((row) => row.status === 'signal')
  )?.strategy;
  const signalNames = picked ? [picked] : [];
  const autoNames = openNames.length ? openNames : signalNames;
  const allOpenNames = [
    ...new Set(
      positions.map((trade) => trade.audit?.strategy).filter((name): name is string => !!name),
    ),
  ];
  const shownNames = chartAuto ? autoNames : chartStrategies;
  const observations = botFresh
    ? strategyRows
        .filter((row) => shownNames.includes(row.strategy))
        .map((row) => ({ ...row, time: learningDecision!.time }))
    : [];
  const selectedRow = strategyRows.find((row) => row.strategy === learningDecision?.selected);
  const selectedTitle = selectedRow ? chartStrategyText(selectedRow).title : '';
  const nextCloseSeconds = 900 - (Math.floor(clock / 1000) % 900);
  return (
    <div className="futures-desk">
      <div className="desk-sticky-header">
        <PairStrip board={board} symbol={symbol} onSelect={onSelect} />
        <div className="mexc-top-dock">
          <div className="mexc-ticker-left">
            <span className="desk-paper-label" title="Vốn mô phỏng · Không gửi lệnh tới sàn">
              <ShieldCheck size={13} /> PAPER · 1×
            </span>
            <span className="mexc-pair-title">
              {symbol.replace('_', ' / ')} <small>Perp</small>
            </span>
            <strong className="mexc-last-price">
              {stream.candle
                ? formatQuote(stream.candle.close)
                : quote
                  ? formatQuote(quote.lastPrice)
                  : '—'}
            </strong>
            <span className="mexc-fair-price">
              Fair {quote ? formatQuote(quote.fairPrice) : '—'}
            </span>
            {quote?.fundingRate !== undefined && (
              <span
                className="mexc-stat"
                title={`${quote.fundingRate >= 0 ? 'Long trả Short' : 'Short trả Long'} · đếm ngược tới kỳ thanh toán funding`}
              >
                <span className="muted">Funding / Còn lại</span>
                <strong>
                  <span className={quote.fundingRate >= 0 ? 'negative' : 'positive'}>
                    {quote.fundingRate >= 0 ? '+' : ''}
                    {(quote.fundingRate * 100).toFixed(4)}%
                  </span>
                  {quote.nextFundingTime && (
                    <> / {fundingCountdown(quote.nextFundingTime - clock / 1000)}</>
                  )}
                </strong>
              </span>
            )}
            <span
              className="mexc-stat"
              title="Open Interest: số hợp đồng còn mở trên MEXC. Chỉ tham khảo bối cảnh, không kích hoạt lệnh."
            >
              <span className="muted">OI (hợp đồng) / 24h</span>
              <strong>
                {wallet?.open_interest
                  ? wallet.open_interest.contracts.toLocaleString('en-US', {
                      notation: 'compact',
                      maximumFractionDigits: 2,
                    })
                  : '—'}
                {' / '}
                {wallet?.open_interest?.change_24h_pct != null && !wallet.stale
                  ? `${wallet.open_interest.change_24h_pct > 0 ? '+' : ''}${wallet.open_interest.change_24h_pct.toFixed(2)}%`
                  : '—'}
                {wallet?.stale && <small> · cũ</small>}
              </strong>
            </span>
            <span className={`pill ${stream.live ? 'green' : 'amber'}`}>
              {stream.live ? 'LIVE 15m' : 'REST'}
            </span>
            <button
              type="button"
              className={`pill-btn ${auto?.enabled ? 'on' : ''}`}
              onClick={() => setOrderMode('auto')}
            >
              <Bot size={13} /> {auto?.enabled ? 'Auto ON' : 'Auto OFF'}
            </button>
          </div>
          <div className="mexc-wallet-strip" aria-label="Ví Paper">
            <div>
              <span className="muted">Số dư ví chung</span>
              <strong>{wallet ? formatPrice(wallet.balance) : '—'}</strong>
            </div>
            <div>
              <span className="muted">Equity chung</span>
              <strong>{wallet ? formatPrice(wallet.equity) : '—'}</strong>
            </div>
            <div>
              <span className="muted">PnL chưa chốt: Tất cả / Cặp này</span>
              {typeof wallet?.unrealized_pnl === 'number' ? (
                <strong>
                  <span className={wallet.unrealized_pnl < 0 ? 'negative' : 'positive'}>
                    {formatPrice(wallet.unrealized_pnl)}
                  </span>
                  {' / '}
                  <span className={(wallet.pair_unrealized_pnl ?? 0) < 0 ? 'negative' : 'positive'}>
                    {formatPrice(wallet.pair_unrealized_pnl ?? 0)}
                  </span>{' '}
                  <small>USDT</small>
                </strong>
              ) : (
                <strong>—</strong>
              )}
            </div>
            <div>
              <span className="muted">Khả dụng chung</span>
              <strong>{wallet ? formatPrice(wallet.available) : '—'}</strong>
            </div>
          </div>
        </div>
        {(stale || wallet?.funding_error || error || message) && (
          <div className="mexc-dock-alert" role="status">
            {stale && (
              <span className="negative">Giá khớp chậm / mất kết nối. {wallet?.error}</span>
            )}
            {wallet?.funding_error && (
              <span className="negative">Funding chờ đồng bộ: {wallet.funding_error}.</span>
            )}
            {Boolean(wallet?.portfolio_stale_symbols?.length) && (
              <span className="negative">
                Vị thế chưa có giá mark mới: {wallet?.portfolio_stale_symbols?.join(', ')}. Tạm dừng
                mở thêm lệnh.
              </span>
            )}
            {error && <span className="negative">{error}</span>}
            {message && <span className="positive">{message}</span>}
          </div>
        )}
      </div>
      <div className="paper-terminal">
        <section ref={chartSection} className="panel prose paper-market">
          <div className="desk-chart-toolbar">
            <button
              type="button"
              className="text-button"
              onClick={() =>
                activitySection.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
              }
            >
              Vị thế & lịch sử ↓
            </button>
            <div className="chart-mode-tabs" role="group" aria-label="Chế độ chart">
              <button
                className={`secondary ${!selected ? 'selected' : ''}`}
                aria-pressed={!selected}
                onClick={() => setSelectedId(-1)}
              >
                Thị trường
              </button>
              {positions.map((position) => (
                <button
                  key={position.id}
                  className={`secondary ${selected?.id === position.id ? 'selected' : ''}`}
                  aria-pressed={selected?.id === position.id}
                  onClick={() => setSelectedId(position.id)}
                >
                  #{position.id} {position.side.toUpperCase()}
                </button>
              ))}
              {historicalId !== null && (
                <span className="pill amber">ĐANG XEM LỊCH SỬ #{historicalId} · ĐÃ ĐÓNG</span>
              )}
            </div>
            {historicalId === null && (
              <div className="paper-strategy-toolbar">
                <label>
                  <input
                    type="checkbox"
                    checked={chartAuto}
                    onChange={(event) => setChartAuto(event.target.checked)}
                  />{' '}
                  Theo bot
                </label>
                <FuturesStrategyMenu
                  rows={strategyRows}
                  holding={allOpenNames}
                  selected={shownNames}
                  onChange={(names) => {
                    setChartAuto(false);
                    setChartStrategies(names);
                  }}
                />
              </div>
            )}
          </div>
          {historicalId === null && (
            <div
              className="desk-overlay-note"
              title="Chỉ đổi lớp vẽ, không đổi chiến thuật bot. Vùng chờ không phải lệnh vào."
            >
              <span>Chỉ đổi lớp vẽ, không đổi chiến thuật bot.</span>
              {!botFresh && <span role="status">Dữ liệu chưa sẵn sàng; chờ nến đánh giá mới.</span>}
              <span>
                {strategyRows.length} biến thể · {signalNames.length} có tín hiệu ·{' '}
                {strategyRows.filter((row) => row.status === 'no_signal').length} đang chờ
              </span>
              {chartAuto && !autoNames.length && (
                <span>Chưa có lệnh hoặc tín hiệu; chọn Chiến thuật để xem vùng chờ.</span>
              )}
            </div>
          )}
          {historicalId !== null && !history && (
            <p role="status">{historyError || 'Đang tải nến của lệnh lịch sử…'}</p>
          )}
          {(historicalId === null || history?.id === historicalId) && (
            <>
              <PaperChart
                candles={chartCandles}
                trade={selected ?? (historicalId === null ? (wallet?.position ?? null) : null)}
                positions={historicalId === null ? positions : []}
                onSelectPosition={setSelectedId}
                observations={observations}
                markPrice={wallet?.quote?.fairPrice ?? null}
                feeBps={wallet?.fee_bps ?? 0}
                markStale={stale}
                onLoadOlder={historicalId === null ? (bars) => void loadOlder(bars) : undefined}
              />
              {olderError && (
                <p className="negative" role="alert">
                  {olderError}
                </p>
              )}
            </>
          )}
          {historicalId === null && (
            <div className="desk-bot-line" role="status" aria-label="Trạng thái bot Futures">
              <Bot size={14} />
              <span
                className={`pill ${online && auto?.enabled && auto.worker_online && !auto.learner_error ? 'green' : 'amber'}`}
              >
                {!online
                  ? 'Mất kết nối'
                  : !auto
                    ? 'Đang tải'
                    : !auto.enabled
                      ? 'Đã dừng'
                      : !auto.worker_online
                        ? 'Worker ngoại tuyến'
                        : auto.learner_error
                          ? 'Lỗi bộ học'
                          : 'Auto ON'}
              </span>
              <span className="desk-bot-message">
                {!online
                  ? 'Mất kết nối · trạng thái có thể cũ'
                  : !auto
                    ? 'Đang tải trạng thái bot'
                    : !auto.enabled
                      ? 'Đã dừng vào lệnh mới'
                      : !auto.worker_online
                        ? 'Worker ngoại tuyến'
                        : auto.learner_error
                          ? `Lỗi bộ học: ${auto.learner_error}`
                          : !auto.learner_enabled
                            ? 'Bộ học đã tạm dừng'
                            : auto.status}
              </span>
              <span className="desk-bot-next">
                <Clock3 size={13} /> Nến kế tiếp{' '}
                <strong>
                  {Math.floor(nextCloseSeconds / 60)}:
                  {String(nextCloseSeconds % 60).padStart(2, '0')}
                </strong>
              </span>
            </div>
          )}
          <div className="paper-book">
            <span>
              Bid (worker) <strong>{quote ? formatQuote(quote.bid1) : '—'}</strong>
            </span>
            <span>
              Ask (worker) <strong>{quote ? formatQuote(quote.ask1) : '—'}</strong>
            </span>
          </div>
          <p className="chart-attribution">
            TradingView Lightweight Charts™ · Copyright (с) 2025 TradingView, Inc. ·{' '}
            <a href="https://www.tradingview.com/" target="_blank" rel="noreferrer">
              TradingView
            </a>
          </p>
        </section>
        {detailOpen && selected && (
          <div className="trade-drawer-backdrop" onClick={() => setDetailOpen(false)}>
            <aside
              className="trade-drawer"
              role="dialog"
              aria-modal="true"
              aria-label={`Chi tiết lệnh #${selected.id}`}
              onClick={(event) => event.stopPropagation()}
            >
              <button
                className="icon-button close"
                aria-label="Đóng chi tiết lệnh"
                onClick={() => setDetailOpen(false)}
              >
                ×
              </button>
              <PaperTradeDetails trade={selected} />
            </aside>
          </div>
        )}
        <aside className="desk-controls" aria-label="Điều khiển giao dịch Paper">
          <div className="desk-mode-switch" role="group" aria-label="Chế độ đặt lệnh">
            <button
              type="button"
              aria-pressed={orderMode === 'manual'}
              onClick={() => setOrderMode('manual')}
            >
              Thủ công
            </button>
            <button
              type="button"
              aria-pressed={orderMode === 'auto'}
              onClick={() => setOrderMode('auto')}
            >
              Auto Paper <span className={`status-dot ${auto?.enabled ? '' : 'stale-dot'}`} />
            </button>
          </div>
          <section hidden={orderMode !== 'manual'} className="panel prose paper-order">
            <div className="section-head">
              <h2>Đặt lệnh thủ công</h2>
              <span className="pill">MARKET</span>
            </div>
            {auto?.enabled && (
              <div className="auto-lock-card" role="status">
                <div>
                  <strong>Auto đang chạy</strong>
                  <span className="micro">
                    {auto.daily_trades}/{auto.max_daily_trades} lệnh hôm nay · Lỗ ngày{' '}
                    {auto.daily_loss_used_percent.toFixed(1)}%
                  </span>
                  <span className="micro">
                    {wallet?.position?.audit?.strategy
                      ? `Đang giữ ${tradeStrategy(wallet.position.audit)?.title}`
                      : auto.learner_decision?.selected
                        ? `Chờ ${selectedTitle}`
                        : 'Đang chờ tín hiệu'}
                  </span>
                </div>
                <button
                  type="button"
                  className="secondary"
                  disabled={busy}
                  onClick={() => {
                    setBusy(true);
                    void setAutoPaperEnabled(false)
                      .then(refresh)
                      .catch((e) => setError(String(e?.message ?? e)))
                      .finally(() => setBusy(false));
                  }}
                >
                  Dừng Auto
                </button>
              </div>
            )}
            <details className="desk-manual-form" open={!auto?.enabled}>
              <summary>Thông số lệnh thủ công</summary>
              <form onSubmit={(event) => void submit(event)}>
                <div className="period-tabs" role="group" aria-label="Chiều lệnh">
                  <button
                    type="button"
                    className={side === 'long' ? 'secondary selected' : 'secondary'}
                    aria-pressed={side === 'long'}
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
                    aria-pressed={side === 'short'}
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
                  disabled={stale || busy || !!auto?.enabled}
                >
                  {busy ? 'Đang xử lý…' : `Mở ${side.toUpperCase()} mô phỏng`}
                </button>
                <p className="micro">
                  Khối lượng làm tròn xuống theo hợp đồng MEXC. Phí {wallet?.fee_bps ?? 8} bps và
                  trượt giá {wallet?.slippage_bps ?? 2} bps mỗi chiều là giả định, chưa gồm độ sâu
                  sổ lệnh.
                </p>
              </form>
            </details>
          </section>
          <div hidden={orderMode !== 'auto'}>
            <AutoPaperPanel
              state={auto}
              stale={!!stale}
              online={online}
              clock={clock}
              onUpdated={refresh}
            />
          </div>
        </aside>
      </div>
      <div
        ref={activitySection}
        className="desk-activity-tabs"
        role="group"
        aria-label="Theo dõi giao dịch"
      >
        {[
          ['position', 'Vị thế đang mở', positions.length],
          ['history', 'Lịch sử lệnh', wallet?.trades.length ?? 0],
          ['decisions', 'Quyết định Auto', auto?.decisions.length ?? 0],
          ['evolution', 'Tiến hóa', ''],
        ].map(([key, label, count]) => (
          <button
            type="button"
            key={key}
            aria-pressed={activityTab === key}
            onClick={() => setActivityTab(String(key))}
          >
            {label} <span>{count}</span>
          </button>
        ))}
      </div>
      <div className="desk-scope" role="group" aria-label="Phạm vi bảng theo dõi">
        <button className="secondary" aria-pressed={!allPairs} onClick={() => setAllPairs(false)}>
          {base} / USDT
        </button>
        <button className="secondary" aria-pressed={allPairs} onClick={() => setAllPairs(true)}>
          Tất cả 10 cặp
        </button>
        <span className="micro">Phạm vi bảng độc lập với chart · bấm lệnh để xem chart</span>
      </div>
      {allPairs && <AllPairsPanel board={board} tab={activityTab} onSelect={onSelect} />}
      <div hidden={allPairs}>
        <section hidden={activityTab !== 'position'} className="panel prose desk-activity-panel">
          <div className="section-head">
            <h2>Vị thế đang mở ({positions.length})</h2>
          </div>
          {positions.some((position) => position.monitoring?.status === 'history_pending') && (
            <p className="negative" role="status">
              Thiếu nến đối soát SL/TP; Auto Paper chờ. SL/TP theo quote hiện tại vẫn chạy.
            </p>
          )}
          {positions.length ? (
            <PositionsTable
              showPair={false}
              rows={positions.map((p) => ({
                s: symbol,
                p,
                mark: quote?.fairPrice ?? null,
                feeBps: wallet?.fee_bps ?? 0,
                stale: !!stale,
              }))}
              onView={({ p }) => viewTrade(p.id)}
              actions={({ p }) => (
                <>
                  <button
                    className="secondary"
                    disabled={busy || stale}
                    onClick={() => void close(false, p.id)}
                  >
                    Đóng
                  </button>
                  {auto?.enabled && (
                    <button
                      className="secondary"
                      disabled={busy || stale}
                      onClick={() => void close(true, p.id)}
                    >
                      Dừng auto + đóng
                    </button>
                  )}
                </>
              )}
            />
          ) : (
            <p>Chưa có vị thế. Bật Auto Paper hoặc đặt lệnh thủ công bằng USDT ảo.</p>
          )}
          <p className="micro">
            Số dư = vốn ban đầu + PnL đã chốt − phí − funding. Equity cộng PnL chưa chốt theo fair
            price, chưa trừ phí đóng dự kiến. Funding thực tế được tính trễ khi nến fair-price tại
            kỳ quyết toán đã đóng.
          </p>
        </section>
        {activityTab === 'evolution' && <FuturesLearningPage />}
        <div hidden={activityTab !== 'decisions'} className="desk-activity-panel">
          <AutoDecisionList decisions={auto?.decisions ?? []} onViewTrade={viewTrade} />
        </div>
        <section hidden={activityTab !== 'history'} className="panel prose desk-activity-panel">
          <div className="section-head">
            <h2>Lịch sử lệnh mô phỏng</h2>
            <span className="muted">100 lệnh gần nhất · click để xem mức giá</span>
          </div>
          <TradeHistoryTable
            showPair={false}
            selectedId={selected?.id}
            rows={(wallet?.trades ?? []).map((t) => ({ s: symbol, t }))}
            onView={({ t }) => viewTrade(t.id)}
          />
          {!wallet?.trades.length && <p>Chưa có lệnh mô phỏng.</p>}
          <details>
            <summary>Giả định và giới hạn mô phỏng</summary>
            <p>
              Market fill tại bid/ask quan sát được cộng trượt giá cố định; toàn bộ khối lượng khớp
              ngay. SL/TP kích hoạt theo bid/ask lấy mẫu khoảng 5 giây và khớp tại giá quan sát kế
              tiếp, có thể vượt mức đặt. Không tái dựng tick bị bỏ lỡ khi mất kết nối. Chưa mô phỏng
              lệnh Limit hoặc khớp một phần tại sàn, liquidation, margin tiers hay ADL. Nhiều vị thế
              dùng chung một ví Cross; ký quỹ và phí bị giới hạn theo cấu hình ví. Chốt từng phần
              theo chính sách quản lý lệnh của cây. Funding dùng rate lịch sử và fair-price open 15m
              xấp xỉ, cập nhật cả lệnh đã đóng khi dữ liệu tới trễ. Không có API key hoặc lệnh gửi
              tới MEXC.
            </p>
          </details>
        </section>
      </div>
    </div>
  );
}
