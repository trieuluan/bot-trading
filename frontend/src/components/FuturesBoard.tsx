import { useEffect, useId, useState } from 'react';
import { FUTURES_SYMBOLS, type FuturesSymbol } from '../lib/futures-symbol';
import { formatPrice } from '../lib/format';
import { estimatedPaperPnl } from '../lib/paper-position';
import { liveSignals } from '../lib/futures-signals';
import { exitLabels } from './PaperTradeDetails';
import { exitLabel, tradeStrategy, strategyShortTitle, type Genome } from '../lib/futures-strategy-info';

function StrategyCell({ trade }: { trade: PaperTrade }) {
  const info = tradeStrategy(trade.audit);
  const id = `strat-${useId().replace(/:/g, '')}`;
  const [pos, setPos] = useState({ top: 0, left: 0 });
  if (!info) return <>{trade.origin === 'manual' ? 'Thủ công' : 'Auto · thiếu dữ liệu cây'}<small>{trade.entry_reason ?? ''}</small></>;
  return (
    <>
      <span className="strat-title">
        <strong>{info.title}</strong>
        {info.generation && <span className="strat-gen">{info.generation}</span>}
      </span>
      <small className="strat-exit">{trade.monitoring?.exit_policy ? exitLabel(trade.monitoring.exit_policy) : exitLabel(trade.audit?.exit_policy ?? trade.audit?.genome?.exit_policy)}</small>
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
      <div id={id} popover="auto" role="dialog" aria-label={`Chi tiết ${info.title}`} className="tsp" style={pos}>
        <header className="tsp-head">
          <span className="tsp-title">{info.title}</span>
          {info.generation && <span className="tsp-gen">{info.generation}</span>}
          <button type="button" className="tsp-close" popoverTarget={id} popoverTargetAction="hide" aria-label="Đóng chi tiết chiến thuật">×</button>
        </header>
        <div className="tsp-body">
          <div className="tsp-sec"><span className="tsp-label">Chiến thuật</span><span>{info.detail}</span></div>
          {(trade.audit?.reason ?? trade.entry_reason) && (
            <div className="tsp-sec"><span className="tsp-label">Lý do vào lệnh</span><span>{trade.audit?.reason ?? trade.entry_reason}</span></div>
          )}
          <div className="tsp-sec"><span className="tsp-label">Nguồn dữ liệu</span><span>{info.source}</span></div>
        </div>
        <footer className="tsp-foot"><code>{trade.audit?.strategy}</code></footer>
      </div>
    </>
  );
}
import type { AutoPaperState, PaperTrade, PaperWallet } from '../types/paper';

export type PairInfo = {
  wallet: PaperWallet | null;
  auto: AutoPaperState | null;
  learning: { population: number; promoted: number; samples: number; error: string | null;
    strategies: { strategy: string; genome: Genome; resolved: number; mean_r: number | null; paused?: boolean; open_trades?: unknown[] }[];
  } | null;
};
export type Board = Record<FuturesSymbol, PairInfo>;

const empty = Object.fromEntries(
  FUTURES_SYMBOLS.map((s) => [s, { wallet: null, auto: null, learning: null }]),
) as Board;

async function get<T>(path: string, symbol: string): Promise<T | null> {
  try {
    const r = await fetch(path, {
      headers: { 'X-Futures-Symbol': symbol },
      signal: AbortSignal.timeout(8000),
    });
    return r.ok ? ((await r.json()) as T) : null;
  } catch {
    return null;
  }
}

export function useFuturesBoard(active: boolean): Board {
  const [board, setBoard] = useState<Board>(empty);
  useEffect(() => {
    if (!active) return;
    let alive = true;
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      const rows = await Promise.all(
        FUTURES_SYMBOLS.map(async (s) => {
          const [wallet, auto, learning] = await Promise.all([
            get<PaperWallet>('/api/paper/wallet', s),
            get<AutoPaperState>('/api/paper/auto', s),
            get<PairInfo['learning']>('/api/workspaces/mexc_futures/learning', s),
          ]);
          return [s, { wallet, auto, learning }] as const;
        }),
      );
      if (alive) setBoard(Object.fromEntries(rows) as Board);
      if (alive) timer = setTimeout(() => void poll(), 6000);
    }
    void poll();
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

const fmtTime = (t: number) =>
  new Date(t * 1000).toLocaleString('vi-VN', {
    day: '2-digit',
    month: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });
const span = (a: number, b: number) => {
  const m = Math.max(0, Math.round((b - a) / 60));
  return m >= 1440
    ? `${Math.floor(m / 1440)}n ${Math.floor((m % 1440) / 60)}g`
    : m >= 60
      ? `${Math.floor(m / 60)}g ${m % 60}p`
      : `${m}p`;
};

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
  return (
    <div className="table-scroll">
      <table className="positions-table">
        <thead>
          <tr>
            <th>Lệnh</th>
            <th>Chiến thuật</th>
            <th>Khối lượng</th>
            <th>Entry → Exit</th>
            <th>SL / TP</th>
            <th>Kết thúc</th>
            <th>Phí / Funding</th>
            <th>PnL ròng</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => {
            const { s, t } = row;
            const lev = t.leverage ?? 1;
            const notional = t.entry * t.quantity;
            const margin = notional / lev;
            const risk = Math.abs(t.entry - t.stop) * t.quantity;
            const net = t.net_pnl;
            const unit = short(s);
            return (
              <tr key={`${s}${t.id}`} className={selectedId === t.id ? 'selected-trade-row' : undefined}>
                <td>
                  <button className="text-button" onClick={() => onView(row)}>
                    {showPair ? `${unit} ` : ''}#{t.id}
                  </button>
                  <span className={`side-tag ${t.side}`}>{t.side.toUpperCase()}</span>
                  <small className="trade-origin">{t.origin === 'auto' ? 'AUTO' : 'THỦ CÔNG'}</small>
                </td>
                <td>
                  <StrategyCell trade={t} />
                </td>
                <td>
                  {formatPrice(margin)} × {lev}x = {formatPrice(notional)} USDT
                  <small>
                    {t.quantity} {unit} · {t.contracts} hợp đồng · Cross
                  </small>
                </td>
                <td>
                  {formatPrice(t.entry)} → {t.exit === null ? '—' : formatPrice(t.exit)}
                  <small>
                    {fmtTime(t.entry_time)}
                    {t.exit_time ? ` → ${fmtTime(t.exit_time)}` : ' · đang mở'}
                  </small>
                </td>
                <td>
                  <span className="negative">{formatPrice(t.stop)}</span> /{' '}
                  <span className="positive">{formatPrice(t.target)}</span>
                  <small>Rủi ro SL {formatPrice(risk)} USDT</small>
                </td>
                <td>
                  {t.exit_reason ? (exitLabels[t.exit_reason] ?? t.exit_reason) : 'Đang mở'}
                  <small>{t.exit_time ? `Giữ ${span(t.entry_time, t.exit_time)}` : ''}</small>
                </td>
                <td>
                  {formatPrice(t.entry_fee + t.exit_fee)} / {formatPrice(t.funding_cost)}
                  <small>USDT</small>
                </td>
                <td className={net === null ? undefined : sign(net)}>
                  {net === null ? 'Chưa chốt' : `${net > 0 ? '+' : ''}${formatPrice(net)}`}
                  <small>
                    {net !== null && margin > 0 ? `${((net / margin) * 100).toFixed(1)}% ký quỹ` : ''}
                    {net !== null && risk > 0 ? ` · ${(net / risk).toFixed(2)}R` : ''}
                  </small>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

const hold = (since: number) => {
  const m = Math.max(0, Math.floor((Date.now() / 1000 - since) / 60));
  return m >= 60 ? `${Math.floor(m / 60)}g ${m % 60}p` : `${m}p`;
};

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
      <table className="positions-table">
        <thead>
          <tr>
            <th>Lệnh</th>
            <th>Chiến thuật</th>
            <th>Khối lượng</th>
            <th>Entry → Hiện tại</th>
            <th>SL / TP</th>
            <th>PnL (sau phí)</th>
            <th>Giữ</th>
            {actions && <th />}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => {
            const { s, p, mark, feeBps } = row;
            const net = estimatedPaperPnl(p, mark, feeBps);
            const gross = estimatedPaperPnl(p, mark, feeBps, false);
            const toSl = mark ? (Math.abs(mark - p.stop) / mark) * 100 : null;
            const toTp = mark ? (Math.abs(p.target - mark) / mark) * 100 : null;
            const notional = p.entry * p.quantity;
            const lev = p.leverage ?? 1;
            const margin = notional / lev;
            const slLoss = Math.abs(p.entry - p.stop) * p.quantity;
            const unit = s.replace('_USDT', '');
            return (
              <tr key={`${s}${p.id}`}>
                <td>
                  <button className="text-button" onClick={() => onView(row)}>
                    {showPair ? `${short(s)} ` : ''}#{p.id}
                  </button>
                  <span className={`side-tag ${p.side}`}>{p.side.toUpperCase()}</span>
                  <small className="trade-origin">
                    {p.origin === 'auto' ? 'AUTO' : 'THỦ CÔNG'}
                  </small>
                </td>
                <td>
                  <StrategyCell trade={p} />
                </td>
                <td
                  title={`Ký quỹ ${formatPrice(margin)} × ${lev}x = ${formatPrice(notional)} USDT\nKhối lượng = ${formatPrice(notional)} ÷ ${formatPrice(p.entry)} = ${p.quantity} ${unit} (${p.contracts} hợp đồng)\nPhí mở ${formatPrice(p.entry_fee)} USDT · Cross: ví chung chịu rủi ro, không giới hạn ở ký quỹ lệnh`}
                >
                  {formatPrice(margin)} × {lev}x = {formatPrice(notional)} USDT
                  <small>
                    {p.quantity} {unit} · {p.contracts} hợp đồng · Cross
                  </small>
                  <small className="negative">Tới SL ≈ {formatPrice(slLoss)} USDT (chưa phí/funding)</small>
                </td>
                <td>
                  {formatPrice(p.entry)} → {mark ? formatPrice(mark) : '—'}
                  <small>{new Date(p.entry_time * 1000).toLocaleString('vi-VN')}</small>
                </td>
                <td>
                  <span className="negative">
                    {formatPrice(p.stop)} {toSl !== null && `(${toSl.toFixed(2)}%)`}
                  </span>
                  <small className="positive">
                    {formatPrice(p.target)} {toTp !== null && `(${toTp.toFixed(2)}%)`}
                  </small>
                </td>
                <td className={net === null ? undefined : sign(net)}>
                  <strong>{net === null ? '—' : formatPrice(net)}</strong>
                  <small>
                    Gộp {gross === null ? '—' : formatPrice(gross)} · Phí {formatPrice(p.entry_fee)}
                  </small>
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
            title={`${s.replace('_', ' / ')} · PnL ${w ? formatPrice(w.pair_unrealized_pnl) : '—'} · ${sigs} cây có tín hiệu`}
          >
            <strong>{short(s)}</strong>
            <small>{w?.quote ? formatPrice(w.quote.lastPrice) : '—'}</small>
            {open > 0 && <span className="pair-badge">{open}</span>}
            {sigs > 0 && <span className="pair-signal" aria-label={`${sigs} cây có tín hiệu mới`}>S {sigs}</span>}
            <span
              className={`pair-dot ${board[s].auto?.enabled && board[s].auto?.worker_online ? 'on' : ''}`}
            />
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
  ).sort((a, b) => (b.t.exit_time ?? 0) - (a.t.exit_time ?? 0));
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
                <th>Giá</th>
                <th>PnL chưa chốt</th>
                <th>Vị thế</th>
                <th>Auto</th>
                <th>Lệnh hôm nay</th>
                <th>Tín hiệu</th>
                <th>Cây giữ lệnh</th>
                <th>Top cây · Mean R</th>
                <th>Bộ học</th>
              </tr>
            </thead>
            <tbody>
              {FUTURES_SYMBOLS.map((s) => {
                const { wallet: w, auto: a, learning: l } = board[s];
                const top = [...(l?.strategies ?? [])].filter((r) => !r.paused && r.mean_r !== null && r.resolved > 0)
                  .sort((first, second) => second.mean_r! - first.mean_r!)[0];
                return (
                  <tr key={s}>
                    <td>
                      <button className="text-button" onClick={() => onSelect(s)}>
                        {s.replace('_', ' / ')}
                      </button>
                    </td>
                    <td>{w?.quote ? formatPrice(w.quote.lastPrice) : '—'}</td>
                    <td className={w ? sign(w.pair_unrealized_pnl) : undefined}>
                      {w ? formatPrice(w.pair_unrealized_pnl) : '—'}
                    </td>
                    <td>{w?.positions.length ?? '—'}</td>
                    <td>
                      <span className={`pill ${a?.enabled && a.worker_online ? 'green' : 'amber'}`}>
                        {!a ? '—' : !a.enabled ? 'Dừng' : a.worker_online ? 'Chạy' : 'Worker off'}
                      </span>
                    </td>
                    <td>{a ? `${a.daily_trades}/${a.max_daily_trades}` : '—'}</td>
                    <td>{a?.learner_decision ? liveSignals(a).length : '—'}</td>
                    <td>{l ? l.strategies.filter((r) => r.open_trades?.length).length : '—'}</td>
                    <td>{top ? <>{strategyShortTitle(top.genome, top.strategy)}<small>{top.mean_r!.toFixed(2)}R · {top.resolved} lệnh đã xét</small></> : 'Chưa có mẫu'}</td>
                    <td>
                      {l
                        ? l.error
                          ? 'Lỗi'
                          : `${l.population} biến thể · ${l.promoted} duyệt · ${l.samples} mẫu`
                        : '—'}
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
            <TradeHistoryTable showPair rows={trades} onView={({ s, t }) => onSelect(s as FuturesSymbol, t.id)} />
          ) : (
            <p className="muted">Chưa có lệnh đóng.</p>
          )}
        </>
      )}
      {tab === 'decisions' && (
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>Cặp</th>
                <th>Nến tín hiệu</th>
                <th>Quyết định</th>
                <th>Lý do</th>
                <th>Lệnh</th>
              </tr>
            </thead>
            <tbody>
              {FUTURES_SYMBOLS.flatMap((s) =>
                (board[s].auto?.decisions ?? []).map((d) => ({ s, d })),
              )
                .sort((a, b) => b.d.candle_time - a.d.candle_time)
                .map(({ s, d }) => (
                  <tr key={`${s}${d.candle_time}`}>
                    <td>
                      <button className="text-button" onClick={() => onSelect(s)}>
                        {short(s)}
                      </button>
                    </td>
                    <td>{new Date(d.candle_time * 1000).toLocaleString('vi-VN')}</td>
                    <td>
                      {d.action === 'opened'
                        ? 'Đã mở'
                        : d.action === 'skipped'
                          ? 'Bỏ qua'
                          : 'Không có setup'}
                    </td>
                    <td>{d.reason}</td>
                    <td>
                      {d.trade_id ? (
                        <button className="text-button" onClick={() => onSelect(s, d.trade_id)}>
                          #{d.trade_id} · Chart
                        </button>
                      ) : (
                        '—'
                      )}
                    </td>
                  </tr>
                ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function FamilySummary({ board }: { board: Board }) {
  const fam = new Map<string, { pairs: Set<string>; trees: number; sig: number; hold: number; n: number; sumR: number }>();
  for (const s of FUTURES_SYMBOLS) {
    const sigNames = new Set(liveSignals(board[s].auto).map((r) => r.strategy));
    for (const r of board[s].learning?.strategies ?? []) {
      const key = strategyShortTitle(r.genome);
      const f = fam.get(key) ?? { pairs: new Set(), trees: 0, sig: 0, hold: 0, n: 0, sumR: 0 };
      f.pairs.add(s);
      f.trees += 1;
      if (sigNames.has(r.strategy)) f.sig += 1;
      if (r.open_trades?.length) f.hold += 1;
      if (r.mean_r !== null && r.resolved > 0) {
        f.n += r.resolved;
        f.sumR += r.mean_r * r.resolved;
      }
      fam.set(key, f);
    }
  }
  const rows = [...fam.entries()].sort((a, b) => b[1].sig + b[1].hold - (a[1].sig + a[1].hold) || b[1].n - a[1].n);
  if (!rows.length) return null;
  return (
    <>
      <h3>Gộp theo họ · 10 cặp</h3>
      <p className="micro">Cùng họ không đồng nghĩa cùng điều kiện cây. Mean R gộp theo số lệnh đã xét của cây trong danh mục, không phải PnL ví hay bảo đảm hiệu quả. Top cây chưa áp ngưỡng số mẫu.</p>
      <div className="table-scroll">
        <table>
          <thead>
            <tr><th>Họ</th><th>Số cặp</th><th>Cây</th><th>Có tín hiệu</th><th>Đang giữ</th><th>Lệnh đã xét</th><th>Mean R</th></tr>
          </thead>
          <tbody>
            {rows.map(([name, f]) => {
              const r = f.n ? f.sumR / f.n : null;
              return (
                <tr key={name}>
                  <td><strong>{name}</strong></td>
                  <td>{f.pairs.size}/10</td>
                  <td>{f.trees}</td>
                  <td>{f.sig || '—'}</td>
                  <td>{f.hold || '—'}</td>
                  <td>{f.n}</td>
                  <td className={r === null ? undefined : sign(r)}>{r === null ? '—' : `${r > 0 ? '+' : ''}${r.toFixed(2)}R`}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </>
  );
}
