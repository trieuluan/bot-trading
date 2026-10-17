import { useEffect, useState } from 'react';
import { FUTURES_SYMBOLS, type FuturesSymbol } from '../lib/futures-symbol';
import { formatPrice } from '../lib/format';
import { estimatedPaperPnl } from '../lib/paper-position';
import type { AutoPaperState, PaperTrade, PaperWallet } from '../types/paper';

export type PairInfo = {
  wallet: PaperWallet | null;
  auto: AutoPaperState | null;
  learning: { population: number; promoted: number; samples: number; error: string | null } | null;
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
                <td title={p.audit?.reason}>
                  {p.audit?.strategy
                    ? p.audit.strategy.replace(/_/g, ' ').replace(':', ' · ')
                    : 'Thủ công'}
                  <small>{p.audit?.reason ?? p.entry_reason ?? ''}</small>
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
        return (
          <button
            key={s}
            type="button"
            aria-pressed={s === symbol}
            onClick={() => onSelect(s)}
            title={`${s.replace('_', ' / ')} · PnL ${w ? formatPrice(w.pair_unrealized_pnl) : '—'}`}
          >
            <strong>{short(s)}</strong>
            <small>{w?.quote ? formatPrice(w.quote.lastPrice) : '—'}</small>
            {open > 0 && <span className="pair-badge">{open}</span>}
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
                <th>Bộ học</th>
              </tr>
            </thead>
            <tbody>
              {FUTURES_SYMBOLS.map((s) => {
                const { wallet: w, auto: a, learning: l } = board[s];
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
            <div className="table-scroll">
              <table>
                <tbody>
                  {trades.map(({ s, t }) => (
                    <tr key={`${s}${t.id}`}>
                      <td>
                        <button className="text-button" onClick={() => onSelect(s, t.id)}>
                          {short(s)} #{t.id}
                        </button>
                      </td>
                      <td>{t.side.toUpperCase()}</td>
                      <td>{t.audit?.strategy ?? 'Thủ công'}</td>
                      <td>
                        {formatPrice(t.entry)} → {t.exit === null ? '—' : formatPrice(t.exit)}
                      </td>
                      <td className={sign(t.net_pnl ?? 0)}>{formatPrice(t.net_pnl ?? 0)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
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
