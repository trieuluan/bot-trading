import { useEffect, useId, useState } from 'react';
import { Activity, ChevronDown, ChevronRight, ReceiptText, Target, TrendingUp, Zap } from 'lucide-react';
import { FUTURES_SYMBOLS, type FuturesSymbol } from '../lib/futures-symbol';
import { formatPrice, formatQuote } from '../lib/format';
import { subscribeFuturesStream } from '../lib/futures-stream';
import {
  estimatedPaperPnl,
  summarizePaperTrades,
  tradePartialProgress,
} from '../lib/paper-position';
import { liveSignals } from '../lib/futures-signals';
import { exitLabels } from './PaperTradeDetails';
import { PaperPendingOrders } from './PaperPendingOrders';
import {
  exitLabel,
  tradeStrategy,
  strategyShortTitle,
  type Genome,
} from '../lib/futures-strategy-info';

function StrategyCell({ trade }: { trade: PaperTrade }) {
  const info = tradeStrategy(trade.audit);
  const id = `strat-${useId().replace(/:/g, '')}`;
  const [pos, setPos] = useState({ top: 0, left: 0 });
  if (!info)
    return (
      <>
        {trade.origin === 'manual' ? 'Thủ công' : 'Auto · thiếu dữ liệu cây'}
        <small>{trade.entry_reason ?? ''}</small>
      </>
    );
  return (
    <>
      <span className="strat-title text-xs font-semibold text-text-primary">
        <strong>{info.title}</strong>
        {info.generation && <span className="strat-gen block text-xs text-text-muted">{info.generation}</span>}
      </span>
      <small className="strat-exit block text-xs text-text-muted">
        {trade.monitoring?.exit_policy
          ? exitLabel(trade.monitoring.exit_policy)
          : exitLabel(trade.audit?.exit_policy ?? trade.audit?.genome?.exit_policy)}
      </small>
      <button
        type="button"
        className="text-button strategy-detail-btn inline-flex items-center gap-1 text-xs text-bull underline-offset-4 hover:underline"
        popoverTarget={id}
        aria-haspopup="dialog"
        onClick={(e) => {
          const r = e.currentTarget.getBoundingClientRect();
          setPos({
            top: Math.max(8, Math.min(r.bottom + 8, window.innerHeight - 308)),
            left: Math.max(8, Math.min(r.left, window.innerWidth - 388)),
          });
        }}
      >
        Chi tiết
      </button>
      <div
        id={id}
        popover="auto"
        role="dialog"
        aria-label={`Chi tiết ${info.title}`}
        className="tsp fixed z-[80] max-h-[70dvh] max-w-[min(360px,calc(100vw-24px))] overflow-auto rounded-lg border border-border-active bg-surface-2 p-3 text-xs text-text-secondary shadow-lg w-[min(420px,calc(100vw-24px))]"
        style={pos}
      >
        <header className="tsp-head flex flex-wrap items-center justify-between gap-2 border-b border-border-base py-2">
          <span className="tsp-title flex flex-wrap items-center justify-between gap-2 border-b border-border-base py-2">{info.title}</span>
          {info.generation && <span className="tsp-gen text-xs text-text-muted">{info.generation}</span>}
          <button
            type="button"
            className="tsp-close ml-auto text-text-muted hover:text-text-primary"
            popoverTarget={id}
            popoverTargetAction="hide"
            aria-label="Đóng chi tiết chiến thuật"
          >
            ×
          </button>
        </header>
        <div className="tsp-body space-y-2 py-2">
          <div className="tsp-sec space-y-2 py-2">
            <span className="tsp-label text-xs text-text-muted">Chiến thuật</span>
            <span>{info.detail}</span>
          </div>
          {(trade.audit?.reason ?? trade.entry_reason) && (
            <div className="tsp-sec space-y-2 py-2">
              <span className="tsp-label text-xs text-text-muted">Lý do vào lệnh</span>
              <span>{trade.audit?.reason ?? trade.entry_reason}</span>
            </div>
          )}
          <div className="tsp-sec space-y-2 py-2">
            <span className="tsp-label text-xs text-text-muted">Nguồn dữ liệu</span>
            <span>{info.source}</span>
          </div>
        </div>
        <footer className="tsp-foot flex flex-wrap items-center justify-between gap-2 border-b border-border-base py-2">
          <code>{trade.audit?.strategy}</code>
        </footer>
      </div>
    </>
  );
}
import type { AutoPaperState, PaperTrade, PaperWallet } from '../types/paper';
import { formatTime } from '../lib/time-format';

export type PairInfo = {
  wallet: PaperWallet | null;
  auto: AutoPaperState | null;
  learning: {
    population: number;
    promoted: number;
    samples: number;
    error: string | null;
    strategies: {
      strategy: string;
      genome: Genome;
      resolved: number;
      mean_r: number | null;
      paused?: boolean;
      open_trades?: unknown[];
      learned_samples?: number;
      learned_mean_r?: number | null;
      paper?: { closed: number; wins: number; net_pnl: number };
    }[];
  } | null;
};
export type Board = Record<FuturesSymbol, PairInfo>;
const MIN_TOP_SAMPLES = 10;

const empty = Object.fromEntries(
  FUTURES_SYMBOLS.map((s) => [s, { wallet: null, auto: null, learning: null }]),
) as Board;

export function useFuturesBoard(active: boolean): Board {
  const [board, setBoard] = useState<Board>(empty);
  useEffect(() => {
    if (!active) return;
    const merge = (part: 'board' | 'learning') => (data: unknown) => {
      const symbols = (data as { symbols: Record<string, unknown> }).symbols;
      setBoard((current) => {
        const next = { ...current };
        for (const s of FUTURES_SYMBOLS) {
          const row = symbols[s];
          if (!row) continue;
          next[s] =
            part === 'board'
              ? { ...next[s], ...(row as Omit<PairInfo, 'learning'>) }
              : { ...next[s], learning: row as PairInfo['learning'] };
        }
        return next;
      });
    };
    return subscribeFuturesStream(
      'parts=board',
      { board: merge('board'), learning: merge('learning') },
      () => undefined,
      async () => {
        const response = await fetch('/api/workspaces/mexc_futures/board', {
          signal: AbortSignal.timeout(10000),
        });
        if (!response.ok) throw new Error(`Board: ${response.status}`);
        const data = await response.json();
        return {
          board: data,
          learning: {
            symbols: Object.fromEntries(
              FUTURES_SYMBOLS.map((pair) => [pair, data.symbols[pair]?.learning]),
            ),
          },
        };
      },
    );
  }, [active]);
  return board;
}

const sign = (v: number) => (v < 0 ? 'negative' : 'positive');
const short = (s: string) => s.replace('_USDT', '');

export type PositionRow = { s: string; p: PaperTrade; mark: number | null; feeBps: number; stale?: boolean };

const fmtTime = (t: number) => formatTime(t, false, false);
const span = (a: number, b: number) => {
  const m = Math.max(0, Math.round((b - a) / 60));
  return m >= 1440
    ? `${Math.floor(m / 1440)}n ${Math.floor((m % 1440) / 60)}g`
    : m >= 60
      ? `${Math.floor(m / 60)}g ${m % 60}p`
      : `${m}p`;
};

type HistoryFilter = 'all' | 'win' | 'loss' | 'auto' | 'manual';
const historyFilters: [HistoryFilter, string][] = [
  ['all', 'Tất cả'],
  ['win', 'Thắng'],
  ['loss', 'Thua'],
  ['auto', 'Auto'],
  ['manual', 'Thủ công'],
];
const PAGE = 25;

/** Rủi ro theo SL (USDT) để quy đổi PnL ra R. */
const riskOf = (t: PaperTrade) => Math.abs(t.entry - t.stop) * t.quantity;

export function TradeHistoryTable({
  rows,
  showPair,
  selectedId,
  onView,
}: {
  rows: { s: string; t: PaperTrade }[];
  showPair: boolean;
  selectedId?: number | null;
  onView: (row: { s: string; t: PaperTrade }) => void;
}) {
  const [filter, setFilter] = useState<HistoryFilter>('all');
  const [limit, setLimit] = useState(PAGE);
  const total = summarizePaperTrades(rows.map(({ t }) => t));
  const { net: totalNet, fees, meanR } = total;
  const shown = rows.filter(({ t }) =>
    filter === 'all'
      ? true
      : filter === 'win'
        ? (t.net_pnl ?? 0) > 0
        : filter === 'loss'
          ? t.net_pnl !== null && t.net_pnl <= 0
          : filter === 'auto'
            ? t.origin === 'auto'
            : t.origin !== 'auto',
  );
  const filtered = summarizePaperTrades(shown.map(({ t }) => t));
  return (
    <>
      {total.closed > 0 && (
        <>
          <p className="text-xs leading-relaxed text-text-muted">
            Thống kê trên {total.closed} lệnh đã chốt trong lịch sử đã tải · tối đa 100 lệnh/cặp
          </p>
          <div className="grid grid-cols-2 gap-2.5 xl:grid-cols-4" aria-label="Tổng kết lịch sử lệnh">
            <div className={`rounded-xl border p-3.5 ${totalNet < 0 ? 'border-bear/20 bg-bear/5' : 'border-bull/20 bg-bull/5'}`}>
              <div className="mb-2 flex items-center justify-between gap-2 text-xs text-text-secondary">PnL ròng <TrendingUp size={15} aria-hidden="true" /></div>
              <strong className={`block font-mono text-lg tabular-nums ${totalNet < 0 ? 'text-bear' : 'text-bull'}`}>
                {totalNet > 0 ? '+' : ''}
                {formatPrice(totalNet)} <span className="text-[10px] font-normal text-text-muted">USDT</span>
              </strong>
              <small className="mt-1 block text-text-muted">Đã trừ phí và funding</small>
            </div>
            <div className="rounded-xl border border-sky-400/20 bg-sky-400/5 p-3.5">
              <div className="mb-2 flex items-center justify-between gap-2 text-xs text-text-secondary">Tỉ lệ thắng <Target size={15} className="text-sky-300" aria-hidden="true" /></div>
              <strong className="block font-mono text-lg tabular-nums text-sky-300">{Math.round((total.wins / total.closed) * 100)}%</strong>
              <small className="mt-1 block text-text-muted">{total.wins}/{total.closed} lệnh đã chốt</small>
            </div>
            <div className="rounded-xl border border-border-base bg-surface-2/50 p-3.5">
              <div className="mb-2 flex items-center justify-between gap-2 text-xs text-text-secondary">R trung bình <Activity size={15} className="text-amber-200" aria-hidden="true" /></div>
              <strong className={`block font-mono text-lg tabular-nums ${meanR == null ? 'text-text-muted' : meanR < 0 ? 'text-bear' : 'text-bull'}`}>
                {meanR == null ? '—' : `${meanR > 0 ? '+' : ''}${meanR.toFixed(2)}R`}
              </strong>
              <small className="mt-1 block text-text-muted">PnL / rủi ro theo SL</small>
            </div>
            <div className="rounded-xl border border-border-base bg-surface-2/50 p-3.5">
              <div className="mb-2 flex items-center justify-between gap-2 text-xs text-text-secondary">Phí + funding <ReceiptText size={15} className="text-amber-200" aria-hidden="true" /></div>
              <strong className="block font-mono text-lg tabular-nums text-text-primary">
                {formatPrice(fees)} <span className="text-[10px] font-normal text-text-muted">USDT</span>
              </strong>
              <small className="mt-1 block text-text-muted">Tổng chi phí đã ghi nhận</small>
            </div>
          </div>
        </>
      )}
      <div className="flex flex-wrap items-center justify-between gap-3 py-2">
      <div className="inline-flex max-w-full flex-wrap gap-1 rounded-xl border border-border-base bg-canvas/60 p-1" role="group" aria-label="Lọc lịch sử lệnh">
        {historyFilters.map(([key, label]) => (
          <button
            key={key}
            type="button"
            aria-pressed={filter === key}
            className={`min-h-9 rounded-lg px-3 text-xs font-semibold transition-colors motion-reduce:transition-none ${filter === key ? 'bg-surface-3 text-text-primary shadow-sm ring-1 ring-border-active' : 'text-text-muted hover:bg-surface-2 hover:text-text-primary'}`}
            onClick={() => {
              setFilter(key);
              setLimit(PAGE);
            }}
          >
            {label}
          </button>
        ))}
      </div>
        <span className="text-xs text-text-muted" role="status">{shown.length} lệnh · Bấm mã lệnh để xem chart</span>
      </div>
      {filter !== 'all' && (
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2 rounded-lg border border-border-subtle bg-surface-2/60 px-3 py-2.5 text-xs text-text-secondary" role="status" aria-live="polite" aria-atomic="true">
          <strong>Đang xem: {historyFilters.find(([key]) => key === filter)?.[1]}</strong>
          <span>{filtered.count} lệnh</span>
          {filtered.count > filtered.closed && (
            <span>
              {filtered.closed} đã chốt · {filtered.count - filtered.closed} đang mở
            </span>
          )}
          {filtered.closed > 0 ? (
            <>
              <span>
                PnL ròng{' '}
                <b className={`font-mono tabular-nums ${filtered.net < 0 ? 'text-bear' : 'text-bull'}`}>
                  {filtered.net > 0 ? '+' : ''}
                  {formatPrice(filtered.net)} USDT
                </b>
              </span>
              <span>
                R TB{' '}
                <b className={`font-mono tabular-nums ${filtered.meanR === null ? 'text-text-muted' : filtered.meanR < 0 ? 'text-bear' : 'text-bull'}`}>
                  {filtered.meanR === null
                    ? '—'
                    : `${filtered.meanR > 0 ? '+' : ''}${filtered.meanR.toFixed(2)}R`}
                </b>
              </span>
              <span>
                Phí + funding <b>{formatPrice(filtered.fees)} USDT</b>
              </span>
              {(filter === 'auto' || filter === 'manual') && (
                <span>
                  Thắng{' '}
                  <b>
                    {Math.round((filtered.wins / filtered.closed) * 100)}% ({filtered.wins}/
                    {filtered.closed})
                  </b>
                </span>
              )}
            </>
          ) : (
            <span>
              {filtered.count ? 'Chưa có lệnh đã chốt để thống kê.' : 'Không có lệnh khớp bộ lọc.'}
            </span>
          )}
        </div>
      )}
      {shown.length > 0 && <div className="min-w-0 max-w-full overflow-x-auto rounded-xl border border-border-base overscroll-x-contain" role="region" aria-label="Danh sách lịch sử lệnh" tabIndex={0}>
        <table className="w-full border-collapse text-xs text-text-secondary lg:whitespace-nowrap">
          <caption className="sr-only">Lịch sử giao dịch, kết quả ròng, chiến thuật và giá khớp lệnh</caption>
          <thead className="sr-only lg:not-sr-only [&_th]:bg-surface-2 [&_th]:py-3 [&_th]:text-[11px]">
            <tr>
              <th scope="col">Lệnh / nguồn</th>
              <th scope="col" className="text-right">PnL ròng · USDT</th>
              <th scope="col">Chiến thuật</th>
              <th scope="col">Giá vào / ra</th>
              <th scope="col">Kết thúc</th>
              <th scope="col">Giá trị · chi phí</th>
            </tr>
          </thead>
          <tbody className="block lg:table-row-group">
            {shown.slice(0, limit).map((row) => {
              const { s, t } = row;
              const lev = t.leverage ?? 1;
              const notional = t.entry * t.quantity;
              const margin = notional / lev;
              const risk = riskOf(t);
              const net = t.net_pnl;
              const unit = short(s);
              return (
                <tr
                  key={`${s}${t.id}`}
                  className={`grid grid-cols-2 border-b border-border-base p-2 last:border-b-0 lg:table-row lg:p-0 [&_td]:min-w-0 [&_td]:border-0 [&_td]:px-2 [&_td]:py-2.5 lg:[&_td]:px-3 lg:[&_td]:py-4 ${selectedId === t.id ? 'bg-bull/10 ring-1 ring-inset ring-bull/25' : 'bg-surface-1 transition-colors hover:bg-surface-2/70'}`}
                >
                  <td>
                    <button type="button" aria-label={`Xem lệnh ${unit} #${t.id} trên chart`} className="mb-2 inline-flex min-h-8 items-center gap-1.5 rounded-md border border-border-base bg-surface-2 px-2 font-mono font-semibold text-text-primary hover:border-bull/50 hover:text-bull" onClick={() => onView(row)}>
                      {showPair ? `${unit} ` : ''}#{t.id}
                      <ChevronRight size={12} aria-hidden="true" />
                    </button>
                    <span className={`ml-2 inline-flex rounded px-1.5 py-0.5 text-[10px] font-bold ${t.side === 'long' ? 'bg-bull/10 text-bull ring-1 ring-bull/20' : 'bg-bear/10 text-bear ring-1 ring-bear/20'}`}>{t.side.toUpperCase()}</span>
                    <small className="block text-[11px] text-text-muted">
                      {t.origin === 'auto' ? 'Auto' : 'Thủ công'} · {fmtTime(t.entry_time)}
                    </small>
                  </td>
                  <td className="text-right font-mono tabular-nums">
                    <strong className={`block text-sm ${net === null ? 'text-amber-200' : net < 0 ? 'text-bear' : 'text-bull'}`}>{net === null ? 'Chưa chốt' : `${net > 0 ? '+' : ''}${formatPrice(net)}`}</strong>
                    <small className="mt-1 block text-[11px] text-text-muted">
                      {net !== null && risk > 0 ? `${(net / risk).toFixed(2)}R` : ''}
                      {net !== null && margin > 0
                        ? ` · ${((net / margin) * 100).toFixed(1)}% ký quỹ`
                        : ''}
                    </small>
                  </td>
                  <td>
                    <span className="mb-1 block text-[10px] text-text-muted lg:hidden">Chiến thuật</span>
                    <StrategyCell trade={t} />
                  </td>
                  <td className="font-mono tabular-nums">
                    <span className="mb-1 block font-sans text-[10px] text-text-muted lg:hidden">Giá vào / ra</span>
                    {formatQuote(t.entry)} → {t.exit === null ? '—' : formatQuote(t.exit)}
                    <small className="mt-1 block text-[11px] text-text-muted">
                      SL <span className="text-bear">{formatQuote(t.stop)}</span> · TP{' '}
                      <span className="text-bull">{formatQuote(t.target)}</span>
                    </small>
                    <small className="mt-1 block text-[11px] text-text-muted">Rủi ro {formatPrice(risk)} USDT</small>
                  </td>
                  <td>
                    <span className="mb-1 block text-[10px] text-text-muted lg:hidden">Kết thúc</span>
                    <span className={`inline-flex rounded-md px-2 py-1 text-[11px] ${t.exit_time ? 'bg-surface-3 text-text-secondary' : 'bg-amber-400/10 text-amber-200'}`}>{t.exit_reason ? (exitLabels[t.exit_reason] ?? t.exit_reason) : 'Đang mở'}</span>
                    <small className="mt-1.5 block text-[11px] text-text-muted">
                      {t.exit_time
                        ? fmtTime(t.exit_time)
                        : ''}
                    </small>
                    {t.exit_time && <small className="mt-1 block text-[11px] text-text-muted">Giữ {span(t.entry_time, t.exit_time)}</small>}
                  </td>
                  <td className="font-mono tabular-nums">
                    <span className="mb-1 block font-sans text-[10px] text-text-muted lg:hidden">Giá trị · chi phí</span>
                    {formatPrice(notional)} USDT
                    <small className="mt-1 block text-[11px] text-text-muted">
                      Ký quỹ {formatPrice(margin)} · {lev}x
                    </small>
                    <small className="mt-1 block text-[11px] text-text-muted">Phí {formatPrice(t.entry_fee + t.exit_fee)} · funding {formatPrice(t.funding_cost)}</small>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>}
      {shown.length > 0 && <div className="flex flex-wrap items-center justify-between gap-3 pt-3">
        <span className="text-xs text-text-muted">Hiển thị <b className="font-mono font-medium text-text-secondary">{Math.min(limit, shown.length)}/{shown.length}</b> lệnh</span>
        {shown.length > limit && (
        <button
          type="button"
          className="inline-flex min-h-10 items-center justify-center gap-2 rounded-lg border border-border-active bg-surface-2 px-4 py-2 text-xs font-semibold text-text-secondary transition-colors hover:bg-surface-3 hover:text-text-primary"
          onClick={() => setLimit((current) => current + PAGE)}
        >
          Xem thêm {Math.min(PAGE, shown.length - limit)} lệnh
          <ChevronDown size={14} aria-hidden="true" />
        </button>
      )}</div>}
      {!shown.length && (
        <div className="grid min-h-48 justify-items-center content-center gap-2 rounded-xl border border-dashed border-border-base bg-surface-2/30 p-6 text-center">
          <ReceiptText size={24} className="mb-1 text-text-muted" aria-hidden="true" />
          <strong className="text-sm text-text-secondary">{rows.length ? 'Không có lệnh khớp bộ lọc' : 'Chưa có lịch sử lệnh'}</strong>
          <p className="text-xs text-text-muted">{rows.length ? 'Thử chọn bộ lọc khác để xem các giao dịch.' : 'Các giao dịch mô phỏng sẽ xuất hiện tại đây.'}</p>
          {filter !== 'all' && <button type="button" className="mt-2 min-h-9 rounded-lg border border-border-base bg-surface-2 px-3 text-xs text-text-primary hover:bg-surface-3" onClick={() => { setFilter('all'); setLimit(PAGE); }}>Xem tất cả lệnh</button>}
        </div>
      )}
    </>
  );
}

const hold = (since: number) => {
  const m = Math.max(0, Math.floor((Date.now() / 1000 - since) / 60));
  return m >= 60 ? `${Math.floor(m / 60)}g ${m % 60}p` : `${m}p`;
};

function PositionPnlDetails({ row }: { row: PositionRow }) {
  const id = `position-pnl-${useId().replace(/:/g, '')}`;
  const [position, setPosition] = useState({ top: 8, left: 8 });
  const { p: trade, s: symbol, mark, feeBps } = row;
  const partial = tradePartialProgress(trade);
  const gross = estimatedPaperPnl(trade, mark, feeBps, false);
  const unit = short(symbol);
  return (
    <>
      <button
        type="button"
        className="position-pnl-detail-button mt-2 inline-flex min-h-8 items-center gap-1 rounded-md border border-border-subtle px-2 text-[11px] text-text-secondary hover:border-border-active hover:text-bull focus-visible:outline-2 focus-visible:outline-sky-400"
        popoverTarget={id}
        aria-haspopup="dialog"
        onClick={(event) => {
          const rect = event.currentTarget.getBoundingClientRect();
          setPosition({
            top: Math.max(8, Math.min(rect.bottom + 8, window.innerHeight - 428)),
            left: Math.max(8, Math.min(rect.left, window.innerWidth - 428)),
          });
        }}
      >
        <ReceiptText size={13} aria-hidden="true" />
        Chi tiết phí & lần chốt
        <ChevronRight size={13} aria-hidden="true" />
      </button>
      <div
        id={id}
        popover="auto"
        role="dialog"
        aria-label={`PnL và các lần chốt ${unit} #${trade.id}`}
        className="tsp position-pnl-popover fixed inset-auto z-[80] m-0 max-h-[70dvh] w-[min(420px,calc(100vw-24px))] overflow-auto whitespace-normal rounded-xl border border-border-active bg-surface-2 p-3 text-xs leading-relaxed text-text-secondary shadow-xl [&_.positive]:text-bull [&_.negative]:text-bear [&_section]:space-y-2 [&_section]:rounded-lg [&_section]:bg-canvas/50 [&_section]:p-3 [&_li]:mt-2 [&_li]:border-t [&_li]:border-border-subtle [&_li]:pt-2 [&_dl]:grid [&_dl]:grid-cols-2 [&_dl]:gap-2 [&_dt]:text-text-muted [&_dd]:m-0 [&_dd]:break-words [&_dd]:text-right [&_dd]:font-mono"
        style={position}
      >
        <header className="tsp-head flex flex-wrap items-center justify-between gap-2 border-b border-border-base py-2">
          <strong className="min-w-0 flex-1 text-text-primary">
            {unit} #{trade.id} · Phí & lần chốt
          </strong>
          <button
            type="button"
            className="tsp-close ml-auto inline-flex size-8 shrink-0 items-center justify-center rounded-md text-lg text-text-muted hover:bg-surface-3 hover:text-text-primary"
            popoverTarget={id}
            popoverTargetAction="hide"
            aria-label="Đóng chi tiết PnL"
          >
            ×
          </button>
        </header>
        <div className="tsp-body space-y-2 py-2">
          <section>
            <strong>
              Phần còn mở · {trade.quantity.toLocaleString('en-US')} {unit}
            </strong>
            <p>
              PnL gộp: {gross === null ? '—' : formatPrice(gross)} USDT.
              <br />
              Phí mở phân bổ: {formatPrice(trade.entry_fee)} USDT.
              <br />
              Funding đã ghi nhận: {formatPrice(trade.funding_cost)} USDT.
            </p>
            <p>
              Số “Còn mở” trên bảng đã trừ các khoản trên và phí đóng dự kiến; chưa chốt vào số dư.
            </p>
          </section>
          {partial ? (
            <section>
              <strong>Đã chốt {partial.count} lần</strong>
              <p>
                Ban đầu: {partial.originalQuantity.toLocaleString('en-US')} {unit}. Tỷ lệ mỗi lần
                tính trên khối lượng ban đầu, không phải phần còn lại.
              </p>
              <ol>
                {trade.monitoring?.partial_exits?.map((leg, index) => {
                  const realized = leg.gross - leg.entry_fee - leg.exit_fee;
                  return (
                    <li key={`${leg.time}-${index}`}>
                      <strong>
                        Lần {index + 1} · {formatTime(leg.time)}
                      </strong>
                      <dl>
                        <dt>Khối lượng đã đóng</dt>
                        <dd>
                          {leg.quantity.toLocaleString('en-US')} {unit} (
                          {((leg.quantity / partial.originalQuantity) * 100).toLocaleString(
                            'vi-VN',
                            { maximumFractionDigits: 1 },
                          )}
                          % ban đầu)
                        </dd>
                        <dt>Giá khớp chốt</dt>
                        <dd>{formatQuote(leg.price)} USDT</dd>
                        <dt>PnL sau phí</dt>
                        <dd className={sign(realized)}>
                          {realized > 0 ? '+' : ''}
                          {formatPrice(realized)} USDT
                        </dd>
                      </dl>
                    </li>
                  );
                })}
              </ol>
              <p>PnL mỗi lần chốt đã trừ phí mở phân bổ và phí chốt; chưa trừ funding.</p>
            </section>
          ) : (
            <p>Chưa có lần chốt một phần.</p>
          )}
        </div>
      </div>
    </>
  );
}

export function PositionsTable({
  rows,
  showPair,
  onView,
  actions,
}: {
  rows: PositionRow[];
  showPair: boolean;
  onView: (row: PositionRow) => void;
  actions?: (row: PositionRow) => React.ReactNode;
}) {
  return (
    <div className="table-scroll min-w-0 max-w-full overflow-x-auto overscroll-x-contain rounded-xl border border-border-subtle focus-visible:outline-2 focus-visible:outline-sky-400" tabIndex={0} role="region" aria-label="Vị thế Paper đang mở, cuộn ngang để xem đầy đủ">
      <table className="positions-table open-positions-table w-full min-w-[1120px] border-collapse text-left text-xs leading-relaxed [&_th]:whitespace-nowrap [&_th]:px-4 [&_th]:py-3 [&_th]:text-[11px] [&_th]:font-medium [&_th]:text-text-muted [&_td]:border-t [&_td]:border-border-subtle [&_td]:px-4 [&_td]:py-4 [&_td]:align-top [&_small]:mt-1 [&_small]:block [&_small]:text-[11px] [&_small]:text-text-muted [&_.positive]:text-bull [&_.negative]:text-bear [&_button]:focus-visible:outline-2 [&_button]:focus-visible:outline-sky-400">
        <caption className="sr-only">Lệnh mô phỏng, PnL phần còn mở, chiến thuật, giá và tiến độ chốt phần. Không phải vị thế trên sàn.</caption>
        <thead className="bg-surface-2/80">
          <tr>
            <th scope="col">Vị thế</th>
            <th scope="col" className="num text-right">PnL ròng dự kiến · USDT</th>
            <th scope="col">Chiến thuật</th>
            <th scope="col">Giá vào / Fair price</th>
            <th scope="col">SL / TP · PnL gộp dự kiến</th>
            <th scope="col">Khối lượng còn mở</th>
            <th scope="col">Thời gian giữ</th>
            {actions && <th scope="col">Thao tác</th>}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => {
            const { s, p, mark, feeBps } = row;
            const net = estimatedPaperPnl(p, mark, feeBps);
            const partial = tradePartialProgress(p);
            const notional = p.entry * p.quantity;
            const lev = p.leverage ?? 1;
            const margin = notional / lev;
            const unit = s.replace('_USDT', '');
            // Signed price move from the current mark to each level, plus PnL of the open size there.
            const levels = (
              [
                ['SL', p.stop],
                ['TP', p.target],
              ] as const
            ).map(([label, price]) => ({
              label,
              price,
              move: mark ? ((price - mark) / mark) * 100 : null,
              pnl: estimatedPaperPnl(p, price, feeBps, false),
            }));
            return (
              <tr key={`${s}${p.id}`} className="transition-colors hover:bg-surface-2/50">
                <td>
                  <button type="button" className="inline-flex min-h-8 items-center gap-1 whitespace-nowrap rounded-md text-xs font-semibold text-text-primary underline-offset-4 hover:text-bull hover:underline" onClick={() => onView(row)} aria-label={`Xem trên chart ${short(s)} #${p.id}`}>
                    {showPair ? `${short(s)} ` : ''}#{p.id}
                  </button>
                  <div className="mt-1 flex flex-wrap items-center gap-1.5"><span className={`side-tag ${p.side} inline-flex shrink-0 items-center rounded-md px-2 py-0.5 text-[11px] font-semibold ${p.side === 'long' ? 'bg-bull/10 text-bull' : 'bg-bear/10 text-bear'}`}>{p.side.toUpperCase()}</span>
                  <span className="rounded-md bg-surface-3 px-1.5 py-0.5 text-[10px] text-text-muted">PAPER</span></div>
                  <small className="trade-origin whitespace-nowrap">
                    {p.origin === 'auto' ? 'Auto' : 'Thủ công'} · {fmtTime(p.entry_time)}
                  </small>
                  {row.stale && <span className="mt-1 inline-flex rounded-md bg-amber-400/10 px-1.5 py-0.5 text-[10px] text-amber-300">Giá có thể đã cũ</span>}
                  {p.monitoring?.status === 'history_pending' && <span className="mt-1 block max-w-40 text-[11px] text-amber-300">Chờ nến đối soát SL/TP</span>}
                </td>
                <td className="num position-pnl-cell text-right tabular-nums">
                  <strong className={`position-pnl-main block whitespace-nowrap font-mono text-base ${net === null ? 'text-text-muted' : sign(net)} font-semibold`}>
                    {net === null ? '—' : `${net > 0 ? '+' : ''}${formatPrice(net)}`}
                  </strong>
                  <small title="ROE = PnL còn mở ÷ ký quỹ của phần còn giữ. Đòn bẩy cao làm ký quỹ nhỏ nên % lớn; margin Cross dùng ví chung, không chỉ phần ký quỹ này.">
                    Còn mở
                    {net !== null && margin > 0
                      ? ` · ROE ${net > 0 ? '+' : ''}${((net / margin) * 100).toFixed(1)}% (${lev}x)`
                      : ''}
                  </small>
                  {partial && (
                    <small>
                      Đã chốt{' '}
                      <b className={`${sign(partial.realizedNet)} font-mono`}>
                        {partial.realizedNet > 0 ? '+' : ''}
                        {formatPrice(partial.realizedNet)}
                      </b>
                    </small>
                  )}
                  <PositionPnlDetails row={row} />
                </td>
                <td className="min-w-48 max-w-60 whitespace-normal break-words">
                  <StrategyCell trade={p} />
                </td>
                <td>
                  <div className="grid grid-cols-[auto_1fr] items-baseline gap-x-3 gap-y-1 whitespace-nowrap font-mono tabular-nums"><span className="text-[10px] text-text-muted">Entry</span><span className="text-right text-text-primary">{formatQuote(p.entry)}</span><span className="text-[10px] text-text-muted">Fair</span><span className="text-right text-text-primary">{mark ? formatQuote(mark) : '—'}</span></div>
                  <small>
                    {mark
                      ? `giá ${mark >= p.entry ? '+' : ''}${(((mark - p.entry) / p.entry) * 100).toFixed(2)}% từ entry`
                      : 'Chưa có fair price'}
                  </small>
                </td>
                <td className="position-levels">
                  <div className="space-y-2">
                  {levels.map(({ label, price, move, pnl }) => (
                    <div key={label} className="position-level grid grid-cols-[auto_1fr] items-baseline gap-x-3 font-mono text-xs tabular-nums">
                      <span className={`position-level-tag ${label.toLowerCase()} rounded px-1 py-0.5 text-[10px] ${label === 'SL' ? 'bg-bear/10 text-bear' : 'bg-bull/10 text-bull'}`}>{label}</span>
                      <span className="text-right text-text-primary">{formatQuote(price)}</span>
                      <small className="whitespace-nowrap" title="Giá phải đi bao nhiêu % từ fair price hiện tại để chạm mức này">
                        {move === null ? '—' : `giá ${move > 0 ? '+' : ''}${move.toFixed(2)}%`}
                      </small>
                      <b
                        className={`whitespace-nowrap text-right text-[11px] ${pnl === null || pnl === 0 ? 'text-text-muted' : sign(pnl)}`}
                        title="PnL phần còn mở nếu khớp đúng mức này · chưa trừ phí, funding, trượt giá"
                      >
                        {pnl === null ? '—' : `${pnl > 0 ? '+' : ''}${formatPrice(pnl)}`}
                      </b>
                    </div>
                  ))}
                  </div>
                </td>
                <td
                  title={`Ký quỹ ${formatPrice(margin)} × ${lev}x = ${formatPrice(notional)} USDT\nKhối lượng = ${formatPrice(notional)} ÷ ${formatQuote(p.entry)} = ${p.quantity} ${unit} (${p.contracts} hợp đồng)\nPhí mở ${formatPrice(p.entry_fee)} USDT · Cross: ví chung chịu rủi ro, không giới hạn ở ký quỹ lệnh`}
                >
                  <span className="whitespace-nowrap font-mono tabular-nums text-text-primary">{p.quantity.toLocaleString('en-US')} {unit}</span>
                  <small>
                    {formatPrice(margin)} × {lev}x · {p.contracts} hợp đồng · Cross
                  </small>
                  {partial ? (
                    <div className="position-partial-progress mt-2 space-y-1.5">
                      <div className="position-partial-caption flex flex-wrap justify-between gap-x-3 gap-y-1 text-[11px] text-text-muted">
                        <button
                          type="button"
                          className="text-button inline-flex items-center gap-1 text-xs text-bull underline-offset-4 hover:underline"
                          onClick={() => onView(row)}
                          aria-label={`Xem ${partial.count} lần chốt của lệnh #${p.id}`}
                        >
                          Đã đóng {partial.closedPercent}%
                        </button>
                        <span>Còn {partial.remainingPercent}%</span>
                      </div>
                      <progress
                        className="block h-1.5 w-full overflow-hidden rounded-full border-0 bg-surface-3 accent-bull [&::-webkit-progress-bar]:rounded-full [&::-webkit-progress-bar]:bg-surface-3 [&::-webkit-progress-value]:rounded-full [&::-webkit-progress-value]:bg-bull [&::-moz-progress-bar]:bg-bull"
                        value={partial.closedPercent}
                        max={100}
                        aria-label={`Đã đóng ${partial.closedPercent}% khối lượng ban đầu`}
                      />
                    </div>
                  ) : (
                    <small>Chưa chốt phần</small>
                  )}
                </td>
                <td className="whitespace-nowrap font-mono text-text-muted">{hold(p.entry_time)}</td>
                {actions && <td><div className="row-actions flex min-w-28 flex-col items-stretch gap-2 [&>button]:min-h-9 [&>button]:whitespace-normal [&>button]:disabled:cursor-not-allowed [&>button]:disabled:opacity-50">{actions(row)}</div></td>}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export function PairStrip({
  board,
  symbol,
  onSelect,
}: {
  board: Board;
  symbol: FuturesSymbol;
  onSelect: (s: FuturesSymbol) => void;
}) {
  return (
    <nav className="pair-strip flex min-w-0 gap-1.5 overflow-x-auto pb-2" aria-label="Chọn cặp Futures">
      {FUTURES_SYMBOLS.map((s) => {
        const w = board[s].wallet;
        const open = w?.positions.length ?? 0;
        const sigs = liveSignals(board[s].auto).length;
        return (
          <button
            key={s}
            type="button"
            className={`inline-flex min-h-8 shrink-0 items-center gap-2 whitespace-nowrap rounded-md border px-2.5 py-1 text-left transition-colors ${s === symbol ? 'border-bull/60 bg-bull/10' : 'border-border-base bg-surface-2/50 hover:border-border-active hover:bg-surface-2'}`}
            aria-pressed={s === symbol}
            onClick={() => onSelect(s)}
            title={`${s.replace('_', ' / ')} · PnL ${w ? formatPrice(w.pair_unrealized_pnl) : '—'} · ⚡ ${sigs} cây có tín hiệu · ${open} vị thế mở`}
          >
            <strong className="pair-sym text-xs font-semibold text-text-primary">{short(s)} <span className="text-[10px] font-normal text-text-muted">/ USDT</span></strong>
            <small className="pair-price font-mono text-xs tabular-nums">{w?.quote ? formatQuote(w.quote.lastPrice) : '—'}</small>
            <span className="pair-indicators flex items-center gap-1 text-xs">
              {sigs > 0 && (
                <span
                  className="pair-signal inline-flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 text-xs font-medium bg-bull/10 text-bull"
                  title={`${sigs} cây có tín hiệu vào ở nến đóng mới nhất`}
                  aria-label={`${sigs} cây có tín hiệu`}
                >
                  <Zap size={10} aria-hidden="true" />
                  {sigs}
                </span>
              )}
              {open > 0 && (
                <span className="pair-badge inline-flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 text-xs font-medium bg-surface-3" title={`${open} vị thế đang mở`}>
                  {open}
                </span>
              )}
              <span
                className={`pair-dot inline-block size-1 shrink-0 rounded-full ${board[s].auto?.enabled && board[s].auto?.worker_online ? 'bg-bull' : 'bg-text-muted/40'}`}
                title={board[s].auto?.enabled ? 'Auto Paper đang chạy' : 'Auto Paper đang tắt'}
              />
            </span>
          </button>
        );
      })}
    </nav>
  );
}

export function AllPairsPanel({
  board,
  onSelect,
  tab,
}: {
  board: Board;
  tab: string;
  onSelect: (s: FuturesSymbol, id?: number | null) => void;
}) {
  const positions: PositionRow[] = FUTURES_SYMBOLS.flatMap((s) =>
    (board[s].wallet?.positions ?? []).map((p) => ({
      s,
      p,
      mark: board[s].wallet?.quote?.fairPrice ?? null,
      feeBps: board[s].wallet?.fee_bps ?? 0,
      stale: !!board[s].wallet?.stale,
    })),
  );
  const trades = FUTURES_SYMBOLS.flatMap((s) =>
    (board[s].wallet?.trades ?? []).filter((t) => t.exit_time !== null).map((t) => ({ s, t })),
  ).sort((a, b) => b.t.id - a.t.id);
  // One shared wallet: every pair reports the same account figures, so read it once.
  const account = FUTURES_SYMBOLS.map((s) => board[s].wallet).find(Boolean) ?? null;
  return (
    <section className="panel prose desk-activity-panel min-w-0 rounded-lg border border-border-base bg-surface-1 p-4 space-y-3 text-sm leading-relaxed text-text-secondary min-w-0">
      <div className="section-head mb-3 flex min-w-0 flex-wrap items-center justify-between gap-2 [&_p]:text-xs [&_p]:text-text-muted">
        <div className="space-y-1">
          <h2>{tab === 'position' ? `Vị thế đang mở (${positions.length})` : tab === 'evolution' ? 'Tiến hóa · Tất cả 10 cặp' : 'Tổng quan 10 cặp'}</h2>
          {tab === 'evolution' && <p>So sánh bộ học theo cặp · R học không phải PnL ví</p>}
        </div>
        <span className="muted text-text-muted">
          Ví chung · Equity {account ? formatPrice(account.equity) : '—'} · PnL chưa chốt{' '}
          <span className={sign(account?.unrealized_pnl ?? 0)}>
            {account ? formatPrice(account.unrealized_pnl) : '—'}
          </span>{' '}
          USDT
        </span>
      </div>
      {tab === 'evolution' && (
        <div className="table-scroll min-w-0 max-w-full overflow-x-auto overscroll-x-contain rounded-lg border border-border-subtle">
          <table className="w-full min-w-[820px] text-xs [&_th]:whitespace-nowrap [&_th]:py-3 [&_td]:align-middle [&_td]:py-3 [&_small]:mt-1 [&_small]:block [&_small]:text-[11px] [&_small]:leading-relaxed">
            <caption className="sr-only">Trạng thái Auto, giao dịch Paper và kết quả học của 10 cặp Futures</caption>
            <thead>
              <tr>
                <th scope="col">Cặp giao dịch</th>
                <th scope="col">Auto · nến 24h</th>
                <th scope="col">Giao dịch Paper</th>
                <th scope="col">Tiến độ học</th>
                <th scope="col">Chiến thuật dẫn đầu</th>
              </tr>
            </thead>
            <tbody>
              {FUTURES_SYMBOLS.map((s) => {
                const { auto: a, learning: l } = board[s];
                const strategies = l?.strategies ?? [];
                const learned = strategies.filter((r) => (r.learned_samples ?? 0) > 0);
                // Same bar as parent selection: below 10 samples a mean R is mostly noise.
                const top = learned
                  .filter((r) => !r.paused && (r.learned_samples ?? 0) >= MIN_TOP_SAMPLES)
                  .sort(
                    (x, y) => (y.learned_mean_r ?? -Infinity) - (x.learned_mean_r ?? -Infinity),
                  )[0];
                const open = strategies.reduce((n, r) => n + (r.open_trades?.length ?? 0), 0);
                const closed = strategies.reduce((n, r) => n + (r.paper?.closed ?? 0), 0);
                const pnl = strategies.reduce((n, r) => n + (r.paper?.net_pnl ?? 0), 0);
                const signals = a?.learner_decision ? liveSignals(a).length : 0;
                return (
                  <tr key={s} className="transition-colors hover:bg-surface-2/60 [&:last-child>td]:border-b-0">
                    <td>
                      <button type="button" className="inline-flex min-h-9 items-center gap-2 whitespace-nowrap rounded-md font-semibold text-text-primary underline-offset-4 hover:text-bull hover:underline" onClick={() => onSelect(s)} aria-label={`Xem ${s.replace('_', ' / ')}`}>
                        {s.replace('_', ' / ')}
                        <ChevronRight size={13} className="text-text-muted" aria-hidden="true" />
                      </button>
                      {signals > 0 && <small className="positive text-bull">⚡ {signals} tín hiệu</small>}
                    </td>
                    <td>
                      <span className={`inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-[11px] font-medium ${!a || !a.enabled ? 'bg-surface-3 text-text-muted' : a.worker_online ? 'bg-bull/10 text-bull' : 'bg-tone-waiting/10 text-tone-waiting'}`}>
                        <span className="size-1.5 rounded-full bg-current" aria-hidden="true" />
                        {!a ? '—' : !a.enabled ? 'Dừng' : a.worker_online ? 'Chạy' : 'Worker off'}
                      </span>
                      {a?.coverage && (
                        <small
                          className={
                            a.enabled && a.coverage.missed + a.coverage.unrecorded
                              ? "negative text-bear"
                              : 'text-text-muted'
                          }
                        >
                          Xét {a.coverage.evaluated}/{a.coverage.tracked_candles}
                          {a.coverage.skipped ? ` · ${a.coverage.skipped} bỏ qua` : ''}
                        </small>
                      )}
                    </td>
                    <td>
                      {open || closed ? (
                        <>
                          {open} đang mở · {closed} đã đóng
                          {closed > 0 && (
                            <small className={sign(pnl)}>
                              {pnl > 0 ? '+' : ''}
                              {formatPrice(pnl)} USDT đã chốt
                            </small>
                          )}
                        </>
                      ) : (
                        <span className="muted text-text-muted">Chưa vào lệnh</span>
                      )}
                    </td>
                    <td>
                      {l ? (
                        l.error ? (
                          <span className="negative text-bear">Lỗi</span>
                        ) : (
                          <>
                            {learned.length}/{l.population} có mẫu học
                            <small className="text-text-muted">
                              {l.promoted} đã duyệt · {l.samples} mẫu forward
                            </small>
                          </>
                        )
                      ) : (
                        '—'
                      )}
                    </td>
                    <td>
                      {top ? (
                        <>
                          {strategyShortTitle(top.genome, top.strategy)}
                          <small className={`font-mono tabular-nums ${sign(top.learned_mean_r ?? 0)}`}>
                            {(top.learned_mean_r ?? 0) > 0 ? '+' : ''}
                            {(top.learned_mean_r ?? 0).toFixed(2)}R · {top.learned_samples} mẫu
                          </small>
                        </>
                      ) : (
                        <span className="block text-text-muted">Chưa đủ mẫu<small>Cần ≥{MIN_TOP_SAMPLES} mẫu / chiến thuật</small></span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      {tab === 'evolution' && <FamilySummary board={board} />}
      {tab === 'pending' && (
        <PaperPendingOrders
          orders={FUTURES_SYMBOLS.flatMap(
            (symbol) => board[symbol].wallet?.pending_orders ?? [],
          ).sort((left, right) => right.id - left.id)}
          onView={(order) => {
            onSelect(order.symbol as FuturesSymbol, order.trade_id ?? -1);
            window.scrollTo({ top: 0, behavior: 'smooth' });
          }}
        />
      )}
      {tab === 'position' && (
        <>
          {positions.length ? (
            <PositionsTable
              showPair
              rows={positions}
              onView={({ s, p }) => onSelect(s as FuturesSymbol, p.id)}
            />
          ) : (
            <div className="flex min-h-36 flex-col items-center justify-center gap-2 rounded-md border border-dashed border-border-base px-4 text-center">
              <strong className="text-sm font-medium text-text-secondary">Chưa có vị thế đang mở</strong>
              <p className="text-xs text-text-muted">Lệnh mô phỏng của tất cả 10 cặp sẽ xuất hiện tại đây.</p>
            </div>
          )}
        </>
      )}
      {tab === 'history' && (
        <>
          <h3>Lịch sử lệnh · tối đa 100 lệnh gần nhất / cặp</h3>
          {trades.length ? (
            <TradeHistoryTable
              showPair
              rows={trades}
              onView={({ s, t }) => onSelect(s as FuturesSymbol, t.id)}
            />
          ) : (
            <p className="muted text-text-muted">Chưa có lệnh đóng.</p>
          )}
        </>
      )}
    </section>
  );
}

function FamilySummary({ board }: { board: Board }) {
  const fam = new Map<
    string,
    { trees: number; learned: number; hold: number; n: number; sumR: number }
  >();
  for (const s of FUTURES_SYMBOLS) {
    for (const r of board[s].learning?.strategies ?? []) {
      const key = strategyShortTitle(r.genome);
      const f = fam.get(key) ?? { trees: 0, learned: 0, hold: 0, n: 0, sumR: 0 };
      f.trees += 1;
      if (r.open_trades?.length) f.hold += 1;
      const n = r.learned_samples ?? 0;
      if (n > 0 && r.learned_mean_r != null) {
        f.learned += 1;
        f.n += n;
        f.sumR += r.learned_mean_r * n;
      }
      fam.set(key, f);
    }
  }
  const rows = [...fam.entries()].sort((a, b) => b[1].hold - a[1].hold || b[1].n - a[1].n);
  if (!rows.length) return null;
  return (
    <details className="family-summary group rounded-lg border border-border-subtle bg-surface-2/30 open:pb-3">
      <summary className="flex min-h-11 list-none items-center gap-2 rounded-lg px-3 text-xs font-medium text-text-secondary hover:bg-surface-2 [&::-webkit-details-marker]:hidden"><ChevronRight size={14} className="shrink-0 transition-transform group-open:rotate-90" aria-hidden="true" />Gộp theo họ chiến thuật<span className="ml-auto rounded bg-surface-3 px-2 py-0.5 text-[11px] text-text-muted">{rows.length} họ · 10 cặp</span></summary>
      <p className="micro px-3 pb-3 text-xs leading-relaxed text-text-muted">
        R học gộp theo số mẫu (replay + forward) của mọi chiến thuật cùng họ. Cùng họ không có nghĩa
        cùng điều kiện; không phải PnL ví.
      </p>
      <div className="table-scroll min-w-0 max-w-full overflow-x-auto overscroll-x-contain">
        <table className="w-full min-w-[640px] text-xs [&_td:not(:first-child)]:text-right [&_td:not(:first-child)]:tabular-nums [&_th:not(:first-child)]:text-right">
          <thead>
            <tr>
              <th>Họ</th>
              <th>Chiến thuật</th>
              <th>Có mẫu học</th>
              <th>Đang giữ lệnh</th>
              <th>Mẫu học</th>
              <th>R học</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(([name, f]) => {
              const r = f.n ? f.sumR / f.n : null;
              return (
                <tr key={name}>
                  <td>
                    <strong>{name}</strong>
                  </td>
                  <td>{f.trees}</td>
                  <td>{f.learned || '—'}</td>
                  <td>{f.hold || '—'}</td>
                  <td>{f.n || '—'}</td>
                  <td className={r === null ? undefined : sign(r)}>
                    {r === null ? '—' : `${r > 0 ? '+' : ''}${r.toFixed(2)}R`}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </details>
  );
}
