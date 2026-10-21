import { TickMarkType, type Time } from 'lightweight-charts';
import { formatTime } from './time-format';
import { timeParts, type TimeZoneMode } from './time-zone';

const pad = (value: number) => String(value).padStart(2, '0');

/**
 * Tuỳ chọn lightweight-charts để trục thời gian và crosshair hiển thị theo múi giờ đang chọn.
 * Dữ liệu nến vẫn là timestamp UTC; chỉ đổi nhãn.
 */
export function chartTimeOptions(mode: TimeZoneMode) {
  return {
    localization: {
      timeFormatter: (time: Time) =>
        typeof time === 'number' ? formatTime(time, false, false) : String(time),
    },
    timeScale: {
      tickMarkFormatter: (time: Time, type: TickMarkType) => {
        if (typeof time !== 'number') return null;
        const t = timeParts(new Date(time * 1000), mode);
        if (type === TickMarkType.Year) return String(t.year);
        if (type === TickMarkType.Month) return `Th${t.month}/${t.year}`;
        if (type === TickMarkType.DayOfMonth) return `${pad(t.day)}/${pad(t.month)}`;
        if (type === TickMarkType.TimeWithSeconds)
          return `${pad(t.hours)}:${pad(t.minutes)}:${pad(t.seconds)}`;
        return `${pad(t.hours)}:${pad(t.minutes)}`;
      },
    },
  };
}
