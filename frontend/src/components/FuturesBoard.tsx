import { useEffect, useId, useState } from 'react';
import { ChevronRight, ReceiptText, Zap } from 'lucide-react';
import { FUTURES_SYMBOLS, type FuturesSymbol } from '../lib/futures-symbol';
import { formatPrice, formatQuote } from '../lib/format';
import {
  estimatedPaperPnl,
  summarizePaperTrades,
  tradePartialProgress,
} from '../lib/paper-position';
import { liveSignals } from '../lib/futures-signals';
import { exitLabels } from './PaperTradeDetails';
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
      <span className="strat-title">
        <strong>{info.title}</strong>
        {info.generation && <span className="strat-gen">{info.generation}</span>}
      </span>
      <small className="strat-exit">
        {trade.monitoring?.exit_policy
          ? exitLabel(trade.monitoring.exit_policy)
          : exitLabel(trade.audit?.exit_policy ?? trade.audit?.genome?.exit_policy)}
      </small>
      <button
        type="button"
        className="text-button strategy-detail-btn"
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
        className="tsp"
        style={pos}
      >
        <header className="tsp-head">
          <span className="tsp-title">{info.title}</span>
          {info.generation && <span className="tsp-gen">{info.generation}</span>}
          <button
            type="button"
            className="tsp-close"
            popoverTarget={id}
            popoverTargetAction="hide"
            aria-label="Đóng chi tiết chiến thuật"
          >
            ×
          </button>
        </header>
        <div className="tsp-body">
          <div className="tsp-sec">
            <span className="tsp-label">Chiến thuật</span>
            <span>{info.detail}</span>
          </div>
          {(trade.audit?.reason ?? trade.entry_reason) && (
            <div className="tsp-sec">
              <span className="tsp-label">Lý do vào lệnh</span>
              <span>{trade.audit?.reason ?? trade.entry_reason}</span>
            </div>
          )}
          <div className="tsp-sec">
            <span className="tsp-label">Nguồn dữ liệu</span>
            <span>{info.source}</span>
          </div>
        </div>
        <footer className="tsp-foot">
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
    let alive = true;
    let timer: ReturnType<typeof setTimeout>;
    // Một request /board cho cả 10 cặp (trước đây 3 x 10 request song song làm nghẽn API
    // khiến dashboard chính báo "mất kết nối" vài giây khi mở trang).
    async function poll() {
      try {
        const response = await fetch('/api/workspaces/mexc_futures/board', {
          signal: AbortSignal.timeout(10000),
        });
        if (response.ok) {
          const data = (await response.json()) as { symbols: Partial<Board> };
          if (alive)
            setBoard(
              Object.fromEntries(
                FUTURES_SYMBOLS.map((s) => [
                  s,
                  data.symbols[s] ?? { wallet: null, auto: null, learning: null },
                ]),
              ) as Board,
            );
        }
      } catch {
        // Giữ bảng cũ; lần poll sau thử lại.
      }
      if (alive) timer = setTimeout(() => void poll(), 6000);
    }
    // Nhường request dashboard/chart chạy trước khi tải bảng tổng hợp.
    timer = setTimeout(() => void poll(), 400);
    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, [active]);
  return board;
}

const sign = (v: number) => (v < 0 ? 'negative' : 'positive');
const short = (s: string) => s.replace('_USDT', '');

export type PositionRow = { s: string; p: PaperTrade; mark: number | null; feeBps: number };

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
          <p className="micro">
            Tổng lịch sử đang tải · {total.closed} lệnh đã chốt · tối đa 100 lệnh/cặp
          </p>
          <div className="history-summary" aria-label="Tổng kết lịch sử lệnh">
            <div>
              <small>PnL ròng</small>
              <strong className={sign(totalNet)}>
                {totalNet > 0 ? '+' : ''}
                {formatPrice(totalNet)} <em>USDT</em>
              </strong>
            </div>
            <div>
              <small>Tỉ lệ thắng</small>
              <strong>
                {Math.round((total.wins / total.closed) * 100)}%
                <em>
                  {' '}
                  {total.wins}/{total.closed}
                </em>
              </strong>
            </div>
            <div>
              <small>R trung bình</small>
              <strong className={meanR == null ? '' : sign(meanR)}>
                {meanR == null ? '—' : `${meanR > 0 ? '+' : ''}${meanR.toFixed(2)}R`}
              </strong>
            </div>
            <div>
              <small>Phí + funding</small>
              <strong>
                {formatPrice(fees)} <em>USDT</em>
              </strong>
            </div>
          </div>
        </>
      )}
      <div className="history-filters" role="group" aria-label="Lọc lịch sử lệnh">
        {historyFilters.map(([key, label]) => (
          <button
            key={key}
            type="button"
            aria-pressed={filter === key}
            onClick={() => {
              setFilter(key);
              setLimit(PAGE);
            }}
          >
            {label}
          </button>
        ))}
        <span className="muted">{shown.length} lệnh · bấm số lệnh để xem trên chart</span>
      </div>
      {filter !== 'all' && (
        <div className="history-filter-summary" role="status" aria-live="polite" aria-atomic="true">
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
                <b className={sign(filtered.net)}>
                  {filtered.net > 0 ? '+' : ''}
                  {formatPrice(filtered.net)} USDT
                </b>
              </span>
              <span>
                R TB{' '}
                <b className={filtered.meanR === null ? '' : sign(filtered.meanR)}>
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
      <div className="table-scroll">
        <table className="positions-table history-table">
          <thead>
            <tr>
              <th>Lệnh</th>
              <th className="num">PnL ròng</th>
              <th>Chiến thuật</th>
              <th>Entry → Exit · SL / TP</th>
              <th>Kết thúc</th>
              <th>Khối lượng · phí</th>
            </tr>
          </thead>
          <tbody>
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
                  className={selectedId === t.id ? 'selected-trade-row' : undefined}
                >
                  <td>
                    <button className="text-button" onClick={() => onView(row)}>
                      {showPair ? `${unit} ` : ''}#{t.id}
                    </button>
                    <span className={`side-tag ${t.side}`}>{t.side.toUpperCase()}</span>
                    <small className="trade-origin">
                      {t.origin === 'auto' ? 'Auto' : 'Thủ công'} · {fmtTime(t.entry_time)}
                    </small>
                  </td>
                  <td className={`num history-pnl ${net === null ? '' : sign(net)}`}>
                    {net === null ? 'Chưa chốt' : `${net > 0 ? '+' : ''}${formatPrice(net)}`}
                    <small>
                      {net !== null && risk > 0 ? `${(net / risk).toFixed(2)}R` : ''}
                      {net !== null && margin > 0
                        ? ` · ${((net / margin) * 100).toFixed(1)}% ký quỹ`
                        : ''}
                    </small>
                  </td>
                  <td>
                    <StrategyCell trade={t} />
                  </td>
                  <td>
                    {formatQuote(t.entry)} → {t.exit === null ? '—' : formatQuote(t.exit)}
                    <small>
                      SL <span className="negative">{formatQuote(t.stop)}</span> · TP{' '}
                      <span className="positive">{formatQuote(t.target)}</span> · rủi ro{' '}
                      {formatPrice(risk)}
                    </small>
                  </td>
                  <td>
                    {t.exit_reason ? (exitLabels[t.exit_reason] ?? t.exit_reason) : 'Đang mở'}
                    <small>
                      {t.exit_time
                        ? `${fmtTime(t.exit_time)} · giữ ${span(t.entry_time, t.exit_time)}`
                        : ''}
                    </small>
                  </td>
                  <td>
                    {formatPrice(notional)} USDT
                    <small>
                      {formatPrice(margin)} × {lev}x · phí {formatPrice(t.entry_fee + t.exit_fee)} ·
                      funding {formatPrice(t.funding_cost)}
                    </small>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {shown.length > limit && (
        <button
          type="button"
          className="secondary history-more"
          onClick={() => setLimit(limit + PAGE)}
        >
          Xem thêm {Math.min(PAGE, shown.length - limit)} lệnh · còn {shown.length - limit}
        </button>
      )}
      {rows.length > 0 && !shown.length && (
        <p className="muted history-empty">Không có lệnh khớp bộ lọc.</p>
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
        className="position-pnl-detail-button"
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
        className="tsp position-pnl-popover"
        style={position}
      >
        <header className="tsp-head">
          <strong className="tsp-title">
            {unit} #{trade.id} · Phí & lần chốt
          </strong>
          <button
            type="button"
            className="tsp-close"
            popoverTarget={id}
            popoverTargetAction="hide"
            aria-label="Đóng chi tiết PnL"
          >
            ×
          </button>
        </header>
        <div className="tsp-body">
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
    <div className="table-scroll">
      <table className="positions-table open-positions-table">
        <thead>
          <tr>
            <th>Lệnh</th>
            <th className="num">PnL · USDT</th>
            <th>Chiến thuật</th>
            <th>Entry → Hiện tại</th>
            <th>SL / TP · nếu khớp</th>
            <th>Khối lượng</th>
            <th>Giữ</th>
            {actions && <th />}
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
              <tr key={`${s}${p.id}`}>
                <td>
                  <button className="text-button" onClick={() => onView(row)}>
                    {showPair ? `${short(s)} ` : ''}#{p.id}
                  </button>
                  <span className={`side-tag ${p.side}`}>{p.side.toUpperCase()}</span>
                  <small className="trade-origin">
                    {p.origin === 'auto' ? 'Auto' : 'Thủ công'} · {fmtTime(p.entry_time)}
                  </small>
                </td>
                <td className="num position-pnl-cell">
                  <strong className={`position-pnl-main ${net === null ? '' : sign(net)}`}>
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
                      <b className={sign(partial.realizedNet)}>
                        {partial.realizedNet > 0 ? '+' : ''}
                        {formatPrice(partial.realizedNet)}
                      </b>
                    </small>
                  )}
                  <PositionPnlDetails row={row} />
                </td>
                <td>
                  <StrategyCell trade={p} />
                </td>
                <td>
                  {formatQuote(p.entry)} → {mark ? formatQuote(mark) : '—'}
                  <small>
                    {mark
                      ? `giá ${mark >= p.entry ? '+' : ''}${(((mark - p.entry) / p.entry) * 100).toFixed(2)}% từ entry`
                      : 'Chưa có fair price'}
                  </small>
                </td>
                <td className="position-levels">
                  {levels.map(({ label, price, move, pnl }) => (
                    <div key={label} className="position-level">
                      <span className={`position-level-tag ${label.toLowerCase()}`}>{label}</span>
                      <span>{formatQuote(price)}</span>
                      <small title="Giá phải đi bao nhiêu % từ fair price hiện tại để chạm mức này">
                        {move === null ? '—' : `giá ${move > 0 ? '+' : ''}${move.toFixed(2)}%`}
                      </small>
                      <b
                        className={pnl === null || pnl === 0 ? undefined : sign(pnl)}
                        title="PnL phần còn mở nếu khớp đúng mức này · chưa trừ phí, funding, trượt giá"
                      >
                        {pnl === null ? '—' : `${pnl > 0 ? '+' : ''}${formatPrice(pnl)}`}
                      </b>
                    </div>
                  ))}
                </td>
                <td
                  title={`Ký quỹ ${formatPrice(margin)} × ${lev}x = ${formatPrice(notional)} USDT\nKhối lượng = ${formatPrice(notional)} ÷ ${formatQuote(p.entry)} = ${p.quantity} ${unit} (${p.contracts} hợp đồng)\nPhí mở ${formatPrice(p.entry_fee)} USDT · Cross: ví chung chịu rủi ro, không giới hạn ở ký quỹ lệnh`}
                >
                  {p.quantity.toLocaleString('en-US')} {unit}
                  <small>
                    {formatPrice(margin)} × {lev}x · {p.contracts} hợp đồng · Cross
                  </small>
                  {partial ? (
                    <div className="position-partial-progress">
                      <div className="position-partial-caption">
                        <button
                          type="button"
                          className="text-button"
                          onClick={() => onView(row)}
                          aria-label={`Xem ${partial.count} lần chốt của lệnh #${p.id}`}
                        >
                          Đã đóng {partial.closedPercent}%
                        </button>
                        <span>Còn {partial.remainingPercent}%</span>
                      </div>
                      <progress
                        value={partial.closedPercent}
                        max={100}
                        aria-label={`Đã đóng ${partial.closedPercent}% khối lượng ban đầu`}
                      />
                    </div>
                  ) : (
                    <small>Chưa chốt phần</small>
                  )}
                </td>
                <td>{hold(p.entry_time)}</td>
                {actions && <td className="row-actions">{actions(row)}</td>}
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
    <nav className="pair-strip" aria-label="Chọn cặp Futures">
      {FUTURES_SYMBOLS.map((s) => {
        const w = board[s].wallet;
        const open = w?.positions.length ?? 0;
        const sigs = liveSignals(board[s].auto).length;
        return (
          <button
            key={s}
            type="button"
            aria-pressed={s === symbol}
            onClick={() => onSelect(s)}
            title={`${s.replace('_', ' / ')} · PnL ${w ? formatPrice(w.pair_unrealized_pnl) : '—'} · ⚡ ${sigs} cây có tín hiệu · ${open} vị thế mở`}
          >
            <strong className="pair-sym">{short(s)}</strong>
            <small className="pair-price">{w?.quote ? formatQuote(w.quote.lastPrice) : '—'}</small>
            <span className="pair-indicators">
              {sigs > 0 && (
                <span
                  className="pair-signal"
                  title={`${sigs} cây có tín hiệu vào ở nến đóng mới nhất`}
                  aria-label={`${sigs} cây có tín hiệu`}
                >
                  <Zap size={10} aria-hidden="true" />
                  {sigs}
                </span>
              )}
              {open > 0 && (
                <span className="pair-badge" title={`${open} vị thế đang mở`}>
                  {open}
                </span>
              )}
              <span
                className={`pair-dot ${board[s].auto?.enabled && board[s].auto?.worker_online ? 'on' : ''}`}
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
    })),
  );
  const trades = FUTURES_SYMBOLS.flatMap((s) =>
    (board[s].wallet?.trades ?? []).filter((t) => t.exit_time !== null).map((t) => ({ s, t })),
  ).sort((a, b) => b.t.id - a.t.id);
  // One shared wallet: every pair reports the same account figures, so read it once.
  const account = FUTURES_SYMBOLS.map((s) => board[s].wallet).find(Boolean) ?? null;
  return (
    <section className="panel prose desk-activity-panel">
      <div className="section-head">
        <h2>Tổng quan 10 cặp</h2>
        <span className="muted">
          Ví chung · Equity {account ? formatPrice(account.equity) : '—'} · PnL chưa chốt{' '}
          <span className={sign(account?.unrealized_pnl ?? 0)}>
            {account ? formatPrice(account.unrealized_pnl) : '—'}
          </span>{' '}
          USDT
        </span>
      </div>
      {tab === 'evolution' && (
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>Cặp</th>
                <th>Auto · nến 24h</th>
                <th>Auto Paper</th>
                <th>Bộ học</th>
                <th>Top chiến thuật · R học</th>
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
                  <tr key={s}>
                    <td>
                      <button className="text-button" onClick={() => onSelect(s)}>
                        {s.replace('_', ' / ')}
                      </button>
                      {signals > 0 && <small className="positive">⚡ {signals} tín hiệu</small>}
                    </td>
                    <td>
                      <span className={`pill ${a?.enabled && a.worker_online ? 'green' : 'amber'}`}>
                        {!a ? '—' : !a.enabled ? 'Dừng' : a.worker_online ? 'Chạy' : 'Worker off'}
                      </span>
                      {a?.coverage && (
                        <small
                          className={
                            a.enabled && a.coverage.missed + a.coverage.unrecorded
                              ? 'negative'
                              : undefined
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
                        <span className="muted">Chưa vào lệnh</span>
                      )}
                    </td>
                    <td>
                      {l ? (
                        l.error ? (
                          <span className="negative">Lỗi</span>
                        ) : (
                          <>
                            {learned.length}/{l.population} có mẫu học
                            <small>
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
                          <small className={sign(top.learned_mean_r ?? 0)}>
                            {(top.learned_mean_r ?? 0) > 0 ? '+' : ''}
                            {(top.learned_mean_r ?? 0).toFixed(2)}R · {top.learned_samples} mẫu
                          </small>
                        </>
                      ) : (
                        <span className="muted">Chưa có chiến thuật ≥{MIN_TOP_SAMPLES} mẫu</span>
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
      {tab === 'position' && (
        <>
          <h3>Vị thế đang mở ({positions.length})</h3>
          {positions.length ? (
            <PositionsTable
              showPair
              rows={positions}
              onView={({ s, p }) => onSelect(s as FuturesSymbol, p.id)}
            />
          ) : (
            <p className="muted">Không có vị thế mở.</p>
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
            <p className="muted">Chưa có lệnh đóng.</p>
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
    <details className="family-summary">
      <summary>Gộp theo họ chiến thuật · cả 10 cặp ({rows.length} họ)</summary>
      <p className="micro">
        R học gộp theo số mẫu (replay + forward) của mọi chiến thuật cùng họ. Cùng họ không có nghĩa
        cùng điều kiện; không phải PnL ví.
      </p>
      <div className="table-scroll">
        <table>
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
