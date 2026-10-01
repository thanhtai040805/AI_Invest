const test = require('node:test');
const assert = require('node:assert/strict');
const { accountLedger, portfolioRisk, quoteMark } = require('../../dist/services/portfolioAccounting');
const receipt = (side, shares, gross, fee, tax = 0) => ({ symbol: 'AAA', side, shares,
  gross_value: gross, brokerage_fee: fee, transfer_tax: tax,
  cash_delta: side === 'BUY' ? -gross - fee : gross - fee - tax });

test('partial sales allocate buy fees; realized + unrealized reconcile to NAV', () => {
  const receipts = [receipt('BUY', 100, 1000000, 10000), receipt('BUY', 100, 2000000, 10000), receipt('SELL', 50, 1000000, 10000, 1000)];
  const cash = 10000000 + receipts.reduce((sum, r) => sum + r.cash_delta, 0);
  const ledger = accountLedger(receipts, [{ symbol: 'AAA', quantity: 150, avgPrice: 15000 }], cash);
  assert.equal(ledger.complete, true);
  assert.equal(ledger.realizedPnl, 234000);
  assert.equal(ledger.costBySymbol.AAA, 2265000);
  assert.equal(ledger.openingCash, 10000000);
  const marketValue = 150 * 20000;
  assert.equal(ledger.realizedPnl + marketValue - ledger.costBySymbol.AAA, cash + marketValue - 10000000);
});

test('fully sold accounts retain realized profit and have no unrealized cost', () => {
  const ledger = accountLedger([receipt('BUY', 100, 1000000, 10000), receipt('SELL', 100, 1200000, 10000, 1200)], [], 10178800);
  assert.equal(ledger.realizedPnl, 178800);
  assert.equal(ledger.openingCash, 10000000);
  assert.equal(ledger.costBySymbol.AAA, 0);
});

test('sales in a period retain earlier buy costs and partial sale fees', () => {
  const receipts = [
    { ...receipt('BUY', 100, 1000000, 10000), executedDate: '2026-09-25' },
    { ...receipt('SELL', 40, 480000, 10000, 480), executedDate: '2026-09-28' },
    { ...receipt('SELL', 60, 660000, 10000, 660), executedDate: '2026-09-29' },
  ];
  const ledger = accountLedger(receipts, [], 10000000 + receipts.reduce((sum, r) => sum + r.cash_delta, 0),
    { from: '2026-09-29', to: '2026-09-29' });
  assert.equal(ledger.complete, true);
  assert.equal(ledger.periodRealizedPnl, 43340);
  assert.equal(ledger.realizedPnl, 108860);
  assert.equal(ledger.sales[1].cost, 606000);
  assert.equal(ledger.sales[1].proceeds, 649340);
  const missingTime = accountLedger([{ ...receipts[0], executedDate: undefined }, ...receipts.slice(1)], [], 10000000, {});
  assert.equal(missingTime.periodRealizedPnl, null);
});

test('missing receipts, unmatched positions, oversales and invalid cash receipts fail closed', () => {
  for (const [rows, positions] of [
    [[{ ...receipt('BUY', 100, 1000000, 10000), cash_delta: null }], []],
    [[], [{ symbol: 'AAA', quantity: 100, avgPrice: 10000 }]],
    [[receipt('SELL', 100, 1000000, 10000, 1000)], []],
    [[{ ...receipt('BUY', 100, 1000000, 10000), cash_delta: -1000000 }], []],
    [[receipt('BUY', 100, 1000000, 10000)], [{ symbol: 'AAA', quantity: 100, avgPrice: 11000 }]],
    [[receipt('BUY', 100, 1000000, 10000, 1000)], []],
    [[{ ...receipt('BUY', 100, 1000000, 10000), gross_value: 'NaN' }], []],
  ]) {
    const result = accountLedger(rows, positions, 10000000);
    assert.equal(result.complete, false);
    assert.equal(result.realizedPnl, null);
    assert.equal(result.openingCash, null);
  }
});

test('quote marks normalize timestamp units, never guess price units, and reject stale/replay prices', () => {
  const now = Date.parse('2026-09-30T07:00:00Z');
  const quote = { price: 20150, source: 'dnse-ws', receivedAt: (now - 1000) / 1000 };
  assert.equal(quoteMark(quote, now).price, 20150);
  assert.equal(quoteMark({ ...quote, price: 400 }, now).price, 400);
  assert.equal(quoteMark({ ...quote, receivedAt: now - 31000 }, now), null);
  assert.equal(quoteMark({ ...quote, receivedAt: now - 31000 }, now, true).stale, true);
  assert.equal(quoteMark({ ...quote, source: 'dnse-replay' }, now), null);
  assert.equal(quoteMark({ ...quote, stale: true }, now), null);
  assert.equal(quoteMark({ price: 10000, source: 'dnse-ws' }, now), null);
  assert.equal(quoteMark({ price: 10000, source: 'dnse-ws', lastUpdate: '2026-09-30 13:59:59' }, now).price, 10000);
});

test('risk metrics use aligned daily NAV; alpha/beta do not invent benchmark observations', () => {
  const points = [], benchmark = [];
  const day = new Date('2026-08-03T00:00:00Z');
  let nav = 1000, index = 1000;
  for (let i = 0; i < 25; i++) {
    const ret = (i % 3 - 1) / 100;
    if (i) { nav *= 1 + 0.0002 + 1.5 * ret; index *= 1 + ret; }
    const date = day.toISOString().slice(0, 10);
    points.push({ date, value: nav }); benchmark.push({ date, value: index });
    do { day.setUTCDate(day.getUTCDate() + 1); } while ([0, 6].includes(day.getUTCDay()));
  }
  const risk = portfolioRisk(points, benchmark);
  assert.equal(risk.observations, 24);
  assert(Math.abs(risk.beta - 1.5) < 1e-10);
  assert(Math.abs(risk.alpha - 5.04) < 1e-9);
  assert(Number.isFinite(risk.sharpe));
  assert.equal(portfolioRisk(points, []).beta, null);
  assert.equal(portfolioRisk(points.slice(0, 3), benchmark).sharpe, null);
  assert.equal(portfolioRisk([{ date: '2026-09-21', value: 100 }, { date: '2026-09-25', value: 90 }], []).observations, 0);
  assert.equal(portfolioRisk([{ date: '2026-09-21', value: 100 }, { date: '2026-09-25', value: 90 }], []).maxDrawdown, 10);
});
