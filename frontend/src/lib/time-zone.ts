import { useSyncExternalStore } from 'react';

// Múi giờ hiển thị cho workspace Forex. Dữ liệu, API và logic bot luôn là UTC;
// lựa chọn này chỉ đổi cách hiển thị (chart, nhật ký, bảng lệnh…). Mặc định UTC
// để khớp giờ server Exness (GMT+0) và nến H4 00/04/08… UTC.

export type TimeZoneMode = 'utc' | 'local';

const KEY = 'signaldesk.timezone';
const listeners = new Set<() => void>();

function read(): TimeZoneMode {
  try {
    return localStorage.getItem(KEY) === 'local' ? 'local' : 'utc';
  } catch {
    return 'utc';
  }
}

let mode: TimeZoneMode = read();

export const getTimeZone = () => mode;

export function setTimeZone(next: TimeZoneMode) {
  if (next === mode) return;
  mode = next;
  try {
    localStorage.setItem(KEY, next);
  } catch {
    // Không lưu được (private mode…): vẫn đổi trong phiên hiện tại.
  }
  listeners.forEach((listener) => listener());
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function useTimeZone(): [TimeZoneMode, typeof setTimeZone] {
  return [useSyncExternalStore(subscribe, getTimeZone), setTimeZone];
}

/** Nhãn múi giờ: "UTC" hoặc "GMT+7" theo máy người xem. */
export function timeZoneLabel(which: TimeZoneMode = mode) {
  if (which === 'utc') return 'UTC';
  const offset = -new Date().getTimezoneOffset();
  const sign = offset >= 0 ? '+' : '-';
  const hours = Math.floor(Math.abs(offset) / 60);
  const minutes = Math.abs(offset) % 60;
  return `GMT${sign}${hours}${minutes ? `:${String(minutes).padStart(2, '0')}` : ''}`;
}

/** Thành phần ngày giờ theo múi giờ đang chọn. */
export function timeParts(date: Date, which: TimeZoneMode = mode) {
  const utc = which === 'utc';
  return {
    year: utc ? date.getUTCFullYear() : date.getFullYear(),
    month: (utc ? date.getUTCMonth() : date.getMonth()) + 1,
    day: utc ? date.getUTCDate() : date.getDate(),
    hours: utc ? date.getUTCHours() : date.getHours(),
    minutes: utc ? date.getUTCMinutes() : date.getMinutes(),
    seconds: utc ? date.getUTCSeconds() : date.getSeconds(),
  };
}
