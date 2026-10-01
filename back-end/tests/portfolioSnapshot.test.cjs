const test = require('node:test');
const assert = require('node:assert/strict');
const queries = [], quoteCalls = [], writes = [];
let quoteAge = 1000, quoteStamp = null, receiptFailure = false;
const accountId = 'account-a';
const position = { id: 'position', symbol: 'AAA', quantity: 50, avgPrice: 10000, stock: { name: 'AAA' } };
const receipts = [
  { symbol: 'AAA', side: 'BUY', shares: 100, gross_value: 1000000, brokerage_fee: 10000, transfer_tax: 0, cash_delta: -1010000 },
  { symbol: 'AAA', side: 'SELL', shares: 50, gross_value: 600000, brokerage_fee: 10000, transfer_tax: 600, cash_delta: 589400 },
];
const query = async (sql, ...params) => {
  queries.push({ sql, params });
  if (sql.includes('FROM orders o LEFT JOIN')) return receipts;
  if (sql.includes('portfolio_nav_history')) return [{ date: '2026-09-21', value: 10000000 }, { date: '2026-09-25', value: 9500000 }];
  if (sql.includes('unnest')) return [{ ticker: 'AAA', current_price: 19000, price_date: '2026-09-29' }];
  return [];
};
const db = {
  user: { findUnique: async () => ({ cashBalance: 9579400 }), update: async value => writes.push({ user: value }) },
  position: {
    findMany: async value => { queries.push({ positions: value }); return [position]; },
    findFirst: async () => position, update: async value => writes.push({ position: value }),
  },
  order: { findMany: async () => [], create: async value => { writes.push({ order: value }); return { id: 'f0037980-7f98-4dc3-b939-f13511337ae0', ...value.data }; } },
  stock: { upsert: async () => {} },
  order_executions: { create: async value => { writes.push({ receipt: value }); if (receiptFailure) throw new Error('receipt failed'); return value; } },
  $queryRawUnsafe: query,
  $transaction: async work => work(db),
};
require.cache[require.resolve('../dist/config/database')] = { loaded: true, exports: { __esModule: true, default: db } };
require.cache[require.resolve('../dist/services/aiEngine.service')] = { loaded: true, exports: {
  aiEngineService: {
    getQuote: async symbol => { quoteCalls.push(symbol); return { price: 20000, source: 'dnse-ws', receivedAt: quoteStamp ?? Date.now() - quoteAge }; },
    getOrderBook: async () => ({ marketState: 'continuous_morning', lastUpdate: new Date().toISOString(), asks: [{ price: 10000, volume: 100 }] }),
  },
} };
const service = require('../dist/services/portfolio.service');

test('one snapshot values each symbol once, reads account history and reconciles all P&L', async () => {
  const snapshot = await service.getSnapshot(accountId);
  assert.deepEqual(quoteCalls, ['AAA']);
  assert.equal(snapshot.summary.realizedPnl, 84400);
  assert.equal(snapshot.summary.unrealizedPnl, 495000);
  assert.equal(snapshot.summary.totalPnl, 579400);
  assert.equal(snapshot.summary.nav, 10579400);
  assert(Math.abs(snapshot.summary.totalReturnPct - 5.794) < 1e-10);
  assert.equal(snapshot.summary.accountId, accountId);
  assert.equal(snapshot.positions[0].priceSource, 'dnse-ws');
  assert.equal(snapshot.performance.equityCurve.length, 2);
  assert.equal(snapshot.risks.maxDrawdown, 5);
  assert(queries.filter(q => q.sql?.includes('FROM orders') || q.sql?.includes('portfolio_nav_history')).every(q => q.params[0] === accountId));
  assert.equal(queries.find(q => q.positions).positions.where.userId, accountId);
  assert.match(queries.find(q => q.sql?.includes('unnest')).sql, /ORDER BY date DESC LIMIT 1/);
});

test('old quotes use newer dated daily marks, without manufacturing a zero return', async () => {
  quoteStamp = Date.parse('2026-09-20T10:00:00+07:00');
  const snapshot = await service.getSnapshot(accountId);
  assert.equal(snapshot.positions[0].currentPrice, 19000);
  assert.equal(snapshot.positions[0].priceSource, 'daily-close');
  assert.equal(snapshot.positions[0].priceAsOf, '2026-09-29');
  assert.deepEqual(snapshot.summary.stalePrices, ['AAA']);
  quoteStamp = null;
});

test('illiquid symbols retain a newer last trade and disclose its staleness', async () => {
  quoteAge = 60000;
  const snapshot = await service.getSnapshot(accountId);
  assert.equal(snapshot.positions[0].currentPrice, 20000);
  assert.equal(snapshot.positions[0].priceSource, 'dnse-ws');
  assert.equal(snapshot.positions[0].stale, true);
  assert.deepEqual(snapshot.summary.stalePrices, ['AAA']);
  quoteAge = 1000;
});

test('manual orders write the financial receipt inside the same transaction', async () => {
  const order = await service.placeOrder(accountId, { symbol: 'AAA', side: 'BUY', orderType: 'LO', price: 10000, quantity: 100 });
  const receipt = writes.find(w => w.receipt).receipt.data;
  assert.equal(receipt.order_id, order.id);
  assert.equal(Number(receipt.gross_value), 1000000);
  assert.equal(receipt.brokerage_fee, 10000);
  assert.equal(receipt.cash_delta, -1010000);
  receiptFailure = true;
  await assert.rejects(service.placeOrder(accountId, { symbol: 'AAA', side: 'BUY', orderType: 'LO', price: 10000, quantity: 100 }), /receipt failed/);
});
