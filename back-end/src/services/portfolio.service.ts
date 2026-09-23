import { Prisma } from '@prisma/client';
import { Decimal } from '@prisma/client/runtime/library';
import prisma from '../config/database';
import { aiEngineService } from './aiEngine.service';
import { shadowFill } from './shadowFill';

export interface PositionView {
  id: string;
  symbol: string;
  name: string;
  quantity: number;
  avgPrice: number;
  currentPrice: number;
  marketValue: number;
  pnl: number;
  pnlPercent: number;
}

export class PortfolioError extends Error {}

export async function getUserCash(userId: string): Promise<number> {
  const user = await prisma.user.findUnique({ where: { id: userId } });
  if (!user) throw new PortfolioError('Portfolio account not found');
  return Number(user.cashBalance);
}

export async function getPositions(userId: string): Promise<PositionView[]> {
  const positions = await prisma.position.findMany({
    where: { userId },
    include: { stock: true },
  });

  return Promise.all(
    positions.map(async (pos) => {
      const quote = await aiEngineService.getQuote(pos.symbol).catch(() => null);
      const rawPrice = Number(quote?.price);
      const price = Number.isFinite(rawPrice) && rawPrice > 0
        ? (rawPrice < 500 ? Math.round(rawPrice * 1000) : rawPrice)
        : Number(pos.avgPrice);
      const marketValue = price * pos.quantity;
      const cost = Number(pos.avgPrice) * pos.quantity;
      return {
        id: pos.id,
        symbol: pos.symbol,
        name: pos.stock.name,
        quantity: pos.quantity,
        avgPrice: Number(pos.avgPrice),
        currentPrice: price,
        marketValue,
        pnl: marketValue - cost,
        pnlPercent: cost > 0 ? ((marketValue - cost) / cost) * 100 : 0,
      };
    }),
  );
}

export async function getSummary(userId: string) {
  const cash = await getUserCash(userId);
  const positions = await getPositions(userId);
  const marketValue = positions.reduce((s, p) => s + p.marketValue, 0);
  const totalCost = positions.reduce((s, p) => s + p.avgPrice * p.quantity, 0);
  const pnl = marketValue - totalCost;
  const pnlPercent = totalCost > 0 ? (pnl / totalCost) * 100 : 0;
  const nav = cash + marketValue;

  return {
    nav,
    cash,
    marketValue,
    totalCost,
    pnl,
    pnlPercent,
    buyingPower: cash,
    positionCount: positions.length,
    holdings: positions.map((p) => p.symbol),
    totalEquity: nav,
    totalProfit: pnl,
    totalProfitPercent: pnlPercent,
    dailyPnL: null,
    dailyPnLPercent: null,
    assetsCount: positions.length,
  };
}

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
  const fee = Math.max(Math.round(notional * 0.001), 10_000);
  const tax = input.side === 'SELL' ? Math.round(notional * 0.001) : 0;
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
      await tx.user.update({ where: { id: userId }, data: { cashBalance: { decrement: notional + fee } } });
    } else {
      if (!existing || existing.quantity < input.quantity) throw new PortfolioError('Insufficient shares to sell');
      const newQty = existing.quantity - input.quantity;
      if (newQty === 0) await tx.position.delete({ where: { id: existing.id } });
      else await tx.position.update({ where: { id: existing.id }, data: { quantity: newQty } });
      await tx.user.update({ where: { id: userId }, data: { cashBalance: { increment: notional - fee - tax } } });
    }

    return tx.order.create({
      data: { userId, symbol, side: input.side, orderType: input.orderType, price: fillPrice, quantity: input.quantity, status: 'FILLED' },
    });
  }, { isolationLevel: Prisma.TransactionIsolationLevel.Serializable });
}

/** Build equity curve from filled orders + current NAV */
export async function getPerformance(userId: string) {
  const summary = await getSummary(userId);
  return {
    equityCurve: [{ date: new Date().toISOString().slice(0, 10), value: summary.nav }],
    message: 'Historical NAV snapshots are not available',
  };
}

/** Risk metrics from equity curve daily returns */
export async function getOrders(userId: string) {
  return prisma.order.findMany({
    where: { userId },
    orderBy: { createdAt: 'desc' },
    take: 100,
  });
}

export async function getRiskMetrics(userId: string) {
  await getUserCash(userId);
  return { sharpe: null, alpha: null, beta: null, maxDrawdown: null, message: 'Historical daily NAV snapshots are not available' };
}
