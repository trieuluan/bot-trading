import { useEffect, useState } from 'react';
import { marketSessionDisplay, type ForexMarketSession } from '../lib/forex-labels';

export function ForexSessionStatus({
  session,
  now,
  compact = false,
}: {
  session?: ForexMarketSession;
  now?: number;
  compact?: boolean;
}) {
  const [clock, setClock] = useState(Date.now());
  useEffect(() => {
    if (now !== undefined) return;
    const timer = window.setInterval(() => setClock(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [now]);
  const view = marketSessionDisplay(session, now ?? clock);
  return (
    <span className={`forex-session ${compact ? 'is-compact' : ''} flex min-w-0 flex-wrap items-center gap-2 text-xs [&.is-compact]:gap-1`} title={view.tooltip}>
      <span className={`status-chip tone-${view.tone} inline-flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 text-xs font-medium bg-surface-3 text-text-secondary`}>{view.label}</span>
      {!compact && <small>{view.transition}</small>}
    </span>
  );
}
