type Part = 'dashboard' | 'execution' | 'markets' | 'analysis';
type Listener = {
  part: Part;
  data: (value: unknown) => void;
  status: (online: boolean, connecting: boolean) => void;
};
type Channel = { symbol: string; offset: number; listeners: Set<Listener>; close: () => void };

const channels = new Map<string, Channel>();
const snapshots = new Map<string, unknown>();

/** One EventSource per symbol/page, shared by all mounted Forex consumers. No REST polling. */
export function subscribeForexStream(
  symbol: string,
  part: Part,
  data: Listener['data'],
  status: Listener['status'],
  offset = 0,
) {
  const key = `${symbol}:${offset}`;
  let channel = channels.get(key);
  if (!channel) {
    channel = { symbol, offset, listeners: new Set(), close: () => {} };
    channels.set(key, channel);
  }
  const listener = { part, data, status };
  channel.listeners.add(listener);
  const cached = snapshots.get(`${key}:${part}`);
  if (cached !== undefined) data(cached);
  connect(channel, key);
  return () => {
    channel.listeners.delete(listener);
    channel.close();
    if (channel.listeners.size) connect(channel, key);
    else channels.delete(key);
  };
}

function connect(channel: Channel, key: string) {
  channel.close();
  let source: EventSource | undefined;
  let retry: ReturnType<typeof setTimeout> | undefined;
  let disposed = false;
  let last = Date.now();
  let backoff = 3000;
  const failed = new Set<Part>();
  const ready = new Set<Part>();
  const report = (online: boolean, connecting = false) => {
    channel.listeners.forEach((listener) =>
      listener.status(
        online && ready.has(listener.part) && !failed.has(listener.part),
        !failed.has(listener.part) && (connecting || (online && !ready.has(listener.part))),
      ),
    );
  };
  report(false, true);
  const open = () => {
    if (disposed || document.hidden) return;
    const parts = [...new Set([...channel.listeners].map((listener) => listener.part))].sort();
    source = new EventSource(
      `/api/workspaces/exness_forex/stream?${new URLSearchParams({ symbol: channel.symbol, parts: parts.join(','), offset: String(channel.offset) })}`,
    );
    const current = source;
    const received = () => {
      last = Date.now();
      backoff = 3000;
      report(true);
    };
    for (const part of parts) {
      current.addEventListener(part, (event) => {
        if (disposed || current !== source) return;
        try {
          const body = JSON.parse((event as MessageEvent<string>).data);
          if (body.symbol !== channel.symbol) return;
          failed.delete(part);
          ready.add(part);
          snapshots.set(`${key}:${part}`, body.data);
          if (snapshots.size > 128) snapshots.delete(snapshots.keys().next().value!);
          channel.listeners.forEach((listener) => {
            if (listener.part === part) listener.data(body.data);
          });
          received();
        } catch {
          failed.add(part);
          report(false);
        }
      });
    }
    current.addEventListener('heartbeat', received);
    current.addEventListener('unavailable', (event) => {
      if (disposed || current !== source) return;
      try {
        failed.add(JSON.parse((event as MessageEvent<string>).data).part as Part);
      } catch {
        report(false);
        return;
      }
      received();
    });
    current.onerror = () => {
      if (disposed || current !== source) return;
      report(false);
      if (current.readyState === EventSource.CLOSED) schedule();
    };
  };
  const schedule = () => {
    source?.close();
    source = undefined;
    clearTimeout(retry);
    retry = setTimeout(open, backoff);
    backoff = Math.min(30000, backoff * 2);
  };
  const visibility = () => {
    clearTimeout(retry);
    source?.close();
    source = undefined;
    report(false, true);
    if (!document.hidden) {
      last = Date.now();
      schedule();
    }
  };
  document.addEventListener('visibilitychange', visibility);
  const watchdog = setInterval(() => {
    if (!document.hidden && source && Date.now() - last > 30000) {
      report(false);
      last = Date.now();
      schedule();
    }
  }, 5000);
  retry = setTimeout(open, 200);
  channel.close = () => {
    disposed = true;
    clearTimeout(retry);
    clearInterval(watchdog);
    document.removeEventListener('visibilitychange', visibility);
    source?.close();
  };
}
