type Handlers = Record<string, (data: unknown) => void>;

/** SSE Futures dùng chung snapshot server; tự mở lại khi trình duyệt đóng hẳn (401/5xx). */
export function subscribeFuturesStream(
  query: string,
  handlers: Handlers,
  onStatus: (online: boolean) => void,
) {
  let source: EventSource | null = null;
  let retry: ReturnType<typeof setTimeout> | undefined;
  let closed = false;
  const open = () => {
    source = new EventSource(`/api/workspaces/mexc_futures/stream?${query}`);
    for (const [name, handle] of Object.entries(handlers))
      source.addEventListener(name, (event) => {
        handle(JSON.parse((event as MessageEvent<string>).data));
        onStatus(true);
      });
    source.addEventListener('unavailable', () => onStatus(false));
    source.onerror = () => {
      onStatus(false);
      if (source?.readyState === EventSource.CLOSED && !closed) {
        source.close();
        retry = setTimeout(open, 5000);
      }
    };
  };
  open();
  return () => {
    closed = true;
    clearTimeout(retry);
    source?.close();
  };
}
