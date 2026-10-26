import assert from 'node:assert/strict';
import { test } from 'node:test';
import { entryLifecycle, visibleEntryIntents, intentEvents } from '../src/lib/forex-entry-lifecycle.ts';

test('entryLifecycle returns distinct label, short text, and tone for each status', () => {
  assert.deepEqual(entryLifecycle({ status: 'waiting', side: 'long' }), {
    label: 'Canh giá',
    short: 'Canh',
    tone: 'waiting',
  });

  assert.deepEqual(entryLifecycle({ status: 'triggered', side: 'long' }), {
    label: 'Đã kích hoạt · đang gửi',
    short: 'Kích',
    tone: 'triggered',
  });

  assert.deepEqual(entryLifecycle({ status: 'uncertain', side: 'short' }), {
    label: 'Chưa rõ kết quả · cần đối soát',
    short: 'Đối soát',
    tone: 'uncertain',
  });

  assert.deepEqual(entryLifecycle({ status: 'submitted', side: 'long', broker_fills: [] }), {
    label: 'Đã gửi · chờ broker xác nhận khớp',
    short: 'Đã gửi',
    tone: 'triggered',
  });

  assert.deepEqual(
    entryLifecycle({
      status: 'submitted',
      side: 'long',
      broker_fills: [{ position_id: '123', price: 4000, deviation: 0 }],
    }),
    {
      label: 'Broker đã xác nhận khớp',
      short: 'Khớp',
      tone: 'filled',
    },
  );

  assert.deepEqual(entryLifecycle({ status: 'cancelled', side: 'long' }), {
    label: 'Đã hủy canh giá',
    short: 'Hủy',
    tone: 'cancelled',
  });

  assert.deepEqual(entryLifecycle({ status: 'expired', side: 'long' }), {
    label: 'Hết hạn canh giá',
    short: 'Hết hạn',
    tone: 'cancelled',
  });
});

test('visibleEntryIntents filters out future intents and retains active or recent within 1 hour', () => {
  const now = 10000;
  const intents = [
    { id: '1', status: 'waiting', available_at: 9500, side: 'long' },
    { id: '2', status: 'cancelled', available_at: 8000, ended_at: 9200, side: 'short' },
    { id: '3', status: 'expired', available_at: 5000, ended_at: 5500, side: 'long' }, // > 3600s ago
    { id: '4', status: 'waiting', available_at: 10500, side: 'long' }, // future
  ];

  const visible = visibleEntryIntents(intents, now);
  assert.deepEqual(visible.map((i) => i.id), ['1', '2']);
});

test('intentEvents creates appropriate chart event markers', () => {
  const waitingIntent = {
    strategy: 'hast4_test',
    side: 'long',
    status: 'waiting',
    available_at: 9000,
  };
  assert.deepEqual(intentEvents(waitingIntent), [
    { time: 9000, text: 'CANH MUA · ẢO', color: '#138b79' },
  ]);

  const triggeredIntent = {
    strategy: 'hast4_test',
    side: 'short',
    status: 'triggered',
    winner: 'limit',
    available_at: 9000,
    triggered_at: 9300,
  };
  assert.deepEqual(intentEvents(triggeredIntent), [
    { time: 9000, text: 'CANH BÁN · ẢO', color: '#138b79' },
    { time: 9300, text: 'KÍCH LIMIT BÁN · ẢO', color: '#cb8428' },
  ]);

  const cancelledIntent = {
    strategy: 'hast4_test',
    side: 'long',
    status: 'cancelled',
    available_at: 9000,
    ended_at: 9600,
  };
  assert.deepEqual(intentEvents(cancelledIntent), [
    { time: 9000, text: 'CANH MUA · ẢO', color: '#138b79' },
    { time: 9600, text: 'HỦY MUA · ẢO', color: '#bd7164' },
  ]);
});
