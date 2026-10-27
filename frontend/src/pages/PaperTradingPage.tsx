import { useEffect, useLayoutEffect, useRef, useState, type FormEvent } from 'react';
import { Bot, Clock3, RefreshCw, ShieldCheck, X } from 'lucide-react';
import { Drawer } from '../components/Drawer';
import { FuturesStrategyMenu } from '../components/FuturesStrategyMenu';
import { AutoPaperPanel } from '../components/AutoPaperPanel';
import { FuturesLearningPage } from './FuturesLearningPage';
import { PaperTradeDetails } from '../components/PaperTradeDetails';
import type { Candle } from '../types/trading';
import { PaperChart } from '../components/PaperChart';
import { PaperPendingOrders } from '../components/PaperPendingOrders';
import { formatPrice, formatQuote, mexcErrorText } from '../lib/format';
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
import type { AutoPaperState, FuturesStrategyObservation, PaperWallet } from '../types/paper';
import { subscribeFuturesStream } from '../lib/futures-stream';
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
  chartTrade: { id: number } | null;
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
  // Chưa có phản hồi đầu tiên: hiện "đang kết nối", không báo mất kết nối giả.
  const [connecting, setConnecting] = useState(true);
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
    if (chartTrade) viewTrade(chartTrade.id);
  }, [chartTrade]);
  // Reuse a command ID after an ambiguous timeout; the API returns its original result.
  const pending = useRef<{ key: string; id: string } | null>(null);
  function commandId(key: string) {
    if (pending.current?.key !== key) pending.current = { key, id: crypto.randomUUID() };
    return pending.current!.id;
  }
  useEffect(() => {
    // Ví + Auto của cặp đang xem qua SSE (server tính một lần cho mọi tab), thay poll 2s.
    const stop = subscribeFuturesStream(
      `parts=selected&symbol=${symbol}`,
      {
        selected: (data) => {
          const next = (data as { wallet: PaperWallet | null }).wallet;
          if (next) {
            latest.current = next;
            setWallet(next);
          }
          return !!next;
        },
        auto: (data) => {
          if (!data) return false;
          setAuto(data as AutoPaperState);
        },
      },
      (ok) => {
        setOnline(ok);
        setConnecting(false);
      },
      async () => {
        const get = async (path: string) => {
          const response = await fetch(`${path}?symbol=${symbol}`, {
            signal: AbortSignal.timeout(10000),
          });
          if (!response.ok) throw new Error(`Futures: ${response.status}`);
          return response.json();
        };
        const [nextWallet, nextAuto] = await Promise.all([
          get('/api/paper/wallet'),
          get('/api/paper/auto'),
        ]);
        return { selected: { wallet: nextWallet }, auto: nextAuto };
      },
    );
    const ticker = setInterval(() => setClock(Date.now()), 1000);
    return () => {
      stop();
      clearInterval(ticker);
    };
  }, [symbol]);
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
  const pendingOrders = wallet?.pending_orders ?? [];
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
      // Controls are sticky under the sticky header; keep their top below it.
      const header = section!.closest('.futures-desk')?.querySelector('.desk-sticky-header');
      (header?.parentElement ?? section!).style.setProperty(
        '--desk-header-h',
        `${Math.ceil(header?.getBoundingClientRect().height ?? 0)}px`,
      );
      section!.style.setProperty(
        '--chart-height',
        `${Math.max(240, window.innerHeight - top - 72)}px`,
      );
    }
    const observer = new ResizeObserver(resize);
    for (const element of [
      section.querySelector('.desk-chart-toolbar'),
      section.querySelector('.chart-ohlc'),
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
  const chartKey = botFresh && shownNames.length
    ? JSON.stringify([symbol, learningDecision!.time, [...shownNames].sort()])
    : '';
  const [chartLayers, setChartLayers] = useState<{
    key: string;
    rows: FuturesStrategyObservation[];
    error?: string;
  }>({ key: '', rows: [] });
  useEffect(() => {
    if (!chartKey) return;
    const [pair, time, names] = JSON.parse(chartKey) as [string, number, string[]];
    const controller = new AbortController();
    let retry: ReturnType<typeof setTimeout> | undefined;
    const load = async () => {
      try {
        const query = new URLSearchParams({ candle_time: String(time), strategies: names.join(',') });
        const response = await fetch(`/api/workspaces/mexc_futures/learning/chart?${query}`, {
          headers: { 'X-Futures-Symbol': pair },
          signal: AbortSignal.any([controller.signal, AbortSignal.timeout(15000)]),
        });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const data = await response.json() as { time: number; strategies: FuturesStrategyObservation[] };
        if (data.time !== time) throw new Error('Mốc nến đã thay đổi');
        if (!controller.signal.aborted) setChartLayers({ key: chartKey, rows: data.strategies });
      } catch (error) {
        if (controller.signal.aborted) return;
        setChartLayers({ key: chartKey, rows: [], error: String(error) });
        retry = setTimeout(load, 5000);
      }
    };
    void load();
    return () => {
      controller.abort();
      clearTimeout(retry);
    };
  }, [chartKey]);
  const observations = botFresh
    ? strategyRows
        .filter((row) => shownNames.includes(row.strategy))
        .map((row) => ({
          ...row,
          ...(chartLayers.key === chartKey
            ? chartLayers.rows.find((layer) => layer.strategy === row.strategy)
            : undefined),
          time: learningDecision!.time,
        }))
    : [];
  const selectedRow = strategyRows.find((row) => row.strategy === learningDecision?.selected);
  const selectedTitle = selectedRow ? chartStrategyText(selectedRow).title : '';
  const nextCloseSeconds = 900 - (Math.floor(clock / 1000) % 900);
  return (
    <div className="futures-desk min-w-0 space-y-3">
      <div className="desk-sticky-header relative min-w-0 rounded-lg border border-border-base bg-surface-1 px-3 py-2 min-[1000px]:sticky min-[1000px]:top-0 min-[1000px]:z-30">
        <PairStrip board={board} symbol={symbol} onSelect={onSelect} />
        <div className="mexc-top-dock relative flex min-w-0 items-center gap-x-4 overflow-x-auto border-t border-border-base pt-2 text-xs [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
          <span className="desk-paper-label inline-flex shrink-0 items-center gap-1 rounded border border-amber-400/20 bg-amber-400/5 px-2 py-0.5 text-[10px] uppercase tracking-wider text-amber-300" title="Vốn mô phỏng · Không gửi lệnh tới sàn">
            <ShieldCheck size={12} /> PAPER
          </span>
          <span className="mexc-pair-title shrink-0 font-display text-sm font-semibold text-text-primary">
            {symbol.replace('_', ' / ')} <small className="text-[11px] text-text-muted">Perp</small>
          </span>
          <strong className="mexc-last-price shrink-0 font-mono text-base font-semibold tracking-tight text-text-primary">
            {stream.candle
              ? formatQuote(stream.candle.close)
              : quote
                ? formatQuote(quote.lastPrice)
                : '—'}
          </strong>
          <span className="mexc-fair-price flex shrink-0 items-baseline gap-1 font-mono text-xs text-text-muted">
            Fair {quote ? formatQuote(quote.fairPrice) : '—'}
          </span>
          {quote?.fundingRate !== undefined && (
            <div
              className="mexc-stat flex shrink-0 flex-col items-start gap-0.5 whitespace-nowrap font-mono text-xs"
              title={`${quote.fundingRate >= 0 ? 'Long trả Short' : 'Short trả Long'} · đếm ngược tới kỳ thanh toán funding`}
            >
              <span className="text-[11px] text-text-muted">Funding / Còn lại</span>
              <strong>
                <span className={quote.fundingRate >= 0 ? "negative text-bear" : "positive text-bull"}>
                  {quote.fundingRate >= 0 ? '+' : ''}
                  {(quote.fundingRate * 100).toFixed(4)}%
                </span>
                {quote.nextFundingTime && (
                  <span className="text-text-muted"> / {fundingCountdown(quote.nextFundingTime - clock / 1000)}</span>
                )}
              </strong>
            </div>
          )}
          <div
            className="mexc-stat hidden shrink-0 flex-col items-start gap-0.5 whitespace-nowrap font-mono text-xs sm:flex"
            title="Open Interest: số hợp đồng còn mở trên MEXC."
          >
            <span className="text-[11px] text-text-muted">OI / 24h</span>
            <strong>
              {wallet?.open_interest
                ? wallet.open_interest.contracts.toLocaleString('en-US', {
                    notation: 'compact',
                    maximumFractionDigits: 1,
                  })
                : '—'}
              {wallet?.open_interest?.change_24h_pct != null && !wallet.stale && (
                <span className={wallet.open_interest.change_24h_pct > 0 ? "text-bull" : "text-bear"}>
                  {' '}{wallet.open_interest.change_24h_pct > 0 ? '+' : ''}{wallet.open_interest.change_24h_pct.toFixed(1)}%
                </span>
              )}
            </strong>
          </div>
          <div className="h-4 w-px shrink-0 bg-border-base" aria-hidden="true" />
          <div className="flex shrink-0 flex-col items-start gap-0.5 whitespace-nowrap font-mono text-xs">
            <span className="text-[11px] text-text-muted">Số dư ví</span>
            <strong className="text-text-primary">{wallet ? formatPrice(wallet.balance) : '—'}</strong>
          </div>
          <div className="flex shrink-0 flex-col items-start gap-0.5 whitespace-nowrap font-mono text-xs">
            <span className="text-[11px] text-text-muted">Equity</span>
            <strong className="text-text-primary">{wallet ? formatPrice(wallet.equity) : '—'}</strong>
          </div>
          <div className="flex shrink-0 flex-col items-start gap-0.5 whitespace-nowrap font-mono text-xs">
            <span className="text-[11px] text-text-muted" title="PnL chưa chốt của toàn ví / của cặp đang xem">PnL mở</span>
            {typeof wallet?.unrealized_pnl === 'number' ? (
              <strong>
                <span className={wallet.unrealized_pnl < 0 ? "text-bear" : "text-bull"}>
                  {formatPrice(wallet.unrealized_pnl)}
                </span>
                {' / '}
                <span className={(wallet.pair_unrealized_pnl ?? 0) < 0 ? "text-bear" : "text-bull"}>
                  {formatPrice(wallet.pair_unrealized_pnl ?? 0)}
                </span>
              </strong>
            ) : (
              <strong>—</strong>
            )}
          </div>
          <div className="flex shrink-0 flex-col items-start gap-0.5 whitespace-nowrap font-mono text-xs">
            <span className="text-[11px] text-text-muted">Khả dụng</span>
            <strong className="text-text-primary">{wallet ? formatPrice(wallet.available) : '—'}</strong>
          </div>
          <div className="h-4 w-px shrink-0 bg-border-base" aria-hidden="true" />
          <span
            className={`pill ${stream.live ? 'green' : 'amber'} inline-flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 text-[11px] font-medium bg-surface-3 text-text-secondary`}
          >
            {stream.live ? 'LIVE 15m' : 'REST'}
          </span>
          <span
            className={`pill ${
              !wallet?.stale &&
              wallet?.quote?.source === 'websocket' &&
              clock - wallet.quote.time * 1000 < 5000
                ? 'green'
                : 'amber'
            } inline-flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 text-[11px] font-medium bg-surface-3 text-text-secondary`}
          >
            SL/TP: {!wallet?.quote ? 'giá cũ' : wallet.quote.source === 'websocket' ? 'WS' : 'REST'}
          </span>
          <button
            type="button"
            className={`pill-btn ${auto?.enabled ? 'on' : ''} ml-auto inline-flex shrink-0 items-center gap-1.5 rounded border border-border-base bg-surface-2 px-2.5 py-1 text-xs [&.is-on]:text-bull [&.on]:text-bull`}
            onClick={() => setOrderMode('auto')}
          >
            <Bot size={13} /> {auto?.enabled ? 'Auto ON' : 'Auto OFF'}
          </button>
        </div>
        {((stale && !connecting) || wallet?.funding_error || error || message) && (
          <div className="mexc-dock-alert mt-2 rounded border border-amber-400/30 p-2 text-xs text-amber-200" role="status">
            {stale && !connecting && (
              <span className="negative text-bear">
                Giá khớp chậm / mất kết nối. {mexcErrorText(wallet?.error)}
              </span>
            )}
            {wallet?.funding_error && (
              <span className="negative text-bear">Funding chờ đồng bộ: {wallet.funding_error}.</span>
            )}
            {Boolean(wallet?.portfolio_stale_symbols?.length) && (
              <span className="negative text-bear">
                Vị thế chưa có giá mark mới: {wallet?.portfolio_stale_symbols?.join(', ')}. Tạm dừng
                mở thêm lệnh.
              </span>
            )}
            {error && <span className="negative text-bear">{error}</span>}
            {message && <span className="positive text-bull">{message}</span>}
          </div>
        )}
      </div>
      <div className="paper-terminal relative grid min-w-0 gap-3 bg-canvas xl:grid-cols-[minmax(0,1fr)_300px]">
        <section ref={chartSection} className="paper-market min-w-0 overflow-hidden rounded-lg border border-border-base bg-surface-1 text-sm text-text-secondary">
          <div className="desk-chart-toolbar flex min-w-0 flex-wrap items-center gap-2 border-b border-border-base px-3 py-2 text-xs">
            <div className="desk-toolbar-lead flex min-w-0 flex-wrap items-center gap-2 text-xs">
              <button
                type="button"
                className="text-button inline-flex items-center gap-1 text-xs text-bull underline-offset-4 hover:underline"
                onClick={() =>
                  activitySection.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
                }
              >
                Vị thế & lịch sử ↓
              </button>
              {historicalId === null && (
                <span
                  className="desk-strategy-count text-xs text-text-muted"
                  title="Chiến thuật bot học đang theo dõi ở nến vừa đóng: số có tín hiệu và số đang chờ setup. Menu Chiến thuật chỉ đổi lớp vẽ trên chart, không đổi chiến thuật bot; vùng chờ không phải lệnh vào."
                >
                  {strategyRows.length} chiến thuật · {signalNames.length} tín hiệu ·{' '}
                  {strategyRows.filter((row) => row.status === 'no_signal').length} chờ
                </span>
              )}
              {historicalId === null && !botFresh && (
                <span className="desk-strategy-stale hidden text-xs text-text-muted 2xl:inline" role="status">
                  Chờ nến đánh giá mới
                </span>
              )}
            </div>
            <div className="chart-mode-tabs flex flex-wrap items-center gap-1 [&>button]:rounded [&>button]:px-2 [&>button]:py-1 [&>button]:text-xs [&>button]:text-text-secondary [&>button:hover]:bg-surface-3 [&>button[aria-pressed=true]]:bg-surface-3 [&>button[aria-pressed=true]]:text-bull border-b border-border-subtle py-1" role="group" aria-label="Chế độ chart">
              <button
                className={`secondary ${!selected ? 'selected' : ''} inline-flex items-center justify-center gap-1.5 rounded-md border border-border-active bg-surface-2 px-3 py-1.5 text-xs text-text-secondary hover:bg-surface-3`}
                aria-pressed={!selected}
                onClick={() => setSelectedId(-1)}
                title="Xem chart giá thị trường, không gắn vào vị thế nào"
              >
                Giá thị trường
              </button>
              {positions.map((position) => (
                <button
                  key={position.id}
                  className={`secondary ${selected?.id === position.id ? 'selected' : ''} inline-flex items-center justify-center gap-1.5 rounded-md border border-border-active bg-surface-2 px-3 py-1.5 text-xs text-text-secondary hover:bg-surface-3`}
                  aria-pressed={selected?.id === position.id}
                  onClick={() => setSelectedId(position.id)}
                >
                  #{position.id} {position.side.toUpperCase()}
                </button>
              ))}
              {historicalId !== null && (
                <span className="pill amber inline-flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 text-xs font-medium bg-surface-3 text-text-secondary text-amber-300">ĐANG XEM LỊCH SỬ #{historicalId} · ĐÃ ĐÓNG</span>
              )}
            </div>
            {historicalId === null && (
              <div className="paper-strategy-toolbar ml-auto flex min-w-0 flex-wrap items-center gap-3">
                <label
                  className="chart-follow inline-flex min-h-9 items-center gap-2 text-xs text-bull"
                  title="Tự hiện trên chart các cây bot đang giữ lệnh, hoặc cây bot vừa chọn"
                >
                  <input
                    type="checkbox"
                    checked={chartAuto}
                    onChange={(event) => setChartAuto(event.target.checked)}
                  />
                  Tự theo bot
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
          {historicalId !== null && !history && (
            <p role="status">{historyError || 'Đang tải nến của lệnh lịch sử…'}</p>
          )}
          {(historicalId === null || history?.id === historicalId) && (
            <>
              <PaperChart
                candles={chartCandles}
                trade={selected ?? (historicalId === null ? (wallet?.position ?? null) : null)}
                positions={historicalId === null ? positions : []}
                pendingOrders={historicalId === null ? pendingOrders : []}
                onSelectPosition={setSelectedId}
                observations={observations}
                markPrice={wallet?.quote?.fairPrice ?? null}
                feeBps={wallet?.fee_bps ?? 0}
                markStale={stale}
                onLoadOlder={historicalId === null ? (bars) => void loadOlder(bars) : undefined}
              />
              {chartKey && (chartLayers.key !== chartKey || chartLayers.error) && (
                <p className="muted text-text-muted" role="status">
                  {chartLayers.key === chartKey && chartLayers.error
                    ? `Chưa tải được lớp chiến thuật (${chartLayers.error}); đang thử lại. Không ảnh hưởng bot.`
                    : 'Đang tải lớp chiến thuật đã chọn…'}
                </p>
              )}
              {olderError && (
                <p className="negative text-bear" role="alert">
                  {olderError}
                </p>
              )}
            </>
          )}
          {historicalId === null && (
            <div className="desk-bot-line flex min-w-0 flex-wrap items-center gap-2 justify-between border-y border-border-base px-3 py-2 text-xs" role="status" aria-label="Trạng thái bot Futures">
              <Bot size={14} />
              <span
                className={`pill ${online && auto?.enabled && auto.worker_online && !auto.learner_error ? 'green' : 'amber'} inline-flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 text-xs font-medium bg-surface-3 text-text-secondary`}
              >
                {connecting
                  ? 'Đang kết nối'
                  : !online
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
              <span className="desk-bot-message text-amber-200">
                {connecting
                  ? 'Đang tải trạng thái bot…'
                  : !online
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
              <span className="desk-bot-next ml-auto inline-flex items-center gap-1.5 text-text-muted [&_strong]:font-mono">
                <Clock3 size={13} /> Nến kế tiếp đóng sau{' '}
                <strong>
                  {Math.floor(nextCloseSeconds / 60)}:
                  {String(nextCloseSeconds % 60).padStart(2, '0')}
                </strong>
              </span>
            </div>
          )}
          <div className="paper-book flex min-w-0 flex-wrap gap-5 px-3 pt-3 text-xs text-text-muted [&_strong]:ml-2 [&_strong]:font-mono [&_strong]:text-text-secondary">
            <span>
              Bid (worker) <strong>{quote ? formatQuote(quote.bid1) : '—'}</strong>
            </span>
            <span>
              Ask (worker) <strong>{quote ? formatQuote(quote.ask1) : '—'}</strong>
            </span>
          </div>
          <p className="chart-attribution px-3 py-2 text-right text-xs text-text-muted">
            TradingView Lightweight Charts™ · Copyright (с) 2025 TradingView, Inc. ·{' '}
            <a href="https://www.tradingview.com/" target="_blank" rel="noreferrer">
              TradingView
            </a>
          </p>
        </section>
        {detailOpen && selected && (
          <Drawer label={`Chi tiết lệnh #${selected.id}`} onClose={() => setDetailOpen(false)}>
            <button
              className="icon-button close absolute right-3 top-3 inline-flex size-8 shrink-0 items-center justify-center rounded text-text-muted hover:bg-surface-2 hover:text-text-primary"
              aria-label="Đóng chi tiết lệnh"
              onClick={() => setDetailOpen(false)}
            >
              <X size={18} />
            </button>
            <PaperTradeDetails trade={selected} symbol={symbol} />
          </Drawer>
        )}
        <aside className="desk-controls min-w-0 space-y-3 rounded-xl border border-border-base bg-surface-1 p-3 [&_button]:disabled:cursor-not-allowed [&_button]:disabled:opacity-50 [&_button]:focus-visible:outline-2 [&_button]:focus-visible:outline-sky-400 xl:sticky xl:top-[calc(var(--desk-header-h,120px)+12px)] xl:max-h-[calc(100dvh-var(--desk-header-h,120px)-24px)] xl:self-start xl:overflow-y-auto" aria-label="Điều khiển giao dịch Paper">
          <div className="desk-mode-switch grid grid-cols-2 gap-1 rounded-md bg-canvas p-1 [&>button]:flex [&>button]:h-9 [&>button]:items-center [&>button]:justify-center [&>button]:gap-1.5 [&>button]:rounded [&>button]:text-xs [&>button]:font-semibold [&>button]:text-text-muted [&>button:hover]:text-text-primary [&>button[aria-pressed=true]]:bg-surface-3 [&>button[aria-pressed=true]]:text-text-primary" role="group" aria-label="Chế độ đặt lệnh">
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
              Auto Paper <span className={`status-dot ${auto?.enabled ? '' : 'stale-dot'} inline-block size-1.5 shrink-0 rounded-full bg-bull [&.stale-dot]:bg-amber-400`} />
            </button>
          </div>
          <section hidden={orderMode !== 'manual'} className="paper-order min-w-0 space-y-4 px-1 pt-2 text-sm text-text-secondary">
            <div className="section-head mb-3 flex min-w-0 flex-wrap items-center justify-between gap-2 [&_p]:text-xs [&_p]:text-text-muted">
              <h2 className="text-sm font-semibold text-text-primary">Đặt lệnh thủ công</h2>
              <span className="pill inline-flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 text-xs font-medium bg-surface-3 text-text-secondary">MARKET</span>
            </div>
            {auto?.enabled && (
              <div className="auto-lock-card min-w-0 space-y-3 rounded-lg border border-bull/25 bg-bull/5 p-3" role="status">
                <div className="flex min-w-0 flex-col gap-1.5 break-words">
                  <strong className="text-bull">Auto đang chạy</strong>
                  <span className="micro text-xs leading-relaxed text-text-muted">
                    {auto.daily_trades}/{auto.max_daily_trades} lệnh hôm nay · Lỗ ngày{' '}
                    {auto.daily_loss_used_percent.toFixed(1)}%
                  </span>
                  <span className="text-xs leading-relaxed text-text-muted">Dừng Auto để mở lệnh thủ công. Vị thế đang mở vẫn được quản lý SL/TP.</span>
                  <span className="micro text-xs leading-relaxed text-text-muted">
                    {wallet?.position?.audit?.strategy
                      ? `Đang giữ ${tradeStrategy(wallet.position.audit)?.title}`
                      : auto.learner_decision?.selected
                        ? `Chờ ${selectedTitle}`
                        : 'Đang chờ tín hiệu'}
                  </span>
                </div>
                <button
                  type="button"
                  className="secondary inline-flex items-center justify-center gap-1.5 rounded-md border border-border-active bg-surface-2 px-3 py-1.5 text-xs text-text-secondary hover:bg-surface-3"
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
            <details className="desk-manual-form min-w-0 rounded-lg border border-border-subtle [&_label]:flex [&_label]:min-w-0 [&_label]:flex-col [&_label]:gap-2 [&_label]:text-xs [&_label]:text-text-muted [&_input]:h-11 [&_input]:w-full [&_input]:min-w-0 [&_input]:rounded-lg [&_input]:border [&_input]:border-border-active [&_input]:bg-canvas [&_input]:px-3 [&_input]:font-mono [&_input]:text-sm [&_input]:text-text-primary [&_input]:focus-visible:outline-2 [&_input]:focus-visible:outline-sky-400 [&_select]:w-full" open={!auto?.enabled}>
              <summary className="cursor-pointer px-3 py-2.5 text-xs font-medium text-text-primary focus-visible:outline-2 focus-visible:outline-sky-400">Thông số lệnh thủ công</summary>
              <form className="grid gap-4 border-t border-border-subtle p-3" onSubmit={(event) => void submit(event)}>
                <div className="grid grid-cols-2 gap-2" role="group" aria-label="Chiều lệnh">
                  <button
                    type="button"
                    className={`h-10 rounded-md border text-sm font-semibold transition-colors ${side === 'long' ? 'border-bull bg-bull text-canvas' : 'border-border-base bg-surface-2 text-text-muted hover:border-bull/50 hover:text-bull'}`}
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
                    className={`h-10 rounded-md border text-sm font-semibold transition-colors ${side === 'short' ? 'border-bear bg-bear text-white' : 'border-border-base bg-surface-2 text-text-muted hover:border-bear/50 hover:text-bear'}`}
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
                  Giá stop-loss
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
                  Giá take-profit
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
                  className="text-button inline-flex items-center gap-1 text-xs text-bull underline-offset-4 hover:underline"
                  disabled={stale || busy}
                  onClick={() => suggestLevels()}
                >
                  <RefreshCw size={13} /> Điền SL 1% / TP 2% để thử
                </button>
                <button
                  type="submit"
                  className={`inline-flex min-h-11 w-full items-center justify-center gap-2 rounded-md px-3 py-2 text-sm font-semibold ${side === 'long' ? 'bg-bull text-canvas hover:bg-emerald-300' : 'bg-bear text-white hover:bg-rose-400'}`}
                  disabled={stale || busy || !!auto?.enabled}
                >
                  {busy ? 'Đang xử lý…' : `Mở ${side.toUpperCase()} mô phỏng`}
                </button>
                <p className="micro text-xs leading-relaxed text-text-muted">
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
              onViewTrade={viewTrade}
            />
          </div>
        </aside>
      </div>
      <div
        ref={activitySection}
        className="desk-activity-tabs flex flex-wrap items-center gap-1 rounded-lg border border-border-base bg-surface-1 p-1.5 [&>button]:min-h-10 [&>button]:rounded-md [&>button]:px-4 [&>button]:text-xs [&>button]:font-medium [&>button]:text-text-muted [&>button:hover]:bg-surface-2 [&>button[aria-pressed=true]]:bg-surface-3 [&>button[aria-pressed=true]]:text-text-primary"
        role="group"
        aria-label="Theo dõi giao dịch"
      >
        {[
          ['position', 'Vị thế đang mở', positions.length],
          [
            'pending',
            'Lệnh chờ',
            allPairs
              ? Object.values(board).reduce(
                  (count, pair) =>
                    count +
                    (pair.wallet?.pending_orders ?? []).filter(
                      (order) => order.status === 'pending',
                    ).length,
                  0,
                )
              : pendingOrders.filter((order) => order.status === 'pending').length,
          ],
          ['history', 'Lịch sử lệnh', wallet?.trades.length ?? 0],
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
      <div className="desk-scope ml-auto flex flex-wrap items-center gap-2 text-xs text-text-muted" role="group" aria-label="Phạm vi bảng theo dõi">
        <button className="secondary inline-flex items-center justify-center gap-1.5 rounded-md border border-border-active bg-surface-2 px-3 py-1.5 text-xs text-text-secondary hover:bg-surface-3" aria-pressed={!allPairs} onClick={() => setAllPairs(false)}>
          {base} / USDT
        </button>
        <button className="secondary inline-flex items-center justify-center gap-1.5 rounded-md border border-border-active bg-surface-2 px-3 py-1.5 text-xs text-text-secondary hover:bg-surface-3" aria-pressed={allPairs} onClick={() => setAllPairs(true)}>
          Tất cả 10 cặp
        </button>
        <span className="micro text-xs leading-relaxed text-text-muted">Phạm vi bảng độc lập với chart · bấm lệnh để xem chart</span>
      </div>
      {allPairs && <AllPairsPanel board={board} tab={activityTab} onSelect={onSelect} />}
      <div hidden={allPairs}>
        {activityTab === 'pending' && (
          <section className="panel prose desk-activity-panel min-w-0 rounded-lg border border-border-base bg-surface-1 p-4 space-y-3 text-sm leading-relaxed text-text-secondary min-w-0">
            <PaperPendingOrders
              orders={pendingOrders}
              onView={(order) => {
                if (order.trade_id) viewTrade(order.trade_id);
                else setSelectedId(-1);
                window.scrollTo({ top: 0, behavior: 'smooth' });
              }}
            />
          </section>
        )}
        <section hidden={activityTab !== 'position'} className="panel prose desk-activity-panel min-w-0 rounded-lg border border-border-base bg-surface-1 p-4 space-y-3 text-sm leading-relaxed text-text-secondary min-w-0">
          <div className="section-head mb-3 flex min-w-0 flex-wrap items-center justify-between gap-2 [&_p]:text-xs [&_p]:text-text-muted">
            <h2>Vị thế đang mở ({positions.length})</h2>
          </div>
          {positions.some((position) => position.monitoring?.status === 'history_pending') && (
            <p className="negative text-bear" role="status">
              Thiếu nến đối soát SL/TP; đang chờ đồng bộ. Auto Paper và SL/TP theo quote hiện tại
              vẫn chạy.
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
                    className="secondary inline-flex items-center justify-center gap-1.5 rounded-md border border-border-active bg-surface-2 px-3 py-1.5 text-xs text-text-secondary hover:bg-surface-3"
                    disabled={busy || stale}
                    onClick={() => void close(false, p.id)}
                  >
                    Đóng
                  </button>
                  {auto?.enabled && (
                    <button
                      className="secondary inline-flex items-center justify-center gap-1.5 rounded-md border border-border-active bg-surface-2 px-3 py-1.5 text-xs text-text-secondary hover:bg-surface-3"
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
          <p className="micro text-xs leading-relaxed text-text-muted">
            Số dư = vốn ban đầu + PnL đã chốt − phí − funding. Equity cộng PnL chưa chốt theo fair
            price, chưa trừ phí đóng dự kiến. Funding thực tế được tính trễ khi nến fair-price tại
            kỳ quyết toán đã đóng.
          </p>
        </section>
        {activityTab === 'evolution' && <FuturesLearningPage onViewTrade={viewTrade} />}
        <section hidden={activityTab !== 'history'} className="panel prose desk-activity-panel min-w-0 rounded-lg border border-border-base bg-surface-1 p-4 space-y-3 text-sm leading-relaxed text-text-secondary min-w-0">
          <div className="section-head mb-3 flex min-w-0 flex-wrap items-center justify-between gap-2 [&_p]:text-xs [&_p]:text-text-muted">
            <h2>Lịch sử lệnh mô phỏng</h2>
            <span className="muted text-text-muted">100 lệnh gần nhất · click để xem mức giá</span>
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
              khớp một phần tại sàn, margin tiers hay ADL. Limit Paper xét nến M15 đóng, khớp khi
              giá xuyên mức chờ, phí taker bảo thủ, tối đa 4 nến; không mô phỏng hàng đợi sàn.
              Retest xác nhận rồi vào market. Nhiều vị thế dùng chung một ví Cross; ký quỹ và phí bị
              giới hạn theo cấu hình ví. Chốt từng phần theo chính sách quản lý lệnh của cây.
              Funding dùng rate lịch sử và fair-price open 15m xấp xỉ, cập nhật cả lệnh đã đóng khi
              dữ liệu tới trễ. Không có API key hoặc lệnh gửi tới MEXC.
            </p>
          </details>
        </section>
      </div>
    </div>
  );
}
