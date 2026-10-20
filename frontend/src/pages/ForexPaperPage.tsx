import type { ObservationChartSnapshot } from '../components/MtfObservationChart';
import { useState } from 'react';

interface Trade {
  confirm_time: number;
  status: string;
  side: string;
  entry?: number;
  entry_time?: number;
  stop: number;
  target?: number;
  units?: number;
  exit?: number;
  exit_time?: number;
  pnl?: number;
  net_r?: number;
  reason?: string;
  outcome?: string;
  ambiguous_bar?: boolean;
  exit_source?: string;
  monitoring?: string;
  last_quote_time?: number;
  journal_version?: number;
  lots?: number;
  execution_model?: string;
  entry_bid?: number;
  entry_ask?: number;
  entry_spread?: number;
  exit_spread?: number | null;
  entry_slippage_usd?: number;
  exit_slippage_usd?: number;
  commission_usd?: number;
  commission_verified?: boolean;
  signal?: {
    trend?: string;
    closed_4h_time?: number;
    ema_4h?: number;
    breakout_time?: number;
    level?: number;
    touch_time?: number;
    reason?: string;
  };
}

interface PaperReport {
  expectancy_r: number | null;
  sum_r: number | null;
  profit_factor: number | null;
  profit_factor_status: string;
  max_closed_drawdown_usd: number;
  max_closed_drawdown_percent: number;
  modeled_slippage_usd: number;
  known_commission_usd: number;
  commission_unverified_count: number;
  swap_excluded_count: number;
  legacy_journal_count: number;
  slippage_coverage_count: number;
  resolved_count: number;
  first_confirmation: number | null;
  last_exit: number | null;
  groups: Record<string, Record<string, { count: number; pnl: number; mean_r: number | null }>>;
}

export interface ForexPaperSummary {
  status: string;
  message: string;
  assumptions?: {
    risk_percent: number;
    slippage_bps_each_side: number;
    round_trip_commission_per_oz: number;
  };
  calibration?: {
    status: string;
    checked_at: number | null;
    error: string | null;
    profile: {
      observed_at: number;
      contract_size: string;
      volume_min: string;
      volume_max: string;
      volume_step: string;
      point_digits: number;
      account_leverage: string;
      margin_percent: string;
      commission_type: string | null;
      commission_per_unit: number | null;
      swap_long: string;
      swap_short: string;
      issues: string[];
    } | null;
  };
  portfolios?: {
    channel: 'live' | 'replay' | 'trend' | 'range' | 'router';
    strategy?: string;
    observation?:
      | (Partial<ObservationChartSnapshot> & {
          candle_time: number;
          status: string;
          reason: string;
          available_at?: number;
          provenance?: string;
        })
      | null;
    confirmed_count?: number;
    accepted_count?: number;
    rejection_reasons?: Record<string, number>;
    status: string;
    balance: number;
    equity: number | null;
    unrealized_pnl: number | null;
    total_pnl: number;
    win_rate: number | null;
    resolved_count: number;
    last_candle: number | null;
    open_trades: Trade[];
    recent_trades: Trade[];
    report?: PaperReport;
  }[];
}

const labels: Record<string, string> = {
  pending: 'Chờ mở nến kế tiếp',
  open: 'Đang mở',
  resolved: 'Đã đóng',
  censored: 'Chưa xác định',
  skipped: 'Bỏ qua',
  invalid: 'Giá mở không hợp lệ',
  stop: 'Chạm SL',
  target: 'Chạm TP',
  stop_gap: 'Gap vượt SL',
  target_at_open: 'TP tại mở nến',
  unverified_session_gap: 'Thiếu đường giá / nghỉ phiên chưa xác minh',
  unresolved_gap: 'Ví bị chặn do vị thế chưa xác định',
  excluded: 'Loại khỏi thống kê (thiếu nến)',
  data_gap: 'Khoảng trống dữ liệu',
  session_close: 'Đóng trước nghỉ phiên',
  session_close_near: 'Gần giờ nghỉ phiên; không vào',
  insufficient_balance: 'Không đủ vốn',
  position_already_active: 'Đã có vị thế',
  missed_entry_while_offline: 'Bỏ lỡ giá vào khi máy ngủ',
  live_quote: 'Khớp mô phỏng theo tick Bid/Ask',
  closed_candle: 'Đối soát theo nến đã đóng',
  live: 'SL/TP đang theo tick Bid/Ask',
  waiting_history: 'Chờ đối soát nến cũ trước tick mới',
  waiting_closed_bar: 'Thiếu tick; chờ nến M15 đóng để đối soát',
  below_minimum_lot: 'Khối lượng theo rủi ro nhỏ hơn lot tối thiểu; không tăng lot để ép vào lệnh',
  currency_conversion_unverified: 'Chưa xác minh quy đổi tiền tệ',
  commission_unavailable: 'API thiếu commission: chưa coi là miễn phí',
  swap_units_and_rollover_unverified: 'Chưa xác minh đơn vị swap, giờ rollover và swap-free',
  dynamic_margin_unverified: 'Chưa đối soát margin/HMR động; vẫn giới hạn notional 1 lần vốn',
  slippage_assumed: 'Trượt giá vẫn là giả định, không phải số đo khớp lệnh thật',
};
const amount = (value?: number | null) =>
  value == null
    ? '—'
    : value.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const date = (stamp?: number | null) =>
  stamp == null ? '—' : new Date(stamp * 1000).toISOString().slice(0, 16).replace('T', ' ');

export function ForexPaperPage({ paper, paused }: { paper: ForexPaperSummary; paused: boolean }) {
  const [channel, setChannel] = useState<'live' | 'replay' | 'trend' | 'range' | 'router'>('live');
  const account = paper.portfolios?.find((row) => row.channel === channel);
  const costs = paper.assumptions;
  const report = account?.report;
  const calibration = paper.calibration;
  const profile = calibration?.profile;
  function table(rows: Trade[]) {
    return rows.length === 0 ? (
      <p>Chưa có lệnh trong kênh này.</p>
    ) : (
      <div className="table-scroll">
        <table>
          <caption>Thời gian UTC · giá USD/oz · khối lượng oz (không phải lot sàn)</caption>
          <thead>
            <tr>
              <th>Xác nhận</th>
              <th>Chiều</th>
              <th>Vào / thời gian</th>
              <th>Oz</th>
              <th>SL / TP</th>
              <th>Thoát / thời gian</th>
              <th>P&L USD / R</th>
              <th>Trạng thái / lý do</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.confirm_time}>
                <td>{date(row.confirm_time)}</td>
                <td>{row.side.toUpperCase()}</td>
                <td>
                  {amount(row.entry)}
                  <br />
                  {date(row.entry_time)}
                </td>
                <td>{amount(row.units)}</td>
                <td>
                  {amount(row.stop)} / {amount(row.target)}
                </td>
                <td>
                  {amount(row.exit)}
                  <br />
                  {date(row.exit_time)}
                </td>
                <td className={row.pnl == null ? '' : row.pnl >= 0 ? 'positive' : 'negative'}>
                  {amount(row.pnl)} / {amount(row.net_r)}R
                </td>
                <td>
                  {labels[row.outcome ?? row.status] ?? row.status}
                  {row.exit_source && (
                    <>
                      <br />
                      {labels[row.exit_source] ?? row.exit_source}
                    </>
                  )}
                  {row.status === 'open' && (
                    <>
                      <br />
                      {row.last_quote_time == null || Date.now() / 1000 - row.last_quote_time > 15
                        ? 'Tick chưa có hoặc đã cũ; chờ đối soát nến'
                        : labels[row.monitoring ?? 'waiting_history']}
                    </>
                  )}
                  {row.reason && (
                    <>
                      <br />
                      {labels[row.reason] ?? row.reason}
                    </>
                  )}
                  {row.ambiguous_bar && (
                    <>
                      <br />
                      Cùng nến chạm SL/TP; ưu tiên SL
                    </>
                  )}
                  <details>
                    <summary>Nhật ký tín hiệu / chi phí</summary>
                    {row.journal_version === 2 ? (
                      <>
                        {(channel === 'live' || channel === 'replay') && (
                          <>
                            <p>
                              H4: {row.signal?.trend ?? '—'} · EMA {amount(row.signal?.ema_4h)} ·
                              nến {date(row.signal?.closed_4h_time)} UTC.
                            </p>
                            <p>
                              H1 breakout: {date(row.signal?.breakout_time)} · vùng{' '}
                              {amount(row.signal?.level)}. M15 retest:{' '}
                              {date(row.signal?.touch_time)} UTC.
                            </p>
                          </>
                        )}
                        <p>{row.signal?.reason ?? 'Không có lý do xác nhận trong snapshot.'}</p>
                        <p>
                          Bid / Ask vào: {amount(row.entry_bid)} / {amount(row.entry_ask)}. Spread
                          vào / thoát USD/oz: {amount(row.entry_spread)} / {amount(row.exit_spread)}
                          .
                        </p>
                        <p>
                          Lot: {amount(row.lots)} · mô hình {row.execution_model ?? '—'}. Trượt giá
                          vào / thoát USD: {amount(row.entry_slippage_usd)} /{' '}
                          {amount(row.exit_slippage_usd)}.
                        </p>
                        <p>
                          Commission:{' '}
                          {row.commission_verified
                            ? `${amount(row.commission_usd)} USD (mô hình khấu trừ vòng lệnh khi đóng)`
                            : 'Chưa xác minh; P&L tạm chưa trừ khoản này'}
                          . Swap chưa tính.
                        </p>
                      </>
                    ) : (
                      <p>Lệnh cũ thiếu nhật ký chi tiết; không bù bằng thông số hiện tại.</p>
                    )}
                  </details>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    );
  }
  return (
    <section className="panel prose forex-paper">
      <div className="section-head">
        <div>
          <h2>Paper cũ Exness · XAUUSD (đã ngừng)</h2>
          <p>Lịch sử ví family cũ, chỉ để đọc; không sinh lệnh mới.</p>
        </div>
        <span className="pill amber">PAPER · CỤC BỘ</span>
      </div>
      <div className="forex-paper-switch" role="group" aria-label="Chọn kênh paper">
        <button
          className="secondary"
          aria-pressed={channel === 'live'}
          onClick={() => setChannel('live')}
        >
          Live · tín hiệu mới
        </button>
        <button
          className="secondary"
          aria-pressed={channel === 'replay'}
          onClick={() => setChannel('replay')}
        >
          Replay · tín hiệu bỏ lỡ
        </button>
        <button
          className="secondary"
          aria-pressed={channel === 'trend'}
          onClick={() => setChannel('trend')}
        >
          Thử nghiệm · Trend Pullback
        </button>
        <button
          className="secondary"
          aria-pressed={channel === 'range'}
          onClick={() => setChannel('range')}
        >
          Thử nghiệm · Range Rejection
        </button>
        <button
          className="secondary"
          aria-pressed={channel === 'router'}
          onClick={() => setChannel('router')}
        >
          Thử nghiệm · Regime Router
        </button>
      </div>
      <p>
        {channel === 'trend' || channel === 'range' || channel === 'router'
          ? 'Ví thử nghiệm riêng, theo dõi tiến tới từ lúc worker khởi tạo; chưa chứng minh lợi thế. Khớp theo nến M15 đã đóng, không gửi lệnh tới sàn.'
          : channel === 'live'
            ? 'Chỉ tín hiệu và giá vào xử lý kịp lúc. Vị thế đã mở vẫn đối soát SL/TP lịch sử khi máy hoạt động lại.'
            : 'Mô phỏng tín hiệu xử lý chạy bù từ khi ví khởi tạo, vào theo giá mở nến kế tiếp trong lịch sử, không theo giá hiện tại. Không gộp vào ví Live. Replay toàn bộ kho nến nằm ở tab Backtest.'}
      </p>
      {account && (
        <div role="status">
          <p>
            Setup xác nhận: {account.confirmed_count ?? 0} · Được duyệt:{' '}
            {account.accepted_count ?? 0}. Được duyệt chưa đồng nghĩa đã khớp.
          </p>
          {account.observation && (
            <p>
              Quan sát M15 lúc {date(account.observation.candle_time)} UTC:{' '}
              {account.observation.reason}
            </p>
          )}
          {Object.entries(account.rejection_reasons ?? {}).map(([reason, count]) => (
            <p key={reason}>
              {labels[reason] ?? reason}: {count}
            </p>
          ))}
        </div>
      )}
      {paused && (
        <p role="status" className="negative">
          Worker/API hoặc dữ liệu chưa sẵn sàng. Số liệu là trạng thái lưu gần nhất.
        </p>
      )}
      {!account || account.status === 'not_started' ? (
        <p role="status">
          Chờ worker đồng bộ dữ liệu và khởi tạo ví. Không mở lệnh từ lịch sử trước lúc bật.
        </p>
      ) : (
        <>
          {account.status === 'blocked' && (
            <p role="alert" className="negative">
              Ví bị chặn mở lệnh: vị thế gặp khoảng giá chưa xác minh. Equity chưa xác định; số dư
              không phản ánh tổn thất chưa đối soát.
            </p>
          )}
          <div className="research-metrics">
            <div>
              <span>Số dư USD</span>
              <strong>{amount(account.balance)}</strong>
            </div>
            <div>
              <span>Equity USD</span>
              <strong>{amount(account.equity)}</strong>
            </div>
            <div>
              <span>P&L đã chốt USD</span>
              <strong>{amount(account.total_pnl)}</strong>
            </div>
            <div>
              <span>P&L chưa chốt USD</span>
              <strong>{amount(account.unrealized_pnl)}</strong>
            </div>
            <div>
              <span>Tỷ lệ thắng · {account.resolved_count} lệnh đóng</span>
              <strong>
                {account.win_rate == null ? '—' : `${(account.win_rate * 100).toFixed(1)}%`}
              </strong>
            </div>
          </div>
          <p>Đối soát đến nến mở {date(account.last_candle)} UTC; chưa phải giá tức thời.</p>
          {report && (
            <>
              <h3>Báo cáo {channel === 'live' ? 'Live' : 'Replay'} · chỉ lệnh đã đóng</h3>
              <div className="research-metrics">
                <div>
                  <span>Kỳ vọng R/lệnh · {report.resolved_count} lệnh</span>
                  <strong>{amount(report.expectancy_r)}R</strong>
                </div>
                <div>
                  <span>Tổng R</span>
                  <strong>{amount(report.sum_r)}R</strong>
                </div>
                <div>
                  <span>Profit factor</span>
                  <strong>
                    {report.profit_factor_status === 'no_losses'
                      ? 'Chưa có lệnh lỗ'
                      : amount(report.profit_factor)}
                  </strong>
                </div>
                <div>
                  <span>Drawdown số dư đã chốt</span>
                  <strong>
                    {amount(report.max_closed_drawdown_usd)} USD ·{' '}
                    {amount(report.max_closed_drawdown_percent)}%
                  </strong>
                </div>
              </div>
              <p>
                Tín hiệu đầu {date(report.first_confirmation)} UTC; thoát gần nhất{' '}
                {date(report.last_exit)} UTC. Drawdown chưa gồm vị thế mở. Live/Replay khác thời
                điểm, không so trực tiếp hay gộp kết quả.
              </p>
              <p>
                Trượt giá mô phỏng: {amount(report.modeled_slippage_usd)} USD (
                {report.slippage_coverage_count}/{report.resolved_count} lệnh đủ nhật ký).
                Commission đã xác minh: {amount(report.known_commission_usd)} USD;{' '}
                {report.commission_unverified_count} lệnh chưa xác minh.{' '}
                {report.swap_excluded_count} lệnh chưa tính swap; {report.legacy_journal_count} nhật
                ký cũ thiếu trường.
              </p>
              <div className="table-scroll">
                <table>
                  <caption>Phân nhóm để đọc nguyên nhân, không phải các ví độc lập</caption>
                  <thead>
                    <tr>
                      <th>Nhóm</th>
                      <th>Giá trị</th>
                      <th>Lệnh đóng</th>
                      <th>P&L mô phỏng USD</th>
                      <th>R trung bình</th>
                    </tr>
                  </thead>
                  <tbody>
                    {Object.entries(report.groups).flatMap(([group, values]) =>
                      Object.entries(values).map(([value, metrics]) => (
                        <tr key={`${group}-${value}`}>
                          <td>
                            {{
                              side: 'Chiều',
                              exit_source: 'Nguồn thoát',
                              execution_model: 'Phiên bản khớp',
                              exit_month_utc: 'Tháng thoát UTC',
                            }[group] ?? group}
                          </td>
                          <td>{labels[value] ?? value}</td>
                          <td>{metrics.count}</td>
                          <td>{amount(metrics.pnl)}</td>
                          <td>{amount(metrics.mean_r)}R</td>
                        </tr>
                      )),
                    )}
                  </tbody>
                </table>
              </div>
            </>
          )}
          <p>
            <a href={`/api/workspaces/exness_forex/paper/trades.csv?channel=${channel}`} download>
              Tải toàn bộ nhật ký {channel} · CSV, timestamp UTC giây
            </a>
          </p>
          <h3>Vị thế / lệnh chờ</h3>
          {table(account.open_trades)}
          <h3>Lịch sử gần nhất · tối đa 20 tín hiệu</h3>
          {table(account.recent_trades)}
        </>
      )}
      <h3>Giả định mô phỏng</h3>
      <h3>Đối soát thông số tài khoản Demo</h3>
      <p>
        Trạng thái:{' '}
        {calibration?.status === 'partial'
          ? 'Đã đọc thông số; chi phí chưa xác minh đầy đủ'
          : calibration?.status === 'stale'
            ? 'Thông số đã cũ / lần đọc gần nhất lỗi'
            : 'Chưa lấy được thông số'}
        . Kiểm tra {date(calibration?.checked_at)} UTC.
      </p>
      {profile && (
        <>
          <p>
            Snapshot {date(profile.observed_at)} UTC. Contract {profile.contract_size} oz/lot; lot
            min / step / max: {profile.volume_min} / {profile.volume_step} / {profile.volume_max};
            giá {profile.point_digits} chữ số thập phân.
          </p>
          <p>
            Leverage tài khoản 1:{profile.account_leverage}; margin_percent API{' '}
            {profile.margin_percent} (chưa dùng để khẳng định margin thực tế). Commission vòng lệnh:{' '}
            {profile.commission_per_unit == null
              ? 'Chưa xác minh'
              : `${amount(profile.commission_per_unit)} USD/oz`}
            . Swap API Long / Short: {profile.swap_long} / {profile.swap_short}; chưa quy đổi sang
            USD.
          </p>
          <ul>
            {profile.issues.map((issue) => (
              <li key={issue}>{labels[issue] ?? issue}</li>
            ))}
          </ul>
        </>
      )}
      <p>
        Rủi ro mục tiêu {costs?.risk_percent ?? 1}% vốn; tổng giá trị vị thế tối đa 1 lần vốn, một
        vị thế mỗi ví. Khớp ở mở nến M15 tiếp theo, chỉ ghi nhận sau khi nến đóng. Long vào Ask,
        thoát Bid; Short ngược lại. TP 2R tính từ giá vào mô phỏng; SL theo cấu trúc retest.
      </p>
      <p>
        Trượt giá giả định {costs?.slippage_bps_each_side ?? 2} bps mỗi chiều; commission theo
        snapshot từng lệnh, không mặc định phí thực bằng 0 khi API thiếu trường. Spread đã nằm trong
        giá Bid/Ask, không trừ lần hai. Lot được làm tròn xuống khi có snapshot hợp lệ; Replay cũ
        không áp thông số tương lai. Chưa tính swap hoặc margin/HMR động. Nếu cùng nến chạm SL/TP,
        ưu tiên SL; gap vượt SL khớp tại giá mở bất lợi, không ép về SL.
      </p>
      <p>
        Ví Live đóng SL/TP theo tick Bid/Ask khi dữ liệu liên tục và nến cũ đã đối soát. Sau mất kết
        nối, chờ nến M15 thiếu tick đóng để xác định SL/TP trước; không lấy giá mới thay cho lần
        chạm cũ. TP khớp bảo thủ tại mức TP; SL theo giá tick, có thể vượt mức SL. Ví Replay chỉ
        dùng nến đã đóng. Máy ngủ không có bảo vệ trên sàn; đây là khớp mô phỏng.
      </p>
    </section>
  );
}
