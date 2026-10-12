export const formatPrice = (v: number) =>
  v.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });

export const observationStatusLabels: Record<string, string> = {
  waiting_breakout: 'Đang chờ breakout 1h',
  waiting_retest: 'Đã breakout 1h · Chờ retest 15m',
  waiting_confirm: 'Đã retest 15m · Chờ nến xác nhận giữ vùng',
  confirmed: 'Đã xác nhận giữ vùng (chỉ quan sát)',
  cancelled: 'Đã hủy setup',
  expired: 'Hết hạn thời gian chờ',
};
