const test = require('node:test');
const assert = require('node:assert/strict');
const express = require('express');
const accountId = process.env.STANDALONE_ML_ACCOUNT_ID?.trim() || 'standalone-pure-ml-fund-account';
const rows = [
  { id: 1, account_id: accountId, ticker: 'HPG', predict_date: '2026-09-21', created_at: '2026-09-27T10:00:00Z', decision: 'BUY', order_status: 'FILLED_REPLAY' },
  { id: 2, account_id: accountId, ticker: 'FPT', predict_date: '2026-09-27', created_at: '2026-09-28T00:00:00Z', decision: 'SKIP', order_status: null },
  { id: 3, account_id: 'other-fund', ticker: 'VNM', predict_date: '2026-09-21' },
];
const queries = [];
const receipts = [
  { symbol: 'AAA', side: 'BUY', shares: 100, gross_value: 500, brokerage_fee: 10, transfer_tax: 0, cash_delta: -510, executedDate: '2026-09-20', executionId: 'buy' },
  { symbol: 'AAA', side: 'SELL', shares: 40, gross_value: 240, brokerage_fee: 10, transfer_tax: 1, cash_delta: 229, executedDate: '2026-09-21', executionId: 'sale1' },
  { symbol: 'AAA', side: 'SELL', shares: 60, gross_value: 360, brokerage_fee: 10, transfer_tax: 1, cash_delta: 349, executedDate: '2026-09-29', executionId: 'sale2' },
];
const database = { $queryRawUnsafe: async (sql, ...params) => {
  queries.push({ sql, params });
  if (sql.includes('COALESCE($2::date')) {
    const date = params[1] || '2026-09-27';
    return rows.filter(row => row.account_id === params[0] && row.predict_date === date);
  }
  if (sql.includes('SELECT DISTINCT') && sql.includes('FROM standalone_ml_predictions')) return [{ date: '2026-09-27' }, { date: '2026-09-21' }];
  if (sql.includes('SELECT a.account_id')) return [{ account_id: params[0], cash_balance: 1068 }];
  if (sql.includes('AS "executionId"')) return receipts;
  if (sql.includes('AS total_evaluated')) return [{ total_evaluated: 0, evaluated_today: 0 }];
  if (sql.includes('FROM portfolio_nav_history')) return [
    { date: '2026-09-20', total_nav: 1000, cash_balance: 1000 },
    { date: '2026-09-21', total_nav: 1100, cash_balance: 500 },
    { date: '2026-09-22', total_nav: 900, cash_balance: 300 },
    { date: '2026-09-25', total_nav: 975500901, cash_balance: 926825901 },
    { date: '2026-09-29', total_nav: 974271008, cash_balance: 403231008 },
  ];
  if (sql.includes('AS trading_date')) return ['2026-09-20', '2026-09-21', '2026-09-22', '2026-09-25', '2026-09-28', '2026-09-29']
    .filter(date => date >= params[0] && date <= params[1]).map(trading_date => ({ trading_date }));
  if (sql.includes('AS fills')) return [{ fills: 2, fees: 10, missing_receipts: 0 }];
  return [];
} };
database.$transaction = async work => work(database);
require.cache[require.resolve('../dist/config/database')] = { loaded: true, exports: { __esModule: true, default: database } };
require.cache[require.resolve('../dist/middleware/auth')] = { loaded: true, exports: {
  optionalAuth: (_req, _res, next) => next(), authMiddleware: (_req, _res, next) => next(),
} };
const router = require('../dist/modules/workspace/workspace.routes').default;

test('ML calendar uses prediction session, isolates account, and exposes actual order status', async () => {
  const app = express();
  app.use(router);
  app.use((err, _req, res, _next) => res.status(err.name === 'ZodError' ? 400 : 500).json({ error: err.message }));
  const server = app.listen(0, '127.0.0.1');
  await new Promise(resolve => server.once('listening', resolve));
  const url = `http://127.0.0.1:${server.address().port}/ml-fund`;
  try {
    const historical = await fetch(`${url}?date=2026-09-21`).then(r => r.json());
    assert.deepEqual(historical.predictions.map(p => p.ticker), ['HPG']);
    assert.equal(historical.predictions[0].order_status, 'FILLED_REPLAY');
    assert.equal(historical.predictions[0].created_at, '2026-09-27T10:00:00Z');
    assert.equal(historical.selectedDate, '2026-09-21');
    assert.equal(historical.account.estimated_nav, 1068);
    assert.equal(historical.account.total_nav, historical.latestClose.total_nav);
    assert.equal(historical.latestClose.date, '2026-09-29');
    assert.equal(historical.trading.realizedPnl, 68);
    const query = queries.find(q => q.sql.includes('COALESCE($2::date'));
    assert.deepEqual(query.params, [accountId, '2026-09-21']);
    assert.match(query.sql, /LEFT JOIN orders o ON o.id = p.order_id/);
    const latest = await fetch(url).then(r => r.json());
    assert.deepEqual(latest.predictions.map(p => p.ticker), ['FPT']);
    assert.equal(latest.selectedDate, '2026-09-27');
    const empty = await fetch(`${url}?date=2026-09-22`).then(r => r.json());
    assert.deepEqual(empty.predictions, []);
    assert.deepEqual(empty.dates, ['2026-09-27', '2026-09-21']);
    assert.equal((await fetch(`${url}?date=2026-02-30`)).status, 400);
    const period = await fetch(`${url}?from=2026-09-22&to=2026-09-22`).then(r => r.json());
    assert.equal(period.performance.openingNav, 1100);
    assert.equal(period.performance.pnl, -200);
    assert.equal(period.performance.sessions[0].dailyPnl, -200);
    assert.equal(period.performance.sessions.length, 1);
    assert.equal((await fetch(`${url}?from=2026-09-23&to=2026-09-21`)).status, 400);
    assert(queries.some(q => q.sql.includes('AS total_evaluated') && q.params[1] === '2026-09-22' && q.params[2] === '2026-09-22'));
    const gapped = await fetch(`${url}?from=2026-09-25&to=2026-09-29`).then(r => r.json());
    assert.deepEqual(gapped.performance.missingDates, ['2026-09-28']);
    const latestNav = gapped.performance.sessions.find(s => s.date === '2026-09-29');
    assert.equal(latestNav.dailyPnl, null);
    assert.equal(latestNav.previousSessionDate, '2026-09-28');
    assert.equal(latestNav.previousNavDate, '2026-09-25');
    assert.equal(gapped.performance.equityCurve.length, 2);
    assert(gapped.navDates.includes('2026-09-25'));
    const saleDay = await fetch(`${url}?from=2026-09-29&to=2026-09-29`).then(r => r.json());
    assert.equal(saleDay.performance.baselineDate, '2026-09-28');
    assert.equal(saleDay.performance.openingNav, null);
    assert.equal(saleDay.performance.pnl, null);
    assert.equal(saleDay.trading.realizedPnl, 43);
    assert.equal(saleDay.trading.sales[0].cost, 306);
    assert.equal(saleDay.trading.sales[0].proceeds, 349);
    assert.equal(saleDay.performance.fees, 11);
    assert.equal(saleDay.latestClose.total_nav, historical.latestClose.total_nav);
    const twoSessions = await fetch(`${url}?from=2026-09-28&to=2026-09-29`).then(r => r.json());
    assert.equal(twoSessions.performance.baselineDate, '2026-09-25');
    assert.equal(twoSessions.performance.pnl, -1229893);
    assert.equal(twoSessions.trading.sales[0].pnl, saleDay.trading.sales[0].pnl);
    const missingEnd = await fetch(`${url}?from=2026-09-21&to=2026-09-28`).then(r => r.json());
    assert.equal(missingEnd.performance.closingDate, '2026-09-28');
    assert.equal(missingEnd.performance.closingNav, null);
    assert.equal(missingEnd.performance.pnl, null);
  } finally { await new Promise(resolve => server.close(resolve)); }
});
