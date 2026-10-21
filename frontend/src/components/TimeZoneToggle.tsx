import { timeZoneLabel, useTimeZone } from '../lib/time-zone';

/** Nút chuyển múi giờ hiển thị (UTC / giờ máy). Dữ liệu và bot luôn chạy theo UTC. */
export function TimeZoneToggle() {
  const [timeZone, setTimeZone] = useTimeZone();
  return (
    <div
      className="tz-toggle"
      role="group"
      aria-label="Múi giờ hiển thị"
      title="Dữ liệu và bot luôn chạy theo UTC; chỉ đổi cách hiển thị"
    >
      {(['utc', 'local'] as const).map((value) => (
        <button
          key={value}
          type="button"
          aria-pressed={timeZone === value}
          onClick={() => setTimeZone(value)}
        >
          {timeZoneLabel(value)}
        </button>
      ))}
    </div>
  );
}
