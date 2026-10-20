import assert from 'node:assert/strict';

// Test URL parse logic directly
const pages = {
  mexc_futures: ['Giao dịch & Bot', 'Cài đặt & Kết nối'],
  exness_forex: ['Tổng quan', 'Bot', 'Nghiên cứu', 'Kết nối sàn'],
};

const tabs = {
  Bot: ['chart', 'orders', 'learning'],
  'Nghiên cứu': ['backtest', 'paper', 'log'],
};

function parseRoute(search) {
  const params = new URLSearchParams(search);
  const workspace = params.get('workspace') === 'exness_forex' ? 'exness_forex' : 'mexc_futures';
  const allowedPages = pages[workspace];
  const page = allowedPages.find((v) => v === params.get('page')) ?? allowedPages[0];
  const tab =
    workspace === 'exness_forex' ? tabs[page]?.find((v) => v === params.get('tab')) : undefined;
  return { workspace, page, tab };
}

// 1. Default empty url
assert.deepEqual(parseRoute(''), {
  workspace: 'mexc_futures',
  page: 'Giao dịch & Bot',
  tab: undefined,
});

// 2. Exness default
assert.deepEqual(parseRoute('?workspace=exness_forex'), {
  workspace: 'exness_forex',
  page: 'Tổng quan',
  tab: undefined,
});

// 3. Exness Bot page with tab
assert.deepEqual(parseRoute('?workspace=exness_forex&page=Bot&tab=learning'), {
  workspace: 'exness_forex',
  page: 'Bot',
  tab: 'learning',
});

// 4. Invalid tab drops to undefined
assert.deepEqual(parseRoute('?workspace=exness_forex&page=Bot&tab=invalid_tab'), {
  workspace: 'exness_forex',
  page: 'Bot',
  tab: undefined,
});

// 5. Invalid page drops to default page
assert.deepEqual(parseRoute('?workspace=exness_forex&page=NonExistent'), {
  workspace: 'exness_forex',
  page: 'Tổng quan',
  tab: undefined,
});

// 6. Tab not allowed on Mexc
assert.deepEqual(
  parseRoute('?workspace=mexc_futures&page=Giao%20d%E1%BB%8Bch%20%26%20Bot&tab=learning'),
  {
    workspace: 'mexc_futures',
    page: 'Giao dịch & Bot',
    tab: undefined,
  },
);

console.log('✓ All router tests passed.');
