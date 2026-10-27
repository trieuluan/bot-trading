import type { ReactNode } from 'react';
import { ArrowUpRight } from 'lucide-react';
export function MetricCard({
  title,
  value,
  detail,
  icon,
  positive = false,
}: {
  title: string;
  value: string;
  detail: string;
  icon: ReactNode;
  positive?: boolean;
}) {
  return (
    <section className="metric min-w-0 rounded-md border border-border-subtle bg-surface-1 p-3 [&_strong]:block [&_strong]:font-mono [&_strong]:text-lg [&_strong]:text-text-primary">
      <div className="metric-label mb-1 flex items-center gap-1.5 text-xs text-text-muted">
        {title}
        {icon}
      </div>
      <div className="metric-value font-mono text-xl font-semibold text-text-primary">{value}</div>
      <p className={positive ? "positive text-bull" : ''}>
        {positive && <ArrowUpRight size={13} />} {detail}
      </p>
    </section>
  );
}
