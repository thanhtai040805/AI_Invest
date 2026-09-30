import { Decimal } from '@prisma/client/runtime/library';

export interface ExecutionReceipt {
  symbol: string; side: string; shares: number | null;
  gross_value: unknown; brokerage_fee: unknown; transfer_tax: unknown; cash_delta: unknown;
}

export interface CostPosition { symbol: string; quantity: number; avgPrice: unknown }

/** Moving average cost, with buy fees allocated proportionally on partial sales. */
export function accountLedger(receipts: ExecutionReceipt[], positions: CostPosition[], cash: number) {
  const lots = new Map<string, { quantity: number; gross: Decimal; net: Decimal }>();
  let realized = new Decimal(0), delta = new Decimal(0), fees = new Decimal(0);
  let complete = true;
  for (const receipt of receipts) {
    if (!receipt.shares || !Number.isInteger(receipt.shares) || receipt.shares <= 0 ||
        [receipt.gross_value, receipt.brokerage_fee, receipt.transfer_tax, receipt.cash_delta].some(v => v == null)) {
      complete = false;
      continue;
    }
    const gross = new Decimal(String(receipt.gross_value));
    const fee = new Decimal(String(receipt.brokerage_fee));
    const tax = new Decimal(String(receipt.transfer_tax));
    const cashDelta = new Decimal(String(receipt.cash_delta));
    const buy = receipt.side === 'BUY';
    if (![gross, fee, tax, cashDelta].every(v => v.isFinite()) ||
        (!buy && !['SELL', 'SELL_MP'].includes(receipt.side)) || gross.lte(0) || fee.lt(0) || tax.lt(0) ||
        (buy && !tax.isZero()) ||
        cashDelta.minus(buy ? gross.plus(fee).negated() : gross.minus(fee).minus(tax)).abs().gt(0.01)) {
      complete = false;
      continue;
    }
    const lot = lots.get(receipt.symbol) ?? { quantity: 0, gross: new Decimal(0), net: new Decimal(0) };
    delta = delta.plus(cashDelta);
    fees = fees.plus(fee).plus(tax);
    if (buy) {
      lot.quantity += receipt.shares;
      lot.gross = lot.gross.plus(gross);
      lot.net = lot.net.plus(gross).plus(fee);
    } else if (lot.quantity >= receipt.shares) {
      const fraction = new Decimal(receipt.shares).div(lot.quantity);
      const soldCost = lot.net.mul(fraction);
      realized = realized.plus(cashDelta).minus(soldCost);
      lot.gross = lot.gross.minus(lot.gross.mul(fraction));
      lot.net = lot.net.minus(soldCost);
      lot.quantity -= receipt.shares;
    } else {
      complete = false;
    }
    lots.set(receipt.symbol, lot);
  }
  for (const position of positions) {
    const lot = lots.get(position.symbol);
    // avg_price is stored to two decimal places; allow its rounding per share.
    if (!lot || lot.quantity !== position.quantity ||
        lot.gross.minus(new Decimal(String(position.avgPrice)).mul(position.quantity)).abs().gt(position.quantity * 0.005 + 0.01)) complete = false;
  }
  if ([...lots].some(([symbol, lot]) => lot.quantity > 0 && !positions.some(p => p.symbol === symbol))) complete = false;
  const money = (value: Decimal) => Number(value.toDecimalPlaces(2));
  const costBySymbol = Object.fromEntries([...lots].map(([symbol, lot]) => [symbol, money(lot.net)]));
  return {
    complete,
    realizedPnl: complete ? money(realized) : null,
    costBySymbol: complete ? costBySymbol : {},
    openingCash: complete ? money(new Decimal(cash).minus(delta)) : null,
    fees: complete ? money(fees) : null,
  };
}

export interface NavPoint { date: string; value: number }

export function portfolioRisk(points: NavPoint[], benchmark: NavPoint[]) {
  let peak = 0, maxDrawdown = 0;
  for (const p of points) {
    peak = Math.max(peak, p.value);
    if (peak > 0) maxDrawdown = Math.max(maxDrawdown, (peak - p.value) / peak * 100);
  }
  const market = new Map(benchmark.map(p => [p.date, p.value]));
  const marketDates = benchmark.map(p => p.date);
  const returns: number[] = [], pairs: [number, number][] = [];
  for (let i = 1; i < points.length; i++) {
    const previous = points[i - 1], current = points[i];
    if (previous.value <= 0 || current.value <= 0) continue;
    const start = marketDates.indexOf(previous.date), end = marketDates.indexOf(current.date);
    // Never annualize a multi-session gap as though it were a daily return.
    const gap = (Date.parse(current.date) - Date.parse(previous.date)) / 86400000;
    const adjacent = start >= 0 && end >= 0 ? end === start + 1
      : gap === 1 || (gap === 3 && new Date(previous.date).getUTCDay() === 5);
    if (!adjacent) continue;
    const ret = current.value / previous.value - 1;
    returns.push(ret);
    const before = market.get(previous.date), after = market.get(current.date);
    if (before && after) pairs.push([ret, after / before - 1]);
  }
  const mean = (values: number[]) => values.reduce((s, v) => s + v, 0) / values.length;
  let sharpe: number | null = null, alpha: number | null = null, beta: number | null = null;
  if (returns.length >= 20) {
    const avg = mean(returns);
    const variance = returns.reduce((s, v) => s + (v - avg) ** 2, 0) / (returns.length - 1);
    if (variance > 0) sharpe = avg / Math.sqrt(variance) * Math.sqrt(252);
  }
  if (pairs.length >= 20) {
    const portfolioMean = mean(pairs.map(p => p[0])), marketMean = mean(pairs.map(p => p[1]));
    const variance = pairs.reduce((s, p) => s + (p[1] - marketMean) ** 2, 0);
    if (variance > 0) {
      beta = pairs.reduce((s, p) => s + (p[0] - portfolioMean) * (p[1] - marketMean), 0) / variance;
      alpha = (portfolioMean - beta * marketMean) * 252 * 100;
    }
  }
  return { sharpe, alpha, beta, maxDrawdown: points.length >= 2 ? maxDrawdown : null,
    observations: returns.length, benchmarkObservations: pairs.length, riskFreeRate: 0,
    message: 'NAV cuối ngày; tối thiểu 20 lợi suất ngày cho Sharpe/Alpha/Beta; lãi suất phi rủi ro 0%. Drawdown theo các snapshot có sẵn.' };
}

/** DNSE prices are already VND. Missing/stale marks must never become cost prices. */
export function quoteMark(quote: Record<string, any> | null, now = Date.now(), allowStale = false) {
  if (!quote) return null;
  const price = Number(quote.price);
  let received = Number(quote.receivedAt);
  if (received > 0 && received < 1e12) received *= 1000;
  if (!received && quote.lastUpdate) {
    const stamp = String(quote.lastUpdate).replace(' ', 'T');
    received = Date.parse(/[Zz]|[+-]\d{2}:?\d{2}$/.test(stamp) ? stamp : `${stamp}+07:00`);
  }
  const age = now - received;
  const stale = age > 30_000 || quote.stale === true;
  if (!Number.isFinite(price) || price <= 0 || !Number.isFinite(received) || received <= 0 || age < -1000 ||
      (!allowStale && stale) || quote.source !== 'dnse-ws') return null;
  return { price, priceAsOf: new Date(received).toISOString(), priceSource: 'dnse-ws', stale };
}
