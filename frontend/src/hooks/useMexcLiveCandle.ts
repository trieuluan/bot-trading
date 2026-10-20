import { getFuturesSymbol } from '../lib/futures-symbol';
import { useEffect, useState } from 'react';
import type { Candle } from '../types/trading';
import { MEXC_STREAM_URL, parseMexcCandle } from '../lib/mexc-stream';

export function useMexcLiveCandle(symbol = getFuturesSymbol()) {
  const [tick, setTick] = useState<{ candle: Candle; receivedAt: number } | null>(null);
  const [connected, setConnected] = useState(false);
  const [clock, setClock] = useState(Date.now());
  useEffect(() => {
    let active = true;
    let socket: WebSocket | null = null;
    let retry: ReturnType<typeof setTimeout>;
    let heartbeat: ReturnType<typeof setInterval>;
    let delay = 1000;
    let lastMessage = Date.now();
    const connect = () => {
      if (!active) return;
      setConnected(false);
      setTick(null);
      lastMessage = Date.now();
      try {
        socket = new WebSocket(MEXC_STREAM_URL);
      } catch {
        retry = setTimeout(connect, delay);
        delay = Math.min(delay * 2, 30_000);
        return;
      }
      socket.onopen = () => {
        socket?.send(JSON.stringify({ method: 'sub.kline', param: { symbol, interval: 'Min15' } }));
      };
      socket.onmessage = (event) => {
        if (!active || typeof event.data !== 'string') return;
        const candle = parseMexcCandle(event.data, Date.now(), symbol);
        if (!candle) return;
        lastMessage = Date.now();
        delay = 1000;
        setTick({ candle, receivedAt: lastMessage });
        setConnected(true);
      };
      socket.onerror = () => socket?.close();
      socket.onclose = () => {
        setConnected(false);
        clearInterval(heartbeat);
        if (active) {
          retry = setTimeout(connect, delay);
          delay = Math.min(delay * 2, 30_000);
        }
      };
      heartbeat = setInterval(() => {
        if (Date.now() - lastMessage > 30_000) socket?.close();
        else if (socket?.readyState === WebSocket.OPEN)
          socket.send(JSON.stringify({ method: 'ping' }));
      }, 10_000);
    };
    connect();
    const timer = setInterval(() => setClock(Date.now()), 1000);
    return () => {
      active = false;
      clearTimeout(retry);
      clearInterval(heartbeat);
      clearInterval(timer);
      if (socket) {
        socket.onopen = socket.onmessage = socket.onerror = socket.onclose = null;
        socket.close();
      }
    };
  }, [symbol]);
  const live =
    connected &&
    tick !== null &&
    clock - tick.receivedAt < 30_000 &&
    tick.candle.time === Math.floor(clock / 900_000) * 900;
  return { candle: live ? tick.candle : null, live, receivedAt: tick?.receivedAt ?? null };
}
