type Handlers = Record<string, (data: unknown) => void | boolean>;

/** SSE Futures dùng chung snapshot server; tự mở lại khi trình duyệt đóng hẳn (401/5xx). */
export function subscribeFuturesStream(
  query: string,
  handlers: Handlers,
  onStatus: (online: boolean) => void,
  fallback: () => Promise<Record<string, unknown>>,
) {
  let source: EventSource | null = null;
  let retry: ReturnType<typeof setTimeout> | undefined;
  let closed = false;
  let lastEvent = Date.now();
  let revision = 0;
  let polling = false;
  let retryDelay = 5000;
  const open = () => {
    source = new EventSource(`/api/workspaces/mexc_futures/stream?${query}`);
    for (const [name, handle] of Object.entries(handlers))
      source.addEventListener(name, (event) => {
        if (closed) return;
        try {
          const valid = handle(JSON.parse((event as MessageEvent<string>).data));
          if (valid === false) return;
          lastEvent = Date.now();
          revision++;
          retryDelay = 5000;
          onStatus(true);
        } catch {
          onStatus(false);
        }
      });
    source.addEventListener('unavailable', () => onStatus(false));
    source.onerror = () => {
      onStatus(false);
      if (source?.readyState === EventSource.CLOSED && !closed) {
        source.close();
        retry = setTimeout(open, retryDelay);
        retryDelay = Math.min(30000, retryDelay * 2);
      }
    };
  };
  open();
  const watchdog = setInterval(async () => {
    if (closed || polling || Date.now() - lastEvent < 20000) return;
    polling = true;
    const version = revision;
    try {
      const updates = await fallback();
      if (closed || version !== revision) return;
      for (const [name, data] of Object.entries(updates)) handlers[name]?.(data);
      onStatus(true);
    } catch {
      if (!closed && version === revision) onStatus(false);
    } finally {
      polling = false;
    }
  }, 10000);
  return () => {
    closed = true;
    clearTimeout(retry);
    clearInterval(watchdog);
    source?.close();
  };
}
