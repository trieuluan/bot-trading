// Nhãn và định dạng dùng chung cho workspace Exness Forex.

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

const pad = (value: number) => String(value).padStart(2, '0');

/** Giờ UTC thống nhất: "21/10 06:45 UTC" (thêm năm khi khác năm hiện tại). */
export function utcTime(value: number | string | null | undefined, withSeconds = false) {
  if (value == null) return '—';
  const date = typeof value === 'number' ? new Date(value * 1000) : new Date(value);
  if (Number.isNaN(date.getTime())) return '—';
  const year =
    date.getUTCFullYear() === new Date().getUTCFullYear() ? '' : `/${date.getUTCFullYear()}`;
  const seconds = withSeconds ? `:${pad(date.getUTCSeconds())}` : '';
  return `${pad(date.getUTCDate())}/${pad(date.getUTCMonth() + 1)}${year} ${pad(date.getUTCHours())}:${pad(date.getUTCMinutes())}${seconds} UTC`;
}

/** "2 phút trước", "còn 8 phút". */
export function relativeTime(value: number | string | null | undefined, now = Date.now()) {
  if (value == null) return '—';
  const ms = typeof value === 'number' ? value * 1000 : new Date(value).getTime();
  if (Number.isNaN(ms)) return '—';
  const diff = Math.round((ms - now) / 1000);
  const abs = Math.abs(diff);
  const text =
    abs < 60
      ? `${abs} giây`
      : abs < 3600
        ? `${Math.round(abs / 60)} phút`
        : abs < 86400
          ? `${Math.round(abs / 3600)} giờ`
          : `${Math.round(abs / 86400)} ngày`;
  if (abs < 5) return 'vừa xong';
  return diff > 0 ? `còn ${text}` : `${text} trước`;
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

interface StatusInput {
  configured: boolean;
  enabled: boolean;
  worker_online: boolean;
  error: string | null;
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
  return {
    tone: data.error ? 'warn' : 'ok',
    title: 'Bot đang chạy',
    reason: data.error
      ? `Lỗi gần nhất: ${data.error}`
      : 'Tự mở lệnh khi có cây cho tín hiệu hợp lệ.',
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
