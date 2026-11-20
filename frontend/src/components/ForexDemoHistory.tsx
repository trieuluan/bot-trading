import { useEffect, useState } from 'react';
import { ChevronLeft, ChevronRight } from 'lucide-react';
import { formatForexPrice } from '../lib/format';
import { formatTime } from '../lib/time-format';
import type { PositionGroup } from '../lib/forex-position-groups';

type History = {
  symbol: string;
  account_suffix: string;
  synced_at: number | null;
  total: number;
  broker_profit: number | null;
  profit_count: number;
  groups: PositionGroup[];
  has_more: boolean;
};
type Result = { key: string; history: History } | { key: string; error: string };

const limit = 25;
const number = new Intl.NumberFormat('vi-VN', { maximumFractionDigits: 4 });
const usd = new Intl.NumberFormat('vi-VN', { minimumFractionDigits: 2, maximumFractionDigits: 2, signDisplay: 'exceptZero' });
const action = 'inline-flex min-h-10 items-center justify-center gap-1 rounded-md border border-border-base bg-surface-2 px-3 text-xs font-semibold text-text-secondary hover:bg-surface-3 focus-visible:outline-2 focus-visible:outline-bull disabled:opacity-40';

export function ForexDemoHistory({ symbol }: { symbol: string }) {
  const [offset, setOffset] = useState(0);
  const [attempt, setAttempt] = useState(0);
  const [result, setResult] = useState<Result>();
  const key = `${symbol}:${offset}:${attempt}`;
  const current = result?.key === key ? result : undefined;
  const history = current && 'history' in current ? current.history : undefined;
  useEffect(() => {
    const controller = new AbortController();
    const params = new URLSearchParams({ symbol, offset: String(offset), limit: String(limit) });
    fetch(`/api/workspaces/exness_forex/execution/history/demo?${params}`, { signal: controller.signal })
      .then(async (response) => {
        if (response.status === 404) throw new Error('Tài khoản demo chưa có lịch sử được xác minh.');
        if (!response.ok) throw new Error('Không tải được lịch sử Demo.');
        const history: History = await response.json();
        if (history.symbol !== symbol) throw new Error('Dữ liệu trả về sai mã giao dịch.');
        if (!controller.signal.aborted) setResult({ key, history });
      })
      .catch((error: Error) => {
        if (!controller.signal.aborted) setResult({ key, error: error.message });
      });
    return () => controller.abort();
  }, [key, offset, symbol]);

  const pnl = history?.broker_profit ?? null;
  return (
    <section className="min-w-0 bg-canvas" aria-labelledby="demo-history-title" aria-busy={!current}>
      <header className="border-b border-border-base bg-surface-1 px-4 py-4 sm:px-6">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
          <h2 id="demo-history-title" className="text-base font-bold text-sky-200">Lịch sử Demo</h2>
          <span className="rounded border border-sky-400/25 bg-sky-400/10 px-2 py-0.5 text-xs font-semibold text-sky-200">Chỉ xem</span>
        </div>
        <p className="mt-1 text-xs text-text-muted">Lệnh đã đóng trên Exness Demo{history ? ` · TK …${history.account_suffix}` : ''} · {symbol}</p>
      </header>
      {!current && <p className="px-4 py-8 text-sm text-text-muted sm:px-6" role="status">Đang tải lịch sử Demo · {symbol}…</p>}
      {current && 'error' in current && (
        <div className="flex flex-wrap items-center gap-3 px-4 py-6 sm:px-6" role="alert">
          <p className="text-sm text-text-secondary">{current.error}</p>
          <button type="button" className={action} onClick={() => setAttempt((value) => value + 1)}>Thử lại</button>
        </div>
      )}
      {history && (
        <>
          <dl className="grid grid-cols-2 gap-x-4 gap-y-3 border-b border-border-subtle px-4 py-3 text-xs sm:grid-cols-[9rem_13rem_1fr] sm:px-6">
            <div><dt className="text-text-muted">Setup đã đóng</dt><dd className="mt-1 font-mono text-sm font-semibold text-text-primary">{number.format(history.total)}</dd></div>
            <div>
              <dt className="text-text-muted">Profit sàn · USD</dt>
              <dd className={`mt-1 font-mono text-sm font-semibold ${pnl == null || pnl === 0 ? 'text-text-primary' : pnl > 0 ? 'text-bull' : 'text-bear'}`}>
                {pnl == null ? '—' : usd.format(pnl)}
              </dd>
              <dd className="text-text-muted">
                Chưa trừ phí và swap{history.profit_count < history.total ? ` · ${history.profit_count}/${history.total} có số liệu` : ''}
              </dd>
            </div>
            <div className="col-span-2 sm:col-span-1"><dt className="text-text-muted">Đồng bộ lần cuối</dt><dd className="mt-1 text-text-secondary">{formatTime(history.synced_at, true)}</dd></div>
          </dl>
          {history.groups.length === 0 ? (
            <p className="px-4 py-8 text-sm text-text-muted sm:px-6">Chưa có lệnh Demo đã đóng cho {symbol}.</p>
          ) : (
            <ul className="divide-y divide-border-subtle" aria-label={`Các setup Demo đã đóng của ${symbol}`}>
              {history.groups.map((group) => {
                const closedAt = Math.max(...group.legs.map((leg) => leg.close_time ?? 0));
                const profit = group.realized_pnl;
                return (
                  <li key={group.id}>
                    <details className="group">
                      <summary className="grid min-h-14 cursor-pointer list-none grid-cols-[1fr_auto] items-center gap-x-4 gap-y-1 px-4 py-2 text-xs hover:bg-surface-1 focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-bull sm:grid-cols-[1fr_9rem_11rem_7rem] sm:px-6 [&::-webkit-details-marker]:hidden">
                        <span className="flex min-w-0 flex-wrap items-center gap-2 text-text-primary">
                          <ChevronRight aria-hidden="true" size={14} className="shrink-0 text-text-muted group-open:rotate-90" />
                          <span className={group.side === 'buy' ? 'text-bull' : 'text-bear'}>{group.side?.toUpperCase() ?? '—'}</span>
                          <strong className="font-mono">{group.symbol} #{group.legs[0].position_id}</strong>
                        </span>
                        <span className="order-3 font-mono tabular-nums text-text-muted sm:order-none">{group.initial_volume == null ? '—' : number.format(group.initial_volume)} lot · {group.ticket_count} ticket</span>
                        <span className="order-4 text-right text-text-muted sm:order-none sm:text-left">{formatTime(closedAt || null, true)}</span>
                        <strong className={`text-right font-mono tabular-nums ${profit == null || profit === 0 ? 'text-text-secondary' : profit > 0 ? 'text-bull' : 'text-bear'}`}>
                          {profit == null ? '—' : `${usd.format(profit)} USD`}
                        </strong>
                      </summary>
                      <div className="divide-y divide-border-subtle bg-surface-1 px-4 sm:px-6">
                        {group.legs.map((leg) => (
                          <dl key={leg.position_id} className="grid grid-cols-2 gap-x-4 gap-y-2 border-t border-border-subtle py-3 text-xs sm:grid-cols-[repeat(4,minmax(0,1fr))_1.5fr_minmax(0,1fr)] [&_dt]:text-text-muted [&_dd]:mt-0.5 [&_dd]:font-mono [&_dd]:text-text-secondary [&_dd]:tabular-nums">
                            <div><dt>Ticket Demo</dt><dd>#{leg.position_id}</dd></div>
                            <div><dt>Lot</dt><dd>{leg.initial_volume == null ? '—' : number.format(leg.initial_volume)}</dd></div>
                            <div><dt>Giá vào</dt><dd>{leg.open_price == null ? '—' : formatForexPrice(leg.open_price)}</dd></div>
                            <div><dt>Giá đóng</dt><dd>{leg.close_price == null ? '—' : formatForexPrice(leg.close_price)}</dd></div>
                            <div><dt>Giờ đóng</dt><dd>{formatTime(leg.close_time, true)}</dd></div>
                            <div className="sm:text-right"><dt>Profit sàn · USD</dt><dd>{leg.pnl == null ? '—' : usd.format(leg.pnl)}</dd></div>
                          </dl>
                        ))}
                      </div>
                    </details>
                  </li>
                );
              })}
            </ul>
          )}
          {(offset > 0 || history.has_more) && (
            <nav className="flex flex-wrap items-center justify-between gap-3 border-t border-border-subtle px-4 py-3 text-xs text-text-muted sm:px-6" aria-label="Phân trang lịch sử Demo">
              <span>{offset + 1}–{offset + history.groups.length} / {history.total} setup</span>
              <div className="flex gap-2">
                <button type="button" className={action} disabled={!offset} onClick={() => setOffset(offset - limit)}><ChevronLeft size={14} aria-hidden="true" /> Trước</button>
                <button type="button" className={action} disabled={!history.has_more} onClick={() => setOffset(offset + limit)}>Sau <ChevronRight size={14} aria-hidden="true" /></button>
              </div>
            </nav>
          )}
        </>
      )}
    </section>
  );
}
