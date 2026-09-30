import { Prisma } from '@prisma/client';
import { Decimal } from '@prisma/client/runtime/library';
import prisma from '../config/database';
import { aiEngineService } from './aiEngine.service';
import { shadowFill } from './shadowFill';
import { accountLedger, portfolioRisk, quoteMark, NavPoint, ExecutionReceipt } from './portfolioAccounting';

export interface PositionView {
  id: string;
  symbol: string;
  name: string;
  quantity: number;
  avgPrice: number;
  currentPrice: number | null;
  marketValue: number | null;
  weight: number | null;
  pnl: number | null;
  pnlPercent: number | null;
  costBasis: number | null;
  priceAsOf: string | null;
  priceSource: string;
  stale: boolean;
}

export class PortfolioError extends Error {}

export async function getUserCash(userId: string): Promise<number> {
  const user = await prisma.user.findUnique({ where: { id: userId } });
  if (!user) throw new PortfolioError('Portfolio account not found');
  return Number(user.cashBalance);
}

export async function getSnapshot(userId: string) {
  const today = new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Ho_Chi_Minh' }).format(new Date());
  const state = await prisma.$transaction(async tx => {
    const user = await tx.user.findUnique({ where: { id: userId }, select: { cashBalance: true } });
    if (!user) throw new PortfolioError('Portfolio account not found');
    const [positions, receipts, history, orders] = await Promise.all([
      tx.position.findMany({ where: { userId, quantity: { gt: 0 } }, include: { stock: true } }),
      tx.$queryRawUnsafe<ExecutionReceipt[]>(`SELECT o.symbol, o.side, e.shares,
        e.gross_value, e.brokerage_fee, e.transfer_tax, e.cash_delta
        FROM orders o LEFT JOIN order_executions e ON e.order_id::text=o.id
        WHERE o.user_id=$1 AND (e.order_id IS NOT NULL OR o.status IN ('FILLED','FILLED_REPLAY','EXECUTED','PARTIALLY_EXECUTED'))
        ORDER BY e.executed_at NULLS LAST, o.created_at, o.id`, userId),
      tx.$queryRawUnsafe<NavPoint[]>(`SELECT to_char(date,'YYYY-MM-DD') AS date, total_nav::float AS value
        FROM portfolio_nav_history WHERE account_id=$1 AND date <= $2::date ORDER BY date`, userId, today),
      tx.order.findMany({ where: { userId }, orderBy: { createdAt: 'desc' }, take: 100 }),
    ]);
    return { cash: Number(user.cashBalance), positions, receipts, history, orders };
  }, { isolationLevel: Prisma.TransactionIsolationLevel.RepeatableRead });
  const ledger = accountLedger(state.receipts, state.positions, state.cash);
  const symbols = state.positions.map(p => p.symbol);
  const [daily, quotes, benchmark] = await Promise.all([
    prisma.$queryRawUnsafe<{ ticker: string; current_price: number; price_date: string }[]>(`
      SELECT p.symbol AS ticker, md.close_unadj * 1000 AS current_price, to_char(md.date,'YYYY-MM-DD') AS price_date
      FROM unnest($1::text[]) AS p(symbol) LEFT JOIN LATERAL (
        SELECT date, close_unadj FROM market_data_daily WHERE ticker=p.symbol AND date <= $2::date
          AND close_unadj > 0 ORDER BY date DESC LIMIT 1
      ) md ON TRUE`, symbols, today),
    Promise.all(symbols.map(async symbol => [symbol, await aiEngineService.getQuote(symbol).catch(() => null)] as const)),
    state.history.length ? prisma.$queryRawUnsafe<NavPoint[]>(`SELECT to_char(date,'YYYY-MM-DD') AS date,
      close_unadj::float AS value FROM market_data_daily WHERE ticker='VNINDEX' AND date BETWEEN $1::date AND $2::date
      AND close_unadj>0 ORDER BY date`, state.history[0].date, state.history.at(-1)!.date) : Promise.resolve([]),
  ]);
  const quoteBySymbol = new Map(quotes);
  const positions: PositionView[] = state.positions.map(pos => {
    const close = daily.find(p => p.ticker === pos.symbol);
    const quote = quoteMark(quoteBySymbol.get(pos.symbol) ?? null, Date.now(), true);
    const closeTime = close?.price_date ? Date.parse(`${close.price_date}T15:00:00+07:00`) : 0;
    // Retain a dated last trade when it is newer than the daily close, even
    // for illiquid symbols; flag it stale instead of reverting to an older mark.
    const mark = quote && Date.parse(quote.priceAsOf) >= closeTime ? quote : null;
    const price = mark?.price ?? (close && close.current_price > 0 ? Number(close.current_price) : null);
    const marketValue = price === null ? null : price * pos.quantity;
    const costBasis = ledger.complete ? ledger.costBySymbol[pos.symbol] ?? 0 : null;
    const pnl = marketValue === null || costBasis === null ? null : marketValue - costBasis;
    return { id: pos.id, symbol: pos.symbol, name: pos.stock?.name || pos.symbol,
      quantity: pos.quantity, avgPrice: Number(pos.avgPrice), currentPrice: price, marketValue, costBasis,
      weight: null, pnl, pnlPercent: pnl !== null && costBasis && costBasis > 0 ? pnl / costBasis * 100 : null,
      priceAsOf: mark?.priceAsOf ?? close?.price_date ?? null,
      priceSource: mark?.priceSource ?? (price === null ? 'missing' : 'daily-close'), stale: mark?.stale ?? true };
  });
  const priced = positions.every(p => p.marketValue !== null);
  const marketValue = priced ? positions.reduce((s, p) => s + p.marketValue!, 0) : null;
  const nav = marketValue === null ? null : state.cash + marketValue;
  const totalCost = ledger.complete ? positions.reduce((s, p) => s + p.costBasis!, 0) : null;
  const unrealizedPnl = marketValue === null || totalCost === null ? null : marketValue - totalCost;
  const totalPnl = unrealizedPnl === null || ledger.realizedPnl === null ? null : unrealizedPnl + ledger.realizedPnl;
  const last = state.history.at(-1);
  const expectedPrevious = new Date(`${today}T00:00:00Z`);
  do { expectedPrevious.setUTCDate(expectedPrevious.getUTCDate() - 1); }
  while ([0, 6].includes(expectedPrevious.getUTCDay()));
  // A multi-day historical gap is not today's P&L. Holidays are handled conservatively.
  const baselinePoint = state.history.find(p => p.date === expectedPrevious.toISOString().slice(0, 10));
  const dailyBaseline = baselinePoint?.value ?? null;
  const dailyPnl = nav === null || dailyBaseline === null ? null : nav - dailyBaseline;
  const summary = {
    accountId: userId, nav, cash: state.cash, marketValue, totalCost,
    realizedPnl: ledger.realizedPnl, unrealizedPnl, totalPnl,
    pnl: unrealizedPnl, pnlPercent: totalCost && unrealizedPnl !== null ? unrealizedPnl / totalCost * 100 : null,
    totalReturnPct: ledger.openingCash && totalPnl !== null ? totalPnl / ledger.openingCash * 100 : null,
    openingCash: ledger.openingCash, fees: ledger.fees, ledgerComplete: ledger.complete,
    buyingPower: state.cash, positionCount: positions.length, holdings: symbols,
    totalEquity: nav, totalProfit: totalPnl,
    totalProfitPercent: ledger.openingCash && totalPnl !== null ? totalPnl / ledger.openingCash * 100 : null,
    dailyPnL: dailyPnl, dailyPnLPercent: dailyBaseline && dailyPnl !== null ? dailyPnl / dailyBaseline * 100 : null,
    dailyBaseline, dailyBaselineDate: baselinePoint?.date ?? null,
    assetsCount: positions.length, valuedAt: new Date().toISOString(),
    stalePrices: positions.filter(p => p.stale).map(p => p.symbol),
  };
  for (const p of positions) p.weight = nav && p.marketValue !== null ? p.marketValue / nav * 100 : null;
  return { summary, positions, performance: { equityCurve: state.history, asOf: last?.date ?? null },
    risks: portfolioRisk(state.history, benchmark), orders: state.orders };
}

export async function getPositions(userId: string) { return (await getSnapshot(userId)).positions; }
export async function getSummary(userId: string) { return (await getSnapshot(userId)).summary; }

export async function placeOrder(
  userId: string,
  input: { symbol: string; side: 'BUY' | 'SELL'; orderType: string; price?: number; quantity: number },
) {
  const symbol = input.symbol.toUpperCase();
  if (input.orderType !== 'LO' && input.orderType !== 'MP') {
    throw new PortfolioError('ATO/ATC auction fills are not supported in Shadow mode');
  }
  let fill: { price: number; notional: number };
  try {
    fill = shadowFill(await aiEngineService.getOrderBook(symbol), input.side, input.quantity,
      input.orderType === 'LO' ? input.price : undefined);
  } catch (error) {
    throw new PortfolioError(error instanceof Error ? error.message : 'Live order book is unavailable');
  }
  const { price: fillPrice, notional } = fill;
  const gross = new Decimal(notional).toDecimalPlaces(2);
  const fee = Number(Decimal.max(gross.mul('0.001'), 10_000));
  const tax = input.side === 'SELL' ? Number(gross.mul('0.001')) : 0;
  const cashDelta = Number((input.side === 'BUY' ? gross.plus(fee).negated() : gross.minus(fee).minus(tax)).toDecimalPlaces(2));
  if (input.side === 'SELL' && notional <= fee + tax) {
    throw new PortfolioError('Sale proceeds do not cover fee and tax');
  }

  return prisma.$transaction(async (tx) => {
    await tx.$queryRawUnsafe('SELECT id FROM users WHERE id = $1 FOR UPDATE', userId);
    await tx.stock.upsert({
      where: { symbol },
      create: { symbol, name: symbol, exchange: 'HOSE' },
      update: {},
    });

    const user = await tx.user.findUnique({ where: { id: userId } });
    if (!user) throw new PortfolioError('Portfolio account not found');
    const cash = Number(user.cashBalance);
    const existing = await tx.position.findFirst({ where: { userId, symbol } });

    if (input.side === 'BUY') {
      if (notional + fee > cash) throw new PortfolioError('Insufficient buying power including fee');
      if (existing) {
        const newQty = existing.quantity + input.quantity;
        const newAvg = (Number(existing.avgPrice) * existing.quantity + notional) / newQty;
        await tx.position.update({
          where: { id: existing.id },
          data: { quantity: newQty, avgPrice: new Decimal(newAvg) },
        });
      } else {
        await tx.position.create({
          data: { userId, symbol, quantity: input.quantity, avgPrice: new Decimal(fillPrice) },
        });
      }
      await tx.user.update({ where: { id: userId }, data: { cashBalance: { increment: cashDelta } } });
    } else {
      if (!existing || existing.quantity < input.quantity) throw new PortfolioError('Insufficient shares to sell');
      const newQty = existing.quantity - input.quantity;
      if (newQty === 0) await tx.position.delete({ where: { id: existing.id } });
      else await tx.position.update({ where: { id: existing.id }, data: { quantity: newQty } });
      await tx.user.update({ where: { id: userId }, data: { cashBalance: { increment: cashDelta } } });
    }

    const order = await tx.order.create({
      data: { userId, symbol, side: input.side, orderType: input.orderType, price: fillPrice, quantity: input.quantity, status: 'FILLED' },
    });
    await tx.order_executions.create({ data: {
      order_id: order.id, ticker: symbol, action: input.side, shares: input.quantity,
      executed_price: fillPrice, target_price: input.price ?? fillPrice, slippage_bps: 0,
      execution_mode: 'SHADOW', gross_value: gross, brokerage_fee: fee, transfer_tax: tax, cash_delta: cashDelta,
    } });
    return order;
  }, { isolationLevel: Prisma.TransactionIsolationLevel.Serializable });
}

export async function getPerformance(userId: string) { return (await getSnapshot(userId)).performance; }

export async function getOrders(userId: string) {
  return prisma.order.findMany({ where: { userId }, orderBy: { createdAt: 'desc' }, take: 100 });
}

export async function getRiskMetrics(userId: string) { return (await getSnapshot(userId)).risks; }
