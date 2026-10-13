const test = require('node:test');
const assert = require('node:assert/strict');
const writes = [];
const stocks = [0, null, undefined, -1, NaN, Infinity, Number.MAX_SAFE_INTEGER + 1, 123456].map((marketCap, index) => ({ symbol: `STOCK${index}`, marketCap }));
const saved = new Map(stocks.map(stock => [stock.symbol, { marketCap: 58000000000000n }]));

require.cache[require.resolve('../../dist/config/database')] = { loaded: true, exports: { __esModule: true, default: {
  stock: { upsert: async ({ where, create, update }) => {
    writes.push({ create, update });
    const current = saved.get(where.symbol);
    const data = current ? update : create;
    saved.set(where.symbol, { ...current, ...Object.fromEntries(Object.entries(data).filter(([, value]) => value !== undefined)) });
  } },
} } };
require.cache[require.resolve('../../dist/services/aiEngine.service')] = { loaded: true, exports: { aiEngineService: { getStockList: async () => ({ stocks }) } } };
const { syncStocksFromEngine } = require('../../dist/services/stockSync.service');

test('stock sync preserves known capital when a snapshot has missing or invalid capital', async () => {
  assert.equal(await syncStocksFromEngine(), stocks.length);
  for (let index = 0; index < stocks.length - 1; index++) {
    assert.equal(writes[index].create.marketCap, undefined);
    assert.equal(writes[index].update.marketCap, undefined);
    assert.equal(saved.get(stocks[index].symbol).marketCap, 58000000000000n);
  }
  assert.equal(saved.get(stocks.at(-1).symbol).marketCap, 123456n);
});
