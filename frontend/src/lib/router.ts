import { useSyncExternalStore } from 'react';

const pages = {
  mexc_futures: ['Giao dịch & Bot', 'Cài đặt & Kết nối'],
  exness_forex: ['Tổng quan', 'Bot', 'Nghiên cứu', 'Kết nối sàn'],
};

const tabs: Record<string, string[]> = {
  Bot: ['chart', 'orders', 'learning'],
  'Nghiên cứu': ['backtest', 'paper', 'log'],
};

export interface RouteState {
  workspace: string;
  page: string;
  tab?: string;
  symbol?: string;
}

export function parseRoute(search = window.location.search): RouteState {
  const params = new URLSearchParams(search);
  const workspace = params.get('workspace') === 'exness_forex' ? 'exness_forex' : 'mexc_futures';
  const allowedPages = pages[workspace];
  const page = allowedPages.find((value) => value === params.get('page')) ?? allowedPages[0];
  const tab =
    workspace === 'exness_forex'
      ? tabs[page]?.find((value) => value === params.get('tab'))
      : undefined;
  const symbol = workspace === 'exness_forex' ? params.get('symbol') || 'XAUUSD' : undefined;
  return { workspace, page, tab, symbol };
}

export function setRoute(update: Partial<RouteState>, options: { replace?: boolean } = {}) {
  const current = parseRoute();
  const workspaceChanged = update.workspace !== undefined && update.workspace !== current.workspace;
  const next: RouteState = {
    workspace: update.workspace !== undefined ? update.workspace : current.workspace,
    page: update.page ?? (workspaceChanged ? '' : current.page),
    symbol: update.symbol ?? current.symbol,
    tab:
      'tab' in update
        ? update.tab
        : workspaceChanged || update.page !== undefined
          ? undefined
          : current.tab,
  };

  const params = new URLSearchParams(window.location.search);
  for (const key of ['workspace', 'page', 'tab', 'symbol'] as const) {
    params.delete(key);
    if (next[key]) params.set(key, next[key]);
  }
  const valid = parseRoute(params.toString());
  params.set('workspace', valid.workspace);
  params.set('page', valid.page);
  if (valid.tab) params.set('tab', valid.tab);
  else params.delete('tab');
  if (valid.symbol) params.set('symbol', valid.symbol);
  else params.delete('symbol');

  const query = params.toString();
  const nextUrl = `${window.location.pathname}?${query}${window.location.hash}`;

  if (window.location.search !== (query ? `?${query}` : '')) {
    if (options.replace) {
      window.history.replaceState(null, '', nextUrl);
    } else {
      window.history.pushState(null, '', nextUrl);
    }
    window.dispatchEvent(new Event('app:routechange'));
  }
}

function subscribe(onChange: () => void) {
  window.addEventListener('popstate', onChange);
  window.addEventListener('app:routechange', onChange);
  return () => {
    window.removeEventListener('popstate', onChange);
    window.removeEventListener('app:routechange', onChange);
  };
}

export function useRoute(): [RouteState, typeof setRoute] {
  const search = useSyncExternalStore(subscribe, () => window.location.search);
  return [parseRoute(search), setRoute];
}
