export const formatPrice = (v: number) =>
  v.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });

export const formatForexPrice = (v: number) =>
  v.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 10 });

export function formatForexLeverage(profile?: {
  calc_mode?: string;
  account_leverage?: string;
  margin_percent?: string;
} | null) {
  const leveraged = ['forex', 'cfd_leverage'].includes(profile?.calc_mode ?? '');
  const fixed = ['forex_no_leverage', 'cfd'].includes(profile?.calc_mode ?? '');
  const value = Number(leveraged ? profile?.account_leverage : profile?.margin_percent);
  if ((!leveraged && !fixed) || !Number.isFinite(value) || value <= 0) return '—';
  const leverage = leveraged ? value : 1 / value;
  return `1:${leverage.toLocaleString('vi-VN', { maximumFractionDigits: 2 })}`;
}

export const formatQuote = (v: number) => {
  const digits = Math.abs(v) >= 100 ? 2 : Math.abs(v) >= 1 ? 3 : Math.abs(v) >= 0.1 ? 4 : 5;
  return v.toLocaleString('en-US', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
};

export const observationStatusLabels: Record<string, string> = {
  waiting_breakout: 'Đang chờ breakout 1h',
  waiting_retest: 'Đã breakout 1h · Chờ retest 15m',
  waiting_confirm: 'Đã retest 15m · Chờ nến xác nhận giữ vùng',
  waiting_pullback: 'Chờ M15 pullback chạm EMA20',
  waiting_range_rejection: 'Chờ M15 từ chối biên range',
  waiting_regime_setup: 'Chờ setup theo trạng thái thị trường',
  conflicting_signals: 'Tín hiệu ngược chiều; bỏ qua',
  confirmed: 'Đã xác nhận giữ vùng (chỉ quan sát)',
  cancelled: 'Đã hủy setup',
  expired: 'Hết hạn thời gian chờ',
  warming_up: 'Đang khởi động',
  data_paused: 'Tạm dừng do dữ liệu',
  catching_up: 'Đang chạy bù MTF',
  not_started: 'Chưa bắt đầu',
};

/** Dịch lỗi dữ liệu MEXC từ backend (tiếng Anh) sang tiếng Việt; giữ nguyên lỗi lạ. */
export function mexcErrorText(error: string | null | undefined) {
  if (!error) return null;
  if (/rate limit/i.test(error)) return 'MEXC giới hạn tần suất, đang chờ thử lại';
  if (/unsuccessful response/i.test(error)) return 'MEXC trả về phản hồi lỗi';
  if (/cannot reach/i.test(error)) return 'Không kết nối được MEXC (đã thử 3 lần)';
  if (/rejected the request|invalid json/i.test(error))
    return 'MEXC từ chối yêu cầu hoặc trả dữ liệu không hợp lệ';
  return error;
}
