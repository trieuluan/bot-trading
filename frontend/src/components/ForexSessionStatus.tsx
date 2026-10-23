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
    <span className={`forex-session ${compact ? 'is-compact' : ''}`} title={view.tooltip}>
      <span className={`status-chip tone-${view.tone}`}>{view.label}</span>
      {!compact && <small>{view.transition}</small>}
    </span>
  );
}
