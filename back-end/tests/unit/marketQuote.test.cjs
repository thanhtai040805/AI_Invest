const test = require('node:test');
const assert = require('node:assert/strict');
const { hasMarketPrice, marketQuoteSnapshot } = require('../../dist/services/marketQuote.service');
const { redisService } = require('../../dist/services/redis.service');

test('quote snapshots keep valid cached prices and expose their age', () => {
  const now = Date.parse('2026-09-30T07:00:00Z');
  const live = { price: 20150, source: 'dnse-ws', receivedAt: (now - 1000) / 1000 };
  const stale = { ...live, receivedAt: (now - 60_000) / 1000 };

  assert.equal(hasMarketPrice(live), true);
  assert.equal(marketQuoteSnapshot(live, now).stale, false);
  assert.equal(marketQuoteSnapshot(stale, now).stale, true);
  assert.equal(marketQuoteSnapshot({ price: 20150, source: 'postgres-eod' }, now).stale, true);
  assert.equal(marketQuoteSnapshot(live, now).isSnapshot, true);
});

test('zero and missing prices do not count as quote snapshots', () => {
  assert.equal(hasMarketPrice(null), false);
  assert.equal(hasMarketPrice({ price: 0, close: null }), false);
  assert.equal(hasMarketPrice({ close: 20150 }), true);
});

test('Redis quote snapshots use SET without expiry while ordinary caches retain their TTL', async () => {
  const calls = [];
  const originalGetClient = redisService.getClient;
  redisService.getClient = () => ({ set: async (...args) => calls.push(args) });
  try {
    await redisService.setCache('stock:FPT:quote', { price: 63200 }, 0);
    await redisService.setCache('market:snapshot:api', { stocks: [] }, 3);
  } finally {
    redisService.getClient = originalGetClient;
  }

  assert.deepEqual(calls, [
    ['stock:FPT:quote', JSON.stringify({ price: 63200 })],
    ['market:snapshot:api', JSON.stringify({ stocks: [] }), 'EX', 3],
  ]);
});
