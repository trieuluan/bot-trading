import type { Dispatch, SetStateAction } from 'react';
import type { BrokerPosition, ExnessExecution } from '../types/exness-execution';
import { ForexPositionGroups } from './ForexPositionGroups';
import type { PositionGroup } from '../lib/forex-position-groups';
import { ForexEntryPilotPanel, HtfLineage } from '../pages/ForexStrategyPage';
import { blockLabel } from '../lib/forex-labels';
import { formatTime } from '../lib/time-format';
import { DecisionJournal } from './DecisionJournal';
import { Activity, GitBranch, ListOrdered, ShieldCheck } from 'lucide-react';
import vpsLearning from '../lib/forex-vps-learning.json';

export type ExecutionPanel = 'orders' | 'learning';
export type ExecutionFilter = 'all' | 'open' | 'closed' | 'bot';
const panels: [ExecutionPanel, string][] = [
  ['orders', 'Lệnh trên sàn'],
  ['learning', 'Tiến hoá & Cây HTF'],
];
const url = '/api/workspaces/exness_forex/execution';
const date = (stamp: number | null) => formatTime(stamp, true);

export function ForexExecutionTabs({ data, panel, setPanel, filter, setFilter, offset, setOffset, selectedId, onSelect, closeTicket, closeSetup, closeDisabled, pnlOf, preview = false }: {
  data: ExnessExecution;
  panel: ExecutionPanel;
  setPanel: (panel: ExecutionPanel) => void;
  filter: ExecutionFilter;
  setFilter: (filter: ExecutionFilter) => void;
  offset: number;
  setOffset: Dispatch<SetStateAction<number>>;
  selectedId?: string;
  onSelect: (id: string) => void;
  closeTicket: (position: BrokerPosition) => void;
  closeSetup: (group: PositionGroup) => void;
  closeDisabled: boolean;
  pnlOf: (position: BrokerPosition) => number | null;
  preview?: boolean;
}) {
  const symbol = data.symbol;
  const rows = data.groups ?? [];
  const closedRows = rows.filter((group) => group.status === 'closed' && group.realized_pnl != null);
  const wins = closedRows.filter((group) => (group.realized_pnl ?? 0) > 0).length;
  const treeCount = data.learning?.htf_ast3_shadow?.tree_count ?? 0;
  const realized = closedRows.reduce((total, group) => total + (group.realized_pnl ?? 0), 0);
  return (
        <section className="panel terminal-bottom mt-3 min-w-0 overflow-hidden rounded-xl border border-border-base bg-surface-1">
          <nav className="terminal-tabs flex flex-wrap items-center gap-1 border-b border-border-subtle bg-surface-2/40 px-3 py-2" role="tablist" aria-label="Không gian bot">
            {panels.map(([key, title], index) => (
              <button
                key={key}
                id={`execution-tab-${key}`}
                type="button"
                role="tab"
                className={`inline-flex min-h-10 flex-1 items-center justify-center gap-2 rounded-lg border px-3 text-xs font-semibold transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-bull sm:flex-none ${panel === key ? 'border-bull/25 bg-bull/10 text-bull shadow-sm' : 'border-transparent text-text-muted hover:bg-surface-2 hover:text-text-primary'}`}
                aria-selected={panel === key}
                aria-controls={`execution-panel-${key}`}
                tabIndex={panel === key ? 0 : -1}
                onClick={() => setPanel(key)}
                onKeyDown={(event) => {
                  let target = index;
                  if (event.key === 'ArrowRight') target = (index + 1) % panels.length;
                  else if (event.key === 'ArrowLeft')
                    target = (index + panels.length - 1) % panels.length;
                  else if (event.key === 'Home') target = 0;
                  else if (event.key === 'End') target = panels.length - 1;
                  else return;
                  event.preventDefault();
                  setPanel(panels[target][0]);
                  document.getElementById(`execution-tab-${panels[target][0]}`)?.focus();
                }}
              >
                {key === 'orders' ? <ListOrdered size={15} aria-hidden="true" /> : <GitBranch size={15} aria-hidden="true" />}
                {title}
                {key === 'orders' && (
                  <span className="terminal-tab-count rounded-md bg-surface-3 px-1.5 py-0.5 font-mono text-[10px] text-text-secondary tabular-nums">{data.open_count}</span>
                )}
              </button>
            ))}
            <span className="terminal-tabs-note ml-auto hidden items-center gap-1.5 text-[11px] text-text-muted md:inline-flex" role="presentation">
              <ShieldCheck size={14} aria-hidden="true" /> {preview ? 'Dữ liệu giả · chỉ xem UI' : 'Dữ liệu broker · tài khoản demo'}
            </span>
          </nav>
          <section
            hidden={panel !== 'orders'}
            id="execution-panel-orders"
            role="tabpanel"
            aria-labelledby="execution-tab-orders"
            tabIndex={0}
            className="execution-tab-content orders-tab-view min-w-0 space-y-4 p-3 sm:p-4"
          >
            <div className="orders-section-card min-w-0 space-y-3">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div><h3 className="m-0 text-sm font-semibold text-text-primary">Lệnh &amp; vị thế <span className="ml-1.5 rounded bg-surface-3 px-1.5 py-0.5 font-mono text-[10px] text-text-secondary">{symbol}</span></h3><p className="mb-0 mt-1 text-xs text-text-muted">Một setup gom các ticket TP1 / Runner. Mở chi tiết để đối soát.</p></div>
                {preview ? <span className="text-xs text-amber-300">Xuất CSV bị khóa trong bản demo UI</span> : <a className="inline-flex min-h-9 items-center rounded-lg border border-border-base bg-surface-2 px-3 text-xs font-medium text-text-secondary transition-colors hover:border-border-active hover:bg-surface-3 focus-visible:outline-2 focus-visible:outline-bull" href={`${url}/trades.csv?symbol=${symbol}`} download>Xuất CSV {symbol}</a>}
              </div>
              <dl className="grid grid-cols-2 gap-2 lg:grid-cols-4 [&_dt]:text-[11px] [&_dt]:text-text-muted [&_dd]:mt-1 [&_dd]:font-mono [&_dd]:text-lg [&_dd]:font-semibold [&_dd]:tabular-nums">
                <div className="rounded-lg border border-bull/20 bg-bull/5 p-3"><dt>Ticket đang mở · {symbol}</dt><dd className="text-bull">{data.open_count}</dd></div>
                <div className="rounded-lg border border-border-subtle bg-surface-2/50 p-3"><dt>Setup trong bộ lọc</dt><dd>{data.total}</dd></div>
                <div className="rounded-lg border border-border-subtle bg-surface-2/50 p-3"><dt>Đã chốt · trang này · USD</dt><dd className={closedRows.length ? realized >= 0 ? 'text-bull' : 'text-bear' : 'text-text-muted'}>{closedRows.length ? `${realized > 0 ? '+' : ''}${realized.toFixed(2)}` : '—'}</dd></div>
                <div className="rounded-lg border border-border-subtle bg-surface-2/50 p-3"><dt>Tỷ lệ thắng · trang này</dt><dd>{closedRows.length ? `${Math.round(wins / closedRows.length * 100)}%` : '—'} <span className="text-[11px] font-normal text-text-muted">{wins}/{closedRows.length} setup đóng</span></dd></div>
              </dl>
              <div className="orders-card-header mb-3 flex min-w-0 flex-wrap items-center justify-between gap-2 [&_p]:text-xs [&_p]:text-text-muted">
                <div className="filter-pills flex flex-wrap items-center gap-1" role="group" aria-label="Lọc lệnh">
                  {(
                    [
                      ['all', 'Tất cả'],
                      ['open', 'Đang mở'],
                      ['closed', 'Đã đóng'],
                      ['bot', 'Của bot'],
                    ] as [ExecutionFilter, string][]
                  ).map(([key, text]) => (
                    <button
                      key={key}
                      className={`min-h-9 rounded-lg border px-3 py-1.5 text-xs transition-colors focus-visible:outline-2 focus-visible:outline-bull ${filter === key ? 'border-bull/30 bg-bull/10 font-semibold text-bull' : 'border-border-base bg-surface-2 text-text-secondary hover:bg-surface-3'}`}
                      aria-pressed={filter === key}
                      onClick={() => { setFilter(key); setOffset(0); }}
                    >
                      {text}
                    </button>
                  ))}
                </div>
                <div className="orders-header-note ml-auto text-xs text-text-muted">
                  {data.total ? offset + 1 : 0}–{Math.min(offset + 50, data.total)} / {data.total}{' '}
                  setup
                </div>
              </div>

              <ForexPositionGroups groups={rows} selectedId={selectedId}
                onSelect={onSelect}
                onCloseTicket={preview ? () => {} : closeTicket} onCloseSetup={preview ? () => {} : closeSetup} closeDisabled={preview || closeDisabled} pnlOf={pnlOf} />

              {data.total > 50 && <div className="orders-pagination-bar flex min-w-0 flex-wrap items-center justify-between gap-2 border-t border-border-base pt-3 text-xs [&_button]:min-h-9 [&_button:disabled]:cursor-not-allowed [&_button:disabled]:opacity-40">
                <button
                  className="secondary inline-flex items-center justify-center gap-1.5 rounded-md border border-border-active bg-surface-2 px-3 py-1.5 text-xs text-text-secondary hover:bg-surface-3"
                  disabled={offset === 0}
                  onClick={() => setOffset(Math.max(0, offset - 50))}
                >
                  ← Trang trước
                </button>
                <span className="muted text-text-muted">
                  Trang {Math.floor(offset / 50) + 1} / {Math.max(1, Math.ceil(data.total / 50))}
                </span>
                <button
                  className="secondary inline-flex items-center justify-center gap-1.5 rounded-md border border-border-active bg-surface-2 px-3 py-1.5 text-xs text-text-secondary hover:bg-surface-3"
                  disabled={!data.has_more}
                  onClick={() => setOffset(offset + 50)}
                >
                  Trang sau →
                </button>
              </div>}
            </div>
            <details className="rounded-lg border border-border-subtle bg-surface-2/30 px-3 py-2 text-xs text-text-muted"><summary className="flex min-h-7 items-center gap-2"><ShieldCheck size={14} aria-hidden="true" />Quản lý rủi ro &amp; đồng bộ lịch sử</summary><p className="mt-2 leading-relaxed">
              SL theo vô hiệu H1 + ATR buffer; TP theo barrier cấu trúc H1/H4 (tối thiểu 1.5R). Tạm
              dừng không đóng vị thế đang mở. Mac ngủ thì bot không mở/đóng chủ động, SL/TP trên sàn
              vẫn có hiệu lực; khi mở lại sẽ đối soát fill thật.
            </p><p className="mt-2 leading-relaxed">Lịch sử nhập từ {date(data.history_from)}; đồng bộ tăng dần, không trộn paper/replay.</p></details>
            <details className="rounded-lg border border-border-subtle bg-surface-2/40 p-3 text-xs text-text-secondary">
              <summary className="cursor-pointer font-medium">Lệnh chờ sàn & nhật ký gửi lệnh · {data.orders.length}</summary>
              <p>
                {data.orders.length} lệnh chờ/SLTP trên sàn. Lệnh chưa xác minh kết quả sẽ chặn mở
                thêm, không gửi lại.
              </p>
              {data.orders.map((order) => (
                <p key={order.order_id}>
                  #{order.order_id} · {order.instrument} · {order.order_type} · {order.volume}
                </p>
              ))}
              {data.intents.map((intent) => (
                <p key={intent.signal_time}>
                  {date(intent.signal_time)} · {intent.status} ·{' '}
                  {intent.ack?.operation_id ?? 'Chưa có ACK'} · {intent.error ?? intent.reason}
                </p>
              ))}
            </details>
          </section>
          <section
            hidden={panel !== 'learning'}
            id="execution-panel-learning"
            role="tabpanel"
            aria-labelledby="execution-tab-learning"
            tabIndex={0}
            className="execution-tab-content learning-tab-view min-w-0 space-y-4 p-3 sm:p-4"
            aria-label="Bộ học chiến lược"
          >
            <div className="flex flex-wrap items-start justify-between gap-3"><div><h3 className="flex items-center gap-2"><GitBranch size={17} className="text-sky-300" aria-hidden="true" />Tiến hoá chiến thuật</h3><p className="mt-1 text-xs text-text-muted">Theo dõi quyết định, thử nghiệm cách vào lệnh và dòng dõi cây HTF.</p></div><span className="rounded-md border border-sky-400/20 bg-sky-400/10 px-2 py-1 text-[11px] text-sky-300">Forward · sau chi phí, chưa swap</span></div>
            {preview && <p role="status" className="rounded-xl border border-sky-400/25 bg-sky-400/5 px-4 py-3 text-xs leading-relaxed text-text-secondary"><strong className="text-sky-300">Snapshot VPS · EURUSD · {formatTime(vpsLearning.captured_at, true)}</strong><br />56 cây V3 + 56 cây pilot V4. VPS hiện ở G0, chưa có mẫu forward đóng; R hiển thị “—”, không phải 0R. Nhật ký quyết định phía dưới là dữ liệu giả để xem UI.</p>}
            <div className="learning-overview-grid grid grid-cols-2 gap-3 lg:grid-cols-4">
              <div className="learning-stat-card min-w-0 rounded-md border border-border-subtle bg-surface-1 p-3 [&_strong]:block [&_strong]:font-mono [&_strong]:text-lg [&_strong]:text-text-primary">
                <span className="card-label mb-1 flex items-center gap-1.5 text-xs text-text-muted">Execution V3</span>
                <div className="card-value font-mono text-xl font-semibold text-text-primary" style={{ fontSize: '18px' }}>
                  {data.learning?.execution_v2?.enabled ? 'Đang gửi lệnh' : 'Chỉ shadow'}
                </div>
                <div className="card-sub text-xs leading-relaxed text-text-muted">
                  {blockLabel(data.learning?.execution_v2?.blocked_reason) ?? 'Sẵn sàng'}
                </div>
              </div>
              <div className="learning-stat-card min-w-0 rounded-md border border-border-subtle bg-surface-1 p-3 [&_strong]:block [&_strong]:font-mono [&_strong]:text-lg [&_strong]:text-text-primary">
                <span className="card-label mb-1 flex items-center gap-1.5 text-xs text-text-muted">Cây được phép vào lệnh</span>
                <div className="card-value font-mono text-xl font-semibold text-text-primary">
                  {data.learning?.execution_v2?.entry_allowed_count ?? 0}
                  <small className="muted text-text-muted"> / {treeCount}</small>
                </div>
                <div className="card-sub text-xs leading-relaxed text-text-muted">
                  Ungated · chọn: {data.learning?.execution_v2?.selected ?? 'không có'}
                </div>
              </div>
              <div className="learning-stat-card min-w-0 rounded-md border border-border-subtle bg-surface-1 p-3 [&_strong]:block [&_strong]:font-mono [&_strong]:text-lg [&_strong]:text-text-primary">
                <span className="card-label mb-1 flex items-center gap-1.5 text-xs text-text-muted">Đủ chuẩn tiến hoá</span>
                <div className="card-value font-mono text-xl font-semibold text-text-primary">
                  {data.learning?.execution_v2?.evolution_ready_count ?? 0}
                  <small className="muted text-text-muted"> / {treeCount}</small>
                </div>
                <div className="card-sub text-xs leading-relaxed text-text-muted">Cần ≥10 mẫu forward và R trung bình &gt; 0</div>
              </div>
              <div className="learning-stat-card min-w-0 rounded-md border border-border-subtle bg-surface-1 p-3 [&_strong]:block [&_strong]:font-mono [&_strong]:text-lg [&_strong]:text-text-primary">
                <span className="card-label mb-1 flex items-center gap-1.5 text-xs text-text-muted">Mẫu forward V3</span>
                <div className="card-value font-mono text-xl font-semibold text-text-primary">{data.learning?.htf_ast3_shadow?.resolved ?? 0}</div>
                <div className="card-sub text-xs leading-relaxed text-text-muted">
                  {data.learning?.htf_ast3_shadow?.pending ?? 0} đang chạy · thế hệ G
                  {data.learning?.htf_ast3_shadow?.generation ?? 0}
                </div>
              </div>
            </div>

            <div className="learning-section-card min-w-0 space-y-3 rounded-md border border-border-subtle bg-surface-1 p-3">
              <div className="card-header mb-3 flex min-w-0 flex-wrap items-center justify-between gap-2 [&_p]:text-xs [&_p]:text-text-muted">
                <div>
                  <h4 className="flex items-center gap-2"><Activity size={15} className="text-amber-300" aria-hidden="true" />Nhật ký quyết định</h4>
                  <span className="muted-desc text-xs leading-relaxed text-text-muted">
                    Mỗi nến M15 đóng: bot thấy gì và dừng ở bước nào
                    {data.funnel ? ` · ${data.funnel.candles} nến gần nhất` : ''}
                  </span>
                </div>
              </div>
              <DecisionJournal journal={data.journal} funnel={data.funnel} />
            </div>
            <ForexEntryPilotPanel
              key={symbol}
              symbol={symbol}
              entryPilot={data.learning?.entry_pilot}
            />
            {data.learning?.htf_ast3_shadow && (
              <details className="group learning-section-card min-w-0 space-y-3 rounded-md border border-border-subtle bg-surface-1 p-3">
                <summary className="card-header mb-3 flex min-w-0 cursor-pointer flex-wrap items-center justify-between gap-2 [&_p]:text-xs [&_p]:text-text-muted">
                  <div>
                    <h4 className="flex items-center gap-2"><GitBranch size={15} className="text-sky-300" aria-hidden="true" />Lưu trữ · Quần thể HTF V3 (chỉ đọc, không sinh setup mới)</h4>
                    <span className="muted-desc text-xs leading-relaxed text-text-muted">
                      Thế hệ {data.learning.htf_ast3_shadow.generation} ·{' '}
                      {data.learning.htf_ast3_shadow.tree_count} cây ·{' '}
                      {data.learning.htf_ast3_shadow.excluded} mẫu bị loại · R sau chi phí, chưa
                      swap
                    </span>
                  </div>
                  <span className="badge-count inline-flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 text-xs font-medium bg-surface-3 text-text-secondary">
                    {data.learning.htf_ast3_shadow.resolved} đã đóng ·{' '}
                    {data.learning.htf_ast3_shadow.pending} đang chạy
                  </span>
                </summary>
                <HtfLineage
                  trees={data.learning.htf_ast3_shadow.trees}
                  nextEvolutionAt={data.learning.htf_ast3_shadow.next_evolution_at}
                />
              </details>
            )}
          </section>
        </section>
  );
}
