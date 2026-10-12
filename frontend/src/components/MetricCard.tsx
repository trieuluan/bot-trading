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
    <section className="metric">
      <div className="metric-label">
        {title}
        {icon}
      </div>
      <div className="metric-value">{value}</div>
      <p className={positive ? 'positive' : ''}>
        {positive && <ArrowUpRight size={13} />} {detail}
      </p>
    </section>
  );
}
