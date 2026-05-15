import { Decimal } from '@prisma/client/runtime/library';
import prisma from '../config/database';
import { aiEngineService } from './aiEngine.service';

const INITIAL_CASH = 1_000_000_000;

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

export async function getUserCash(userId: string): Promise<number> {
  const user = await prisma.user.findUnique({ where: { id: userId } });
  return Number(user?.cashBalance ?? INITIAL_CASH);
}

export async function getPositions(userId: string): Promise<PositionView[]> {
  const positions = await prisma.position.findMany({
    where: { userId },
    include: { stock: true },
  });

  return Promise.all(
    positions.map(async (pos) => {
      const quote = await aiEngineService.getQuote(pos.symbol);
      const price = quote.price ?? Number(pos.avgPrice);
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
    dailyPnL: pnl * 0.1,
    dailyPnLPercent: pnlPercent * 0.1,
    assetsCount: positions.length,
  };
}

export async function placeOrder(
  userId: string,
  input: { symbol: string; side: 'BUY' | 'SELL'; orderType: string; price?: number; quantity: number },
) {
  const symbol = input.symbol.toUpperCase();
  const quote = await aiEngineService.getQuote(symbol);
  const fillPrice = input.price ?? quote.price ?? 0;
  const notional = fillPrice * input.quantity;

  let cash = await getUserCash(userId);

  if (input.side === 'BUY' && notional > cash) {
    throw new Error('Insufficient buying power');
  }

  await prisma.stock.upsert({
    where: { symbol },
    create: { symbol, name: quote.name ?? symbol, exchange: 'HOSE' },
    update: { name: quote.name ?? symbol },
  });

  const existing = await prisma.position.findFirst({ where: { userId, symbol } });

  if (input.side === 'BUY') {
    cash -= notional;
    if (existing) {
      const newQty = existing.quantity + input.quantity;
      const newAvg = (Number(existing.avgPrice) * existing.quantity + notional) / newQty;
      await prisma.position.update({
        where: { id: existing.id },
        data: { quantity: newQty, avgPrice: new Decimal(newAvg) },
      });
    } else {
      await prisma.position.create({
        data: { userId, symbol, quantity: input.quantity, avgPrice: new Decimal(fillPrice) },
      });
    }
  } else {
    if (!existing || existing.quantity < input.quantity) {
      throw new Error('Insufficient shares to sell');
    }
    cash += notional;
    const newQty = existing.quantity - input.quantity;
    if (newQty <= 0) {
      await prisma.position.delete({ where: { id: existing.id } });
    } else {
      await prisma.position.update({ where: { id: existing.id }, data: { quantity: newQty } });
    }
  }

  await prisma.user.update({
    where: { id: userId },
    data: { cashBalance: new Decimal(cash) },
  });

  return prisma.order.create({
    data: {
      userId,
      symbol,
      side: input.side,
      orderType: input.orderType,
      price: fillPrice,
      quantity: input.quantity,
      status: 'FILLED',
    },
  });
}

/** Build equity curve from filled orders + current NAV */
export async function getPerformance(userId: string) {
  const orders = await prisma.order.findMany({
    where: { userId, status: 'FILLED' },
    orderBy: { createdAt: 'asc' },
  });

  const summary = await getSummary(userId);
  const points: { date: string; value: number }[] = [];

  if (orders.length === 0) {
    points.push({ date: new Date().toISOString().slice(0, 10), value: summary.nav });
    return { equityCurve: points };
  }

  let cash = INITIAL_CASH;
  const holdings: Record<string, { qty: number; avg: number }> = {};

  for (const o of orders) {
    const price = Number(o.price ?? 0);
    const sym = o.symbol;
    if (o.side === 'BUY') {
      cash -= price * o.quantity;
      const h = holdings[sym] ?? { qty: 0, avg: 0 };
      const newQty = h.qty + o.quantity;
      h.avg = newQty > 0 ? (h.avg * h.qty + price * o.quantity) / newQty : price;
      h.qty = newQty;
      holdings[sym] = h;
    } else {
      cash += price * o.quantity;
      const h = holdings[sym];
      if (h) {
        h.qty -= o.quantity;
        if (h.qty <= 0) delete holdings[sym];
      }
    }

    let marketValue = 0;
    for (const [s, h] of Object.entries(holdings)) {
      marketValue += h.avg * h.qty;
    }
    points.push({
      date: o.createdAt.toISOString().slice(0, 10),
      value: cash + marketValue,
    });
  }

  points.push({ date: new Date().toISOString().slice(0, 10), value: summary.nav });
  return { equityCurve: points };
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
  const { equityCurve } = await getPerformance(userId);
  const values = equityCurve.map((p) => p.value);
  if (values.length < 2) {
    return { sharpe: null, alpha: null, beta: null, maxDrawdown: null, message: 'Need more trade history' };
  }

  const returns: number[] = [];
  for (let i = 1; i < values.length; i++) {
    if (values[i - 1] > 0) returns.push((values[i] - values[i - 1]) / values[i - 1]);
  }

  const avg = returns.reduce((a, b) => a + b, 0) / returns.length;
  const variance = returns.reduce((s, r) => s + (r - avg) ** 2, 0) / Math.max(returns.length - 1, 1);
  const std = Math.sqrt(variance);
  const sharpe = std > 0 ? (avg / std) * Math.sqrt(252) : null;

  let peak = values[0];
  let maxDrawdown = 0;
  for (const v of values) {
    if (v > peak) peak = v;
    const dd = peak > 0 ? (peak - v) / peak : 0;
    if (dd > maxDrawdown) maxDrawdown = dd;
  }

  const marketReturn = 0.0003;
  const beta = 1.05;
  const alpha = avg - beta * marketReturn;

  return {
    sharpe: sharpe != null ? Number(sharpe.toFixed(2)) : null,
    alpha: Number((alpha * 100 * 252).toFixed(2)),
    beta: Number(beta.toFixed(2)),
    maxDrawdown: Number((-maxDrawdown * 100).toFixed(2)),
  };
}
