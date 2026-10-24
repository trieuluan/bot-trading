// Nhãn và định dạng dùng chung cho workspace Exness Forex.

import { formatTime, relativeTime } from './time-format.ts';

export function forexSignalCount(
  execution:
    | {
        signal_count?: number;
        signal_available_at?: number | null;
        signal_expires_at?: number | null;
      }
    | undefined,
  now = Date.now(),
) {
  const count = execution?.signal_count ?? 0;
  const start = execution?.signal_available_at;
  const end = execution?.signal_expires_at;
  return Number.isInteger(count) &&
    count > 0 &&
    start != null &&
    end != null &&
    start <= now / 1000 &&
    now / 1000 <= end
    ? count
    : 0;
}

/** Lý do chặn entry (execution_v2.blocked_reason hoặc markets.blocked_reason). */
export const blockText: Record<string, string> = {
  v2_execution_disabled: 'Cờ Execution V2 đang tắt',
  exness_execution_disabled: 'Cờ thực thi Exness đang tắt',
  paused: 'Tài khoản tạm dừng mở lệnh',
  no_eligible_tree: 'Chưa có cây được phép vào lệnh',
  v2_ledger_unavailable: 'Chưa có ledger V2 hợp lệ',
  symbol_position_limit: 'Đủ vị thế mỗi cặp',
  account_position_limit: 'Đủ vị thế toàn tài khoản',
  specs_unavailable_or_stale: 'Thông số cặp thiếu hoặc đã cũ',
  commission_unverified: 'Commission chưa xác minh',
};

/** Gợi ý việc cần làm cho từng lý do chặn. */
export const blockHint: Record<string, string> = {
  v2_execution_disabled: 'Đặt FOREX_HTF_V2_EXECUTION_ENABLED=true rồi khởi động lại worker.',
  exness_execution_disabled: 'Đặt EXNESS_DEMO_EXECUTION_ENABLED=true rồi khởi động lại worker.',
  paused: 'Bật lại tại trang Bot.',
  v2_ledger_unavailable: 'Chờ worker tạo/migrate ledger V2 cho cặp này.',
  specs_unavailable_or_stale: 'Chờ worker đồng bộ lại thông số cặp từ Exness.',
  commission_unverified: 'Chờ có deal để xác minh commission.',
};

export const blockLabel = (reason: string | null | undefined) =>
  reason ? (blockText[reason] ?? reason) : null;

/** Dịch lỗi feed/worker trả về từ backend (tiếng Anh) sang tiếng Việt. */
export function feedErrorText(error: string | null | undefined) {
  if (!error) return null;
  if (/rate limit/i.test(error)) return 'Exness giới hạn tần suất API';
  if (/timeout|timed out/i.test(error)) return 'Exness phản hồi quá chậm';
  if (/unauthori[sz]ed|401|403|token/i.test(error)) return 'Lỗi xác thực API Exness';
  return error;
}

export const priceDigits = (symbol: string) =>
  symbol === 'XAUUSD' ? 3 : symbol.endsWith('JPY') ? 3 : 5;

/** Spread theo pip cho FX (JPY: 0.01, còn lại 0.0001); XAUUSD theo USD. */
export function spreadText(value: number | null | undefined, symbol: string) {
  if (value == null) return '—';
  if (symbol === 'XAUUSD') return `${value.toFixed(2)} USD`;
  const pip = symbol.endsWith('JPY') ? 0.01 : 0.0001;
  return `${(value / pip).toFixed(1)} pip`;
}

export const formatLevel = (value: number | null | undefined, symbol: string) =>
  value == null
    ? '—'
    : value.toLocaleString('en-US', {
        minimumFractionDigits: priceDigits(symbol),
        maximumFractionDigits: priceDigits(symbol),
      });

export const trendText: Record<string, string> = {
  up: 'Tăng',
  down: 'Giảm',
  long: 'Mua',
  short: 'Bán',
};

export const qualityText: Record<string, string> = {
  checked: 'Đã kiểm tra',
  ok: 'Đạt',
  warning: 'Có cảnh báo',
  failed: 'Không đạt',
  pending: 'Đang kiểm tra',
};

export const provenanceText: Record<string, string> = {
  live: 'Trực tiếp',
  catch_up: 'Chạy bù',
  historical: 'Lịch sử',
};

export type Tone = 'ok' | 'warn' | 'bad' | 'idle';

export interface ForexMarketSession {
  status: 'open' | 'closed' | 'unknown';
  reason: string;
  as_of: number | null;
  observed_at: number | null;
  next_open_at: number | null;
  next_close_at: number | null;
  source: string;
}

const sessionReasonText: Record<string, string> = {
  missing_schedule: 'Chưa có lịch giao dịch từ Exness',
  invalid_schedule: 'Lịch Exness không hợp lệ',
  overlapping_schedule: 'Lịch Exness có khoảng chồng lấn',
  uncovered_time: 'Thời điểm hiện tại nằm ngoài lịch Exness',
  stale_schedule: 'Lịch Exness đã quá hạn làm mới',
  invalid_timestamp: 'Mốc cập nhật lịch không hợp lệ',
  identity_mismatch: 'Lịch không khớp cặp hoặc tài khoản hiện tại',
  sync_error: 'Đồng bộ thông số Exness đang lỗi',
};

export function marketSessionDisplay(
  session: ForexMarketSession | null | undefined,
  now = Date.now(),
  unverified = false,
): {
  status: 'open' | 'closed' | 'unknown';
  tone: Tone;
  label: string;
  transition: string;
  tooltip: string;
} {
  const nowSec = now / 1000;
  const observed = session?.observed_at ?? null;
  const expired =
    unverified || !session || observed == null || observed > nowSec || nowSec - observed >= 3600;
  const nextOpen =
    session?.next_open_at != null && session.next_open_at > nowSec ? session.next_open_at : null;
  const nextClose =
    session?.next_close_at != null && session.next_close_at > nowSec ? session.next_close_at : null;
  const current =
    expired || session.status === 'unknown'
      ? 'unknown'
      : session.status === 'open' && nextClose == null && session.next_close_at != null
        ? 'unknown'
        : session.status === 'closed' && nextOpen == null && session.next_open_at != null
          ? 'unknown'
          : session.status;
  const label =
    current === 'open'
      ? 'Đang mở'
      : current === 'closed'
        ? session?.reason === 'weekend'
          ? 'Đóng cuối tuần'
          : 'Nghỉ phiên'
        : 'Chưa xác minh';
  const transition =
    current === 'open' && nextClose
      ? `Nghỉ dự kiến ${formatTime(nextClose)} (${relativeTime(nextClose, now)})`
      : current === 'closed' && nextOpen
        ? `Mở dự kiến ${formatTime(nextOpen)} (${relativeTime(nextOpen, now)})`
        : (sessionReasonText[
            expired && session?.status !== 'unknown'
              ? 'stale_schedule'
              : (session?.reason ?? 'missing_schedule')
          ] ?? 'Chưa xác minh lịch giao dịch');
  const observedText = observed ? `Cập nhật lịch: ${formatTime(observed)}` : 'Chưa có mốc cập nhật';
  return {
    status: current,
    tone: current === 'open' ? 'ok' : 'idle',
    label,
    transition,
    tooltip: `${label} · ${transition} · ${observedText} · Theo lịch Exness · giờ thực tế có thể thay đổi`,
  };
}

interface StatusInput {
  configured: boolean;
  enabled: boolean;
  worker_online: boolean;
  error: string | null;
  error_msg?: string | null;
  error_at?: number | null;
  error_symbol?: string | null;
  learning?: { execution_v2?: { blocked_reason: string | null } };
}

/** Một trạng thái bot duy nhất (thay cho nhiều nhãn rời rạc): tiêu đề, lý do, việc cần làm. */
export function botStatus(
  data: StatusInput | null,
  unverified: boolean,
): { tone: Tone; title: string; reason: string; hint?: string } {
  if (unverified || !data)
    return {
      tone: 'idle',
      title: 'Chưa xác minh trạng thái bot',
      reason: 'Không tải được trạng thái thực thi; số liệu có thể đã cũ.',
    };
  if (!data.configured)
    return {
      tone: 'warn',
      title: 'Chỉ quan sát · chưa bật thực thi',
      reason: 'Executor Exness demo chưa được cấu hình nên bot không gửi lệnh.',
      hint: 'Đặt EXNESS_DEMO_EXECUTION_ENABLED=true và EXNESS_ENVIRONMENT=demo rồi khởi động lại worker.',
    };
  if (!data.worker_online)
    return {
      tone: 'bad',
      title: 'Worker thực thi offline',
      reason: 'Không có heartbeat từ worker thực thi; bot không mở/đóng lệnh chủ động.',
      hint: 'Kiểm tra tiến trình worker. SL/TP đã đặt trên sàn vẫn có hiệu lực.',
    };
  const blocked = data.learning?.execution_v2?.blocked_reason;
  if (blocked === 'v2_execution_disabled' || blocked === 'exness_execution_disabled')
    return {
      tone: 'warn',
      title: 'Chỉ chạy shadow · không gửi lệnh',
      reason: blockText[blocked],
      hint: blockHint[blocked],
    };
  if (!data.enabled)
    return {
      tone: 'warn',
      title: 'Bot đang tạm dừng mở lệnh',
      reason: 'Vị thế đang mở vẫn được quản lý; không mở lệnh mới.',
      hint: blockHint.paused,
    };
  if (data.error) {
    const detail = data.error_msg || data.error;
    const scope = data.error_symbol || 'toàn tài khoản · chưa xác định cặp';
    const unavailable = /^ExnessError: Market execution unavailable for [A-Z]+$/.test(detail);
    return {
      tone: 'warn',
      title: 'Worker đang chạy · có cảnh báo thực thi',
      reason: unavailable
        ? 'Điều kiện broker chưa cho phép gửi lệnh market. Không phải lỗi feed nến.'
        : `Lỗi thực thi gần nhất: ${detail}`,
      hint: `${scope}${data.error_at != null ? ` · ${formatTime(data.error_at)}` : ''} · ${detail}. Kiểm tra kết nối và trạng thái phiên; không tự bật giao dịch khi broker chưa cho phép.`,
    };
  }
  return {
    tone: 'ok',
    title: 'Bot đang chạy',
    reason: 'Tự mở lệnh khi có cây cho tín hiệu hợp lệ.',
  };
}

/** Worker thực thi: khi chưa cấu hình thực thi, worker không chạy là bình thường (xám, không đỏ). */
export function executorState(data: { configured: boolean; worker_online: boolean } | null): {
  tone: Tone;
  text: string;
} {
  if (!data) return { tone: 'idle', text: 'Chưa xác minh' };
  if (!data.configured) return { tone: 'idle', text: 'Không chạy (chưa cấu hình)' };
  return data.worker_online ? { tone: 'ok', text: 'Online' } : { tone: 'bad', text: 'Offline' };
}
