import { timeZoneLabel, useTimeZone } from '../lib/time-zone';

/** Nút chuyển múi giờ hiển thị (UTC / giờ máy). Dữ liệu và bot luôn chạy theo UTC. */
export function TimeZoneToggle() {
  const [timeZone, setTimeZone] = useTimeZone();
  return (
    <div
      className="tz-toggle inline-flex shrink-0 items-center gap-0.5 rounded-md border border-border-base bg-surface-1 p-0.5"
      role="group"
      aria-label="Múi giờ hiển thị"
      title="Dữ liệu và bot luôn chạy theo UTC; chỉ đổi cách hiển thị"
    >
      {(['utc', 'local'] as const).map((value) => (
        <button
          key={value}
          type="button"
          className={`rounded border-0 px-2 py-1 text-xs transition-colors ${timeZone === value ? 'bg-surface-3 text-text-primary' : 'bg-transparent text-text-muted hover:text-text-primary'}`}
          aria-pressed={timeZone === value}
          onClick={() => setTimeZone(value)}
        >
          {timeZoneLabel(value)}
        </button>
      ))}
    </div>
  );
}
