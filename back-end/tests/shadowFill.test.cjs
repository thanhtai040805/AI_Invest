const test = require('node:test');
const assert = require('node:assert/strict');
const { shadowFill } = require('../dist/services/shadowFill');

test('Shadow fills only against fresh, executable displayed depth', () => {
  const now = Date.now();
  const book = { marketState: 'continuous_morning', lastUpdate: new Date(now - 1000).toISOString(), bids: [{ price: 24, volume: 200 }], asks: [{ price: 25.05, volume: 100 }, { price: 25, volume: 200 }] };
  assert.deepEqual(shadowFill(book, 'BUY', 300, 25100, now), { price: (200 * 25000 + 100 * 25050) / 300, notional: 200 * 25000 + 100 * 25050 });
  assert.throws(() => shadowFill(book, 'BUY', 300, 25000, now), /Insufficient executable depth/);
  assert.throws(() => shadowFill(book, 'BUY', 400, undefined, now), /Insufficient executable depth/);
  assert.throws(() => shadowFill({ ...book, lastUpdate: new Date(now - 11000).toISOString() }, 'SELL', 100, undefined, now), /stale/);
  assert.throws(() => shadowFill({ ...book, bids: [] }, 'SELL', 100, undefined, now), /no executable depth/);
  assert.throws(() => shadowFill({ ...book, marketState: 'closing_auction' }, 'BUY', 100, undefined, now), /continuous trading/);
});
