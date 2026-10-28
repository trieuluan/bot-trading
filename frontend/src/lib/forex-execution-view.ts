import type { BrokerPosition } from '../types/exness-execution';
import type { ForexEntryIntent } from '../types/forex-entry';

export interface ExecutionLeg {
  positionId: string;
  role: string;
  lots: number | null;
  price: number;
  status: 'open' | 'closed' | 'unknown';
  pnl: number | null;
  position?: BrokerPosition;
}

export interface ExecutionTimelineStep {
  label: string;
  time: number | null;
  detail?: string;
  done: boolean;
  missingTime: boolean;
}

const ROLE: Record<string, string> = { tp1: 'TP1', runner: 'Runner', partial1: 'Chốt phần 1', partial2: 'Chốt phần 2' };

export function executionLegs(
  intent: ForexEntryIntent,
  positions: BrokerPosition[],
  pnlOf: (position: BrokerPosition) => number | null,
): ExecutionLeg[] {
  return (intent.broker_fills ?? []).map((fill) => {
    const position = positions.find((row) => row.position_id === fill.position_id);
    const pnl = position ? (position.status === 'closed' ? position.pnl : pnlOf(position)) : null;
    return {
      positionId: fill.position_id,
      role: position?.role ? ROLE[position.role] ?? 'Chưa rõ vai trò' : 'Chưa rõ vai trò',
      lots: position?.initial_volume ?? position?.volume ?? null,
      price: fill.price,
      status: !position ? 'unknown' : position.status === 'open' ? 'open' : position.status === 'closed' ? 'closed' : 'unknown',
      pnl: pnl != null && Number.isFinite(pnl) ? pnl : null,
      position,
    };
  });
}

export function executionSummary(legs: ExecutionLeg[]) {
  const lots = legs.reduce<number | null>((sum, leg) => (sum == null || leg.lots == null ? null : sum + leg.lots), 0);
  const weighted = lots && legs.every((leg) => leg.lots != null)
    ? legs.reduce((sum, leg) => sum + leg.price * (leg.lots ?? 0), 0) / lots
    : legs.length === 1 ? legs[0].price : null;
  const pnls = legs.map((leg) => leg.pnl);
  const pnl = legs.length && pnls.every((value): value is number => value != null) ? pnls.reduce((a, b) => a + b, 0) : null;
  const open = legs.filter((leg) => leg.status === 'open').length;
  return { lots: legs.length ? lots : null, averagePrice: weighted, pnl, open, closed: legs.filter((leg) => leg.status === 'closed').length };
}

export function brokerFillsDiagnosis(intent?: ForexEntryIntent, legs: ExecutionLeg[] = []) {
  if (!intent) return null;
  const fills = intent.broker_fills ?? [];
  const roles = legs.map((l) => l.role.toLowerCase());
  const hasTp1 = roles.some((r) => r.includes('tp1') || r.includes('phần 1'));
  const hasRunner = roles.some((r) => r.includes('runner'));
  const openCount = legs.filter((l) => l.status === 'open').length;
  const closedCount = legs.filter((l) => l.status === 'closed').length;

  let text = '';
  let subtext = '';
  let isPartialFill = false;

  if (fills.length === 0) {
    text = 'Chưa có lệnh broker khớp';
  } else if (hasTp1 && !hasRunner && fills.length === 1) {
    isPartialFill = true;
    text = 'Chỉ khớp TP1 (thiếu runner)';
    subtext = closedCount === 1 ? 'TP1 đã đóng · broker kết thúc' : 'TP1 đang mở · không có runner';
  } else if (openCount > 0) {
    text = `${openCount} vị thế đang mở`;
    if (closedCount > 0) subtext = `${closedCount} vị thế đã đóng`;
  } else if (closedCount === legs.length && legs.length > 0) {
    text = `Đã đóng hết (${closedCount} vị thế)`;
  } else {
    text = `${fills.length} lệnh broker đã khớp`;
  }

  return { text, subtext, isPartialFill, openCount, closedCount };
}

/** Shadow chỉ cập nhật theo nến M15; broker đóng trước thì báo đang đối chiếu thay vì "đang mở". */
export function shadowStatusText(status: string, brokerClosed: boolean, labels: Record<string, string>) {
  if (brokerClosed && ['open', 'pending', 'waiting'].includes(status)) return 'Đang đối chiếu · cập nhật theo nến M15';
  return labels[status] ?? status;
}

export function entrySlippage(intent: ForexEntryIntent, averagePrice: number | null): number | null {
  if (averagePrice == null) return null;
  const direction = intent.side === 'long' ? 1 : -1;
  const reference = intent.winner === 'limit' ? intent.limit_price
    : intent.winner === 'stop' ? intent.stop_price
      : intent.winner === 'market' ? (intent.side === 'long' ? intent.trigger_quote?.ask : intent.trigger_quote?.bid) : null;
  return reference == null || !Number.isFinite(reference) ? null : (averagePrice - reference) * direction;
}

export function executionTimeline(intent: ForexEntryIntent, legs: ExecutionLeg[]): (ExecutionTimelineStep & { gap: number | null })[] {
  const terminal = ['cancelled', 'expired', 'completed'].includes(intent.status);
  const sent = legs.length > 0 || ['submitted', 'completed', 'uncertain'].includes(intent.status);
  const steps: ExecutionTimelineStep[] = [
    { label: 'Tín hiệu', time: intent.available_at ?? null, done: intent.available_at != null, missingTime: intent.available_at == null },
    { label: 'Canh giá', time: intent.created_at ?? null, done: intent.created_at != null, missingTime: intent.created_at == null },
  ];
  if (intent.triggered_at != null || sent) {
    steps.push({ label: 'Chạm điều kiện', time: intent.triggered_at ?? null, detail: intent.winner?.toUpperCase(), done: intent.triggered_at != null,
      missingTime: intent.triggered_at == null });
  }
  const firstOf = (key: 'submitted_at' | 'filled_at') => {
    const times = (intent.broker_fills ?? []).map((fill) => fill[key]).filter((v): v is number => typeof v === 'number');
    return times.length ? Math.min(...times) : null;
  };
  const submittedAt = firstOf('submitted_at');
  const filledAt = firstOf('filled_at');
  if (sent) steps.push({ label: 'Gửi lệnh', time: submittedAt, detail: submittedAt == null ? 'Không có giờ ghi nhận' : undefined,
    done: true, missingTime: submittedAt == null });
  if (sent) steps.push({ label: 'Broker khớp', time: filledAt, detail: legs.length ? `${legs.length} vị thế` : 'Chưa có fill xác nhận',
    done: legs.length > 0, missingTime: filledAt == null });
  if (terminal && !sent) steps.push({ label: intent.status === 'expired' ? 'Hết hạn' : 'Hủy canh', time: intent.ended_at ?? null,
    done: true, missingTime: intent.ended_at == null });
  if (sent) {
    const summary = executionSummary(legs);
    steps.push({ label: summary.open ? 'Đang mở' : 'Vị thế', time: null,
      detail: summary.open ? `${summary.open} đang mở${summary.closed ? ` · ${summary.closed} đã đóng` : ''}` : legs.length && summary.closed === legs.length ? 'Đã đóng hết' : 'Chưa rõ trạng thái',
      done: summary.closed === legs.length && legs.length > 0, missingTime: false });
  }
  let previous: number | null = null;
  return steps.map((step) => {
    const gap = step.time != null && previous != null ? Math.round(step.time - previous) : null;
    if (step.time != null) previous = step.time;
    return { ...step, gap };
  });
}
