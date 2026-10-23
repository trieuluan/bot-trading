// Định dạng giờ dùng chung cho mọi workspace, theo múi giờ hiển thị đang chọn.
import { timeParts, timeZoneLabel } from './time-zone.ts';

const pad = (value: number) => String(value).padStart(2, '0');

/**
 * Giờ hiển thị theo múi giờ đang chọn (UTC mặc định), luôn kèm nhãn:
 * "21/10 06:45 UTC" / "21/10 13:45 GMT+7". Thêm năm khi khác năm hiện tại.
 */
export function formatTime(
  value: number | string | null | undefined,
  withSeconds = false,
  withLabel = true,
) {
  if (value == null) return '—';
  const date = typeof value === 'number' ? new Date(value * 1000) : new Date(value);
  if (Number.isNaN(date.getTime())) return '—';
  const t = timeParts(date);
  const year = t.year === timeParts(new Date()).year ? '' : `/${t.year}`;
  const seconds = withSeconds ? `:${pad(t.seconds)}` : '';
  const label = withLabel ? ` ${timeZoneLabel()}` : '';
  return `${pad(t.day)}/${pad(t.month)}${year} ${pad(t.hours)}:${pad(t.minutes)}${seconds}${label}`;
}

/** Chỉ giờ:phút theo múi giờ đang chọn, kèm nhãn: "06:00 UTC". */
export function clockTime(value: number, withLabel = true) {
  const t = timeParts(new Date(value * 1000));
  return `${pad(t.hours)}:${pad(t.minutes)}${withLabel ? ` ${timeZoneLabel()}` : ''}`;
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
