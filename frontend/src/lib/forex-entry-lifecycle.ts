import type { ForexEntryIntent, ForexRecordedSignal } from '../types/forex-entry';

export interface ForexSignalSetup {
  id: string;
  time: number;
  side: 'long' | 'short';
  intents: ForexEntryIntent[];
  signals: ForexRecordedSignal[];
}

export function signalSetups(intents: ForexEntryIntent[], signals: ForexRecordedSignal[], now = Date.now() / 1000) {
  const groups = new Map<string, ForexSignalSetup>();
  for (const intent of intents) {
    const time = intent.available_at ?? intent.created_at;
    if (time == null || !Number.isFinite(time) || time > now) continue;
    const id = `setup:${intent.setup_id ?? intent.id}:${time}:${intent.side}`;
    const group = groups.get(id) ?? { id, time, side: intent.side, intents: [], signals: [] };
    group.intents.push(intent);
    groups.set(id, group);
  }
  for (const signal of signals) {
    if (!Number.isFinite(signal.available_at) || signal.available_at > now) continue;
    const matching = [...groups.values()].filter((group) => group.time === signal.available_at &&
      group.side === signal.side && group.intents.some((intent) => intent.strategy === signal.strategy));
    const id = `signals:${signal.available_at}:${signal.side}:${signal.strategy.startsWith('hast4_') ? 'v4' : 'v3'}`;
    const targets = matching.length ? matching : [groups.get(id) ?? {
      id, time: signal.available_at, side: signal.side, intents: [], signals: [],
    }];
    for (const group of targets) {
      if (!group.signals.some((recorded) => recorded.strategy === signal.strategy)) group.signals.push(signal);
      groups.set(group.id, group);
    }
  }
  for (const group of groups.values()) {
    for (const intent of group.intents) {
      if (!group.signals.some((signal) => signal.strategy === intent.strategy))
        group.signals.push({ strategy: intent.strategy, side: intent.side, available_at: group.time, status: 'recorded' });
    }
  }
  return [...groups.values()].sort((left, right) => right.time - left.time || left.id.localeCompare(right.id));
}

export function entryLifecycle(intent: ForexEntryIntent) {
  const filled = !!intent.broker_fills?.length;
  switch (intent.status) {
    case 'waiting':
      return { label: 'Canh giá', short: 'Canh', tone: 'waiting' };
    case 'triggered':
    case 'submitting':
      return { label: 'Đã kích hoạt · đang gửi', short: 'Kích', tone: 'triggered' };
    case 'uncertain':
      return { label: 'Chưa rõ kết quả · cần đối soát', short: 'Đối soát', tone: 'uncertain' };
    case 'submitted':
      return filled
        ? { label: 'Broker đã xác nhận khớp', short: 'Khớp', tone: 'filled' }
        : { label: 'Đã gửi · chờ broker xác nhận khớp', short: 'Đã gửi', tone: 'triggered' };
    case 'completed':
      return filled
        ? { label: 'Đã khớp · vòng đời lệnh kết thúc', short: 'Đã xong', tone: 'ended' }
        : { label: 'Kết thúc · không có fill xác nhận', short: 'Kết thúc', tone: 'ended' };
    case 'cancelled':
      return { label: 'Đã hủy canh giá', short: 'Hủy', tone: 'cancelled' };
    case 'expired':
      return { label: 'Hết hạn canh giá', short: 'Hết hạn', tone: 'cancelled' };
    default:
      return { label: intent.status, short: intent.status, tone: 'ended' };
  }
}

export function visibleEntryIntents(intents: ForexEntryIntent[], now: number) {
  return intents.filter((intent) => {
    if ((intent.available_at ?? intent.created_at ?? 0) > now) return false;
    if (['waiting', 'triggered', 'submitting', 'submitted', 'uncertain'].includes(intent.status))
      return true;
    const ended = intent.ended_at ?? intent.triggered_at ?? intent.available_at;
    return ended != null && ended <= now && now - ended <= 3600;
  });
}

export function intentEvents(intent: ForexEntryIntent) {
  const side = intent.side === 'long' ? 'MUA' : 'BÁN';
  const events = [
    { time: intent.available_at ?? intent.created_at, text: `CANH ${side} · ẢO`, color: '#138b79' },
  ];
  if (intent.triggered_at != null)
    events.push({
      time: intent.triggered_at,
      text: `KÍCH ${intent.winner?.toUpperCase() ?? ''} ${side} · ẢO`,
      color: '#cb8428',
    });
  if (['cancelled', 'expired'].includes(intent.status) && intent.ended_at != null)
    events.push({
      time: intent.ended_at,
      text: `${intent.status === 'expired' ? 'HẾT HẠN' : 'HỦY'} ${side} · ẢO`,
      color: '#bd7164',
    });
  return events.filter((event): event is typeof event & { time: number } =>
    Number.isFinite(event.time),
  );
}
