export const formatPrice = (v: number) =>
  v.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });

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
