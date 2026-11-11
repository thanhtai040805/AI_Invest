import { Prisma } from '@prisma/client';
import { Decimal } from '@prisma/client/runtime/library';
import prisma from '../config/database';
import { aiEngineService } from './aiEngine.service';
import { redisService } from './redis.service';
import { socketService } from './socket.service';
import { accountLedger, portfolioRisk, quoteMark, NavPoint, ExecutionReceipt } from './portfolioAccounting';
import {
  liveOrderGateway,
  validateExchangeOrder,
  computeExchangeBands,
  normalizeVndPrice,
  ExchangeCode,
  OrderSide,
  OrderType,
  ExternalOrderBookSnapshot,
  FillSlice,
} from './matchingEngine';

export interface PositionView {
  id: string;
  symbol: string;
  name: string;
  sector?: string | null;
  exchange?: string | null;
  quantity: number;
  lockedQuantity?: number;
  sellableQuantity?: number;
  pendingT2Quantity?: number;
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

export interface OrderDecisionContext {
  omsVersion?: number;
  exchange?: ExchangeCode;
  limitPrice?: number;
  filledQuantity?: number;
  remainingQuantity?: number;
  avgFillPrice?: number;
  cumulativeGross?: number;
  cumulativeFee?: number;
  cumulativeTax?: number;
  cumulativeCashDelta?: number;
  lockedCash?: number;
  lockedShares?: number;
  fills?: FillSlice[];
  amendedAt?: string;
  cancelledAt?: string;
}

export interface EnrichedOrderView {
  id: string;
  userId: string;
  symbol: string;
  side: OrderSide;
  orderType: string;
  price: number;
  limitPrice: number;
  quantity: number;
  filledQuantity: number;
  remainingQuantity: number;
  avgFillPrice: number | null;
  lockedCash: number;
  lockedShares: number;
  status: string;
  createdAt: Date | string;
  fills: FillSlice[];
  stockName: string;
  sector: string | null;
  exchange: string;
}

export class PortfolioError extends Error {}

const OPEN_ORDER_STATUSES = new Set(['PENDING', 'PARTIALLY_FILLED']);
let hydratedRestingOrders = false;

function isRealPrismaClient(): boolean {
  return typeof (prisma as unknown as { $connect?: unknown }).$connect === 'function';
}

function computeCumulativeFinancials(side: OrderSide, grossNotional: number): {
  gross: Decimal;
  fee: number;
  tax: number;
  cashDelta: number;
} {
  if (grossNotional <= 0) {
    return { gross: new Decimal(0), fee: 0, tax: 0, cashDelta: 0 };
  }
  const gross = new Decimal(grossNotional).toDecimalPlaces(2);
  const fee = Number(Decimal.max(gross.mul('0.001'), 10_000).toDecimalPlaces(2));
  const tax = side === 'SELL' ? Number(gross.mul('0.001').toDecimalPlaces(2)) : 0;
  const cashDelta = Number(
    (side === 'BUY' ? gross.plus(fee).negated() : gross.minus(fee).minus(tax)).toDecimalPlaces(2),
  );
  return { gross, fee, tax, cashDelta };
}

function computeBuyLockedCash(
  filledGross: number,
  paidFee: number,
  remainingQty: number,
  limitPrice: number,
): number {
  if (remainingQty <= 0 || limitPrice <= 0) return 0;
  const remGross = remainingQty * limitPrice;
  const worstTotalGross = new Decimal(filledGross + remGross).toDecimalPlaces(2);
  const worstTotalFee = Number(Decimal.max(worstTotalGross.mul('0.001'), 10_000).toDecimalPlaces(2));
  const incrementalFee = Math.max(0, worstTotalFee - paidFee);
  return Number((remGross + incrementalFee).toFixed(2));
}

function readOrderContext(raw: unknown): OrderDecisionContext {
  if (!raw) return {};
  if (typeof raw === 'string') {
    try {
      const parsed = JSON.parse(raw);
      return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? (parsed as OrderDecisionContext) : {};
    } catch {
      return {};
    }
  }
  if (typeof raw === 'object' && !Array.isArray(raw)) return raw as OrderDecisionContext;
  return {};
}

async function fetchDecisionContextsByUser(
  executor: { $queryRawUnsafe: typeof prisma.$queryRawUnsafe },
  userId: string,
): Promise<Map<string, OrderDecisionContext>> {
  const map = new Map<string, OrderDecisionContext>();
  if (!isRealPrismaClient()) return map;
  try {
    const rows = await executor.$queryRawUnsafe<Array<{ id: string; decision_context: unknown }>>(
      'SELECT id, decision_context FROM "orders" WHERE user_id = $1 AND decision_context IS NOT NULL',
      userId,
    );
    for (const row of rows) {
      map.set(row.id, readOrderContext(row.decision_context));
    }
  } catch {
    // Graceful fallback if column unavailable in mock DB
  }
  return map;
}

async function fetchSingleDecisionContext(
  executor: { $queryRawUnsafe: typeof prisma.$queryRawUnsafe },
  orderId: string,
): Promise<OrderDecisionContext> {
  if (!isRealPrismaClient()) return {};
  try {
    const rows = await executor.$queryRawUnsafe<Array<{ decision_context: unknown }>>(
      'SELECT decision_context FROM "orders" WHERE id = $1 LIMIT 1',
      orderId,
    );
    return readOrderContext(rows[0]?.decision_context);
  } catch {
    return {};
  }
}

async function writeOrderDecisionContext(
  executor: { $queryRawUnsafe: typeof prisma.$queryRawUnsafe },
  userId: string,
  orderId: string,
  ctx: OrderDecisionContext,
): Promise<void> {
  if (!isRealPrismaClient()) return;
  await executor.$queryRawUnsafe(
    'UPDATE "orders" SET decision_context = $2::jsonb WHERE user_id = $1 AND id = $3',
    userId,
    JSON.stringify(ctx),
    orderId,
  );
}

function enrichOrderRecord(
  ord: Record<string, any>,
  explicitCtx?: OrderDecisionContext,
): EnrichedOrderView {
  const ctx = explicitCtx ?? readOrderContext(ord.decisionContext ?? ord.decision_context);
  const status = String(ord.status ?? 'FILLED');
  const qty = Number(ord.quantity ?? 0);
  const rawPrice = ord.price != null ? Number(ord.price) : 0;
  const side: OrderSide = String(ord.side ?? 'BUY').toUpperCase() === 'SELL' ? 'SELL' : 'BUY';
  const filledQuantity =
    ctx.filledQuantity != null
      ? Number(ctx.filledQuantity)
      : status === 'FILLED' || status === 'EXECUTED' || status === 'FILLED_REPLAY'
        ? qty
        : 0;
  const remainingQuantity =
    ctx.remainingQuantity != null
      ? Number(ctx.remainingQuantity)
      : OPEN_ORDER_STATUSES.has(status)
        ? Math.max(0, qty - filledQuantity)
        : 0;
  const limitPrice = ctx.limitPrice != null && ctx.limitPrice > 0 ? Number(ctx.limitPrice) : rawPrice;
  const avgFillPrice =
    ctx.avgFillPrice != null && ctx.avgFillPrice > 0
      ? Number(ctx.avgFillPrice)
      : filledQuantity > 0
        ? rawPrice
        : null;
  const lockedCash = OPEN_ORDER_STATUSES.has(status) ? Number(ctx.lockedCash ?? 0) : 0;
  const lockedShares = OPEN_ORDER_STATUSES.has(status) ? Number(ctx.lockedShares ?? 0) : 0;

  return {
    id: String(ord.id ?? ''),
    userId: String(ord.userId ?? ord.user_id ?? ''),
    symbol: String(ord.symbol ?? '').toUpperCase(),
    side,
    orderType: String(ord.orderType ?? ord.order_type ?? 'LO'),
    price: filledQuantity > 0 && status === 'FILLED' ? (avgFillPrice ?? rawPrice) : limitPrice || rawPrice,
    limitPrice,
    quantity: qty,
    filledQuantity,
    remainingQuantity,
    avgFillPrice,
    lockedCash,
    lockedShares,
    status,
    createdAt: ord.createdAt ?? ord.created_at ?? new Date(),
    fills: Array.isArray(ctx.fills) ? ctx.fills : [],
    stockName: String(ord.stock?.name ?? ord.stockName ?? ord.symbol ?? ''),
    sector: ord.stock?.sector ?? ord.sector ?? null,
    exchange: String(ord.stock?.exchange ?? ctx.exchange ?? ord.exchange ?? 'HOSE'),
  };
}

function computeT2CutoffDate(now = new Date()): Date {
  const cursor = new Date(now.getTime());
  let businessDaysBack = 0;
  while (businessDaysBack < 2) {
    cursor.setUTCDate(cursor.getUTCDate() - 1);
    const dow = cursor.getUTCDay();
    if (dow !== 0 && dow !== 6) businessDaysBack++;
  }
  cursor.setUTCHours(5, 0, 0, 0);
  return cursor;
}

export async function ensureRestingOrdersHydrated(): Promise<void> {
  if (hydratedRestingOrders) return;
  hydratedRestingOrders = true;
  if (!isRealPrismaClient()) return;
  try {
    const openOrders = await prisma.$queryRawUnsafe<
      Array<{
        id: string;
        user_id: string;
        symbol: string;
        side: string;
        order_type: string;
        price: unknown;
        quantity: number;
        created_at: Date;
        decision_context: unknown;
        exchange: string | null;
      }>
    >(
      `SELECT o.id, o.user_id, o.symbol, o.side, o.order_type, o.price, o.quantity, o.created_at, o.decision_context, s.exchange
       FROM "orders" o LEFT JOIN stocks s ON s.symbol = o.symbol
       WHERE o.status IN ('PENDING', 'PARTIALLY_FILLED')
       ORDER BY o.created_at ASC`,
    );
    for (const ord of openOrders) {
      const ctx = readOrderContext(ord.decision_context);
      const filled = Number(ctx.filledQuantity ?? 0);
      const remaining = Math.max(0, Number(ctx.remainingQuantity ?? ord.quantity - filled));
      if (remaining < 100) continue;
      const limitPrice = Number(ctx.limitPrice ?? ord.price ?? 0);
      const exch = String(ord.exchange ?? ctx.exchange ?? 'HOSE').toUpperCase();
      const exchange: ExchangeCode = exch === 'HNX' || exch === 'UPCOM' ? exch : 'HOSE';
      const oType = String(ord.order_type ?? 'LO').toUpperCase();
      const orderType: OrderType =
        oType === 'MP' || oType === 'ATO' || oType === 'ATC' ? oType : 'LO';
      liveOrderGateway.registerRestingOrder({
        orderId: ord.id,
        userId: ord.user_id,
        symbol: ord.symbol.toUpperCase(),
        exchange,
        side: ord.side === 'SELL' ? 'SELL' : 'BUY',
        orderType,
        limitPrice,
        initialQuantity: ord.quantity,
        filledQuantity: filled,
        remainingQuantity: remaining,
        createdAtMs: new Date(ord.created_at).getTime(),
      });
    }
  } catch (err) {
    console.warn('[Portfolio OMS] Resting order hydration skipped:', err);
  }
}

async function broadcastSymbolBook(symbol: string): Promise<void> {
  if (!isRealPrismaClient()) return;
  try {
    const ext = await redisService
      .getCache<ExternalOrderBookSnapshot>(`stock:${symbol}:orderbook`)
      .catch(() => null);
    const merged = liveOrderGateway.getMergedOrderBook(symbol, ext);
    socketService.emitOrderBook(symbol, merged);
  } catch {
    // non-fatal
  }
}

async function settleRestingOrderFill(event: {
  orderId: string;
  userId: string;
  symbol: string;
  side: OrderSide;
  fillPrice: number;
  fillQuantity: number;
  remainingQuantity: number;
  liquidity: FillSlice['liquidity'];
}): Promise<void> {
  const symbol = event.symbol.toUpperCase();
  const result = await prisma.$transaction(
    async (tx) => {
      await tx.$queryRawUnsafe('SELECT id FROM users WHERE id = $1 FOR UPDATE', event.userId);
      const order = await tx.order.findUnique({ where: { id: event.orderId } });
      if (!order || !OPEN_ORDER_STATUSES.has(order.status)) return null;

      const ctx = await fetchSingleDecisionContext(tx, order.id);
      const prevFilled = Number(ctx.filledQuantity ?? 0);
      const maxRemaining = Math.max(0, order.quantity - prevFilled);
      const execQty = Math.min(event.fillQuantity, maxRemaining);
      if (execQty <= 0) return null;

      const prevGross = Number(ctx.cumulativeGross ?? 0);
      const prevCashDelta = Number(ctx.cumulativeCashDelta ?? 0);

      const sliceNotional = execQty * event.fillPrice;
      const newFilled = prevFilled + execQty;
      const newRemaining = Math.max(0, order.quantity - newFilled);
      const newGrossNum = prevGross + sliceNotional;
      const newAvgPrice = newGrossNum / newFilled;

      const cumFin = computeCumulativeFinancials(event.side, newGrossNum);
      const incCashDelta = Number((cumFin.cashDelta - prevCashDelta).toFixed(2));

      const existingPos = await tx.position.findFirst({ where: { userId: event.userId, symbol } });
      if (event.side === 'BUY') {
        if (existingPos) {
          const nextQty = existingPos.quantity + execQty;
          const nextAvg = (Number(existingPos.avgPrice) * existingPos.quantity + sliceNotional) / nextQty;
          await tx.position.update({
            where: { id: existingPos.id },
            data: {
              quantity: nextQty,
              avgPrice: new Decimal(nextAvg),
              peakPrice: new Decimal(Math.max(Number(existingPos.peakPrice), event.fillPrice)),
            },
          });
        } else {
          await tx.position.create({
            data: {
              userId: event.userId,
              symbol,
              quantity: execQty,
              avgPrice: new Decimal(event.fillPrice),
              peakPrice: new Decimal(event.fillPrice),
            },
          });
        }
      } else {
        if (!existingPos || existingPos.quantity < execQty) {
          throw new PortfolioError('Insufficient shares during resting sell settlement');
        }
        const nextQty = existingPos.quantity - execQty;
        if (nextQty === 0) await tx.position.delete({ where: { id: existingPos.id } });
        else await tx.position.update({ where: { id: existingPos.id }, data: { quantity: nextQty } });
      }

      await tx.user.update({
        where: { id: event.userId },
        data: { cashBalance: { increment: incCashDelta } },
      });

      const limitPrice = Number(ctx.limitPrice ?? order.price ?? event.fillPrice);
      const newLockedCash =
        event.side === 'BUY' && newRemaining > 0
          ? computeBuyLockedCash(newGrossNum, cumFin.fee, newRemaining, limitPrice)
          : 0;
      const newLockedShares = event.side === 'SELL' ? newRemaining : 0;

      const fills: FillSlice[] = [
        ...(Array.isArray(ctx.fills) ? ctx.fills : []),
        {
          price: event.fillPrice,
          quantity: execQty,
          notional: sliceNotional,
          liquidity: event.liquidity,
          matchedAt: new Date().toISOString(),
        },
      ];

      const nextStatus = newRemaining === 0 ? 'FILLED' : 'PARTIALLY_FILLED';
      const nextCtx: OrderDecisionContext = {
        ...ctx,
        omsVersion: 2,
        limitPrice,
        filledQuantity: newFilled,
        remainingQuantity: newRemaining,
        avgFillPrice: Number(newAvgPrice.toFixed(2)),
        cumulativeGross: newGrossNum,
        cumulativeFee: cumFin.fee,
        cumulativeTax: cumFin.tax,
        cumulativeCashDelta: cumFin.cashDelta,
        lockedCash: newLockedCash,
        lockedShares: newLockedShares,
        fills,
      };

      const updatedOrder = await tx.order.update({
        where: { id: order.id },
        data: {
          status: nextStatus,
          price: nextStatus === 'FILLED' ? new Decimal(newAvgPrice.toFixed(2)) : new Decimal(limitPrice),
        },
      });
      await writeOrderDecisionContext(tx, event.userId, order.id, nextCtx);

      const existingExec = await tx.order_executions.findUnique({ where: { order_id: order.id } });
      if (existingExec) {
        await tx.order_executions.update({
          where: { order_id: order.id },
          data: {
            shares: newFilled,
            executed_price: new Decimal(newAvgPrice.toFixed(2)),
            gross_value: cumFin.gross,
            brokerage_fee: cumFin.fee,
            transfer_tax: cumFin.tax,
            cash_delta: cumFin.cashDelta,
            executed_at: new Date(),
          },
        });
      } else {
        await tx.order_executions.create({
          data: {
            order_id: order.id,
            ticker: symbol,
            action: event.side,
            shares: newFilled,
            executed_price: new Decimal(newAvgPrice.toFixed(2)),
            target_price: limitPrice || event.fillPrice,
            slippage_bps: 0,
            execution_mode: 'LIVE',
            gross_value: cumFin.gross,
            brokerage_fee: cumFin.fee,
            transfer_tax: cumFin.tax,
            cash_delta: cumFin.cashDelta,
          },
        });
      }

      return { order: updatedOrder, ctx: nextCtx };
    },
    { isolationLevel: Prisma.TransactionIsolationLevel.Serializable },
  );

  if (result) {
    socketService.emitOrderUpdated(event.userId, enrichOrderRecord(result.order, result.ctx));
    void broadcastSymbolBook(symbol);
  }
}

liveOrderGateway.registerSettlementHandler(settleRestingOrderFill);

export async function getUserCash(userId: string): Promise<number> {
  const user = await prisma.user.findUnique({ where: { id: userId } });
  if (!user) throw new PortfolioError('Portfolio account not found');
  return Number(user.cashBalance);
}

export async function getSnapshot(userId: string) {
  await ensureRestingOrdersHydrated();
  const today = new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Ho_Chi_Minh' }).format(new Date());
  const state = await prisma.$transaction(async tx => {
    const user = await tx.user.findUnique({ where: { id: userId }, select: { cashBalance: true } });
    if (!user) throw new PortfolioError('Portfolio account not found');
    const [positions, receipts, history, orders, ctxMap] = await Promise.all([
      tx.position.findMany({ where: { userId, quantity: { gt: 0 } }, include: { stock: true } }),
      tx.$queryRawUnsafe<ExecutionReceipt[]>(`SELECT o.symbol, o.side, e.shares,
        e.gross_value, e.brokerage_fee, e.transfer_tax, e.cash_delta
        FROM orders o LEFT JOIN order_executions e ON e.order_id::text=o.id
        WHERE o.user_id=$1 AND (e.order_id IS NOT NULL OR o.status IN ('FILLED','FILLED_REPLAY','EXECUTED','PARTIALLY_EXECUTED'))
        ORDER BY e.executed_at NULLS LAST, o.created_at, o.id`, userId),
      tx.$queryRawUnsafe<NavPoint[]>(`SELECT to_char(date,'YYYY-MM-DD') AS date, total_nav::float AS value
        FROM portfolio_nav_history WHERE account_id=$1 AND date <= $2::date ORDER BY date`, userId, today),
      tx.order.findMany({
        where: { userId },
        orderBy: { createdAt: 'desc' },
        take: 2000,
        include: { stock: { select: { name: true, sector: true, exchange: true } } },
      }),
      fetchDecisionContextsByUser(tx, userId),
    ]);
    return { cash: Number(user.cashBalance), positions, receipts, history, orders, ctxMap };
  }, { isolationLevel: Prisma.TransactionIsolationLevel.RepeatableRead });

  const enrichedOrders: EnrichedOrderView[] = state.orders.map((ord: Record<string, any>) =>
    enrichOrderRecord(ord, state.ctxMap.get(String(ord.id))),
  );
  let lockedCash = 0;
  const lockedSellBySymbol = new Map<string, number>();
  const unsettledBuyBySymbol = new Map<string, number>();
  const t2Cutoff = computeT2CutoffDate();
  let openOrdersCount = 0;

  for (const ord of enrichedOrders) {
    const sym = ord.symbol;
    if (OPEN_ORDER_STATUSES.has(ord.status)) {
      openOrdersCount++;
      if (ord.side === 'BUY') {
        const fallbackLock =
          ord.remainingQuantity > 0 && ord.limitPrice > 0
            ? computeBuyLockedCash(0, 0, ord.remainingQuantity, ord.limitPrice)
            : 0;
        lockedCash += ord.lockedCash > 0 ? ord.lockedCash : fallbackLock;
      } else if (ord.side === 'SELL') {
        const lockQty = ord.lockedShares > 0 ? ord.lockedShares : ord.remainingQuantity;
        lockedSellBySymbol.set(sym, (lockedSellBySymbol.get(sym) ?? 0) + lockQty);
      }
    }
    if (ord.side === 'BUY' && ord.filledQuantity > 0 && ord.createdAt) {
      const created = new Date(ord.createdAt);
      if (!Number.isNaN(created.getTime()) && created >= t2Cutoff) {
        unsettledBuyBySymbol.set(sym, (unsettledBuyBySymbol.get(sym) ?? 0) + ord.filledQuantity);
      }
    }
  }
  lockedCash = Number(lockedCash.toFixed(2));

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
  const strictT2 = process.env.STRICT_T2_SETTLEMENT === 'true';

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
    const lockedQuantity = lockedSellBySymbol.get(pos.symbol.toUpperCase()) ?? 0;
    const pendingT2Quantity = Math.min(pos.quantity, unsettledBuyBySymbol.get(pos.symbol.toUpperCase()) ?? 0);
    const sellableQuantity = Math.max(
      0,
      pos.quantity - lockedQuantity - (strictT2 ? pendingT2Quantity : 0),
    );
    return { id: pos.id, symbol: pos.symbol, name: pos.stock?.name || pos.symbol,
      sector: pos.stock?.sector ?? pos.stock?.industry ?? null,
      exchange: pos.stock?.exchange ?? null,
      quantity: pos.quantity, lockedQuantity, sellableQuantity, pendingT2Quantity,
      avgPrice: Number(pos.avgPrice), currentPrice: price, marketValue, costBasis,
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
  const availableBuyingPower = Math.max(0, Number((state.cash - lockedCash).toFixed(2)));
  const summary = {
    accountId: userId, nav, cash: state.cash, lockedCash, openOrdersCount,
    marketValue, totalCost,
    realizedPnl: ledger.realizedPnl, unrealizedPnl, totalPnl,
    pnl: unrealizedPnl, pnlPercent: totalCost && unrealizedPnl !== null ? unrealizedPnl / totalCost * 100 : null,
    totalReturnPct: ledger.openingCash && totalPnl !== null ? totalPnl / ledger.openingCash * 100 : null,
    openingCash: ledger.openingCash, fees: ledger.fees, ledgerComplete: ledger.complete,
    buyingPower: availableBuyingPower, positionCount: positions.length, holdings: symbols,
    totalEquity: nav, totalProfit: totalPnl,
    totalProfitPercent: ledger.openingCash && totalPnl !== null ? totalPnl / ledger.openingCash * 100 : null,
    dailyPnL: dailyPnl, dailyPnLPercent: dailyBaseline && dailyPnl !== null ? dailyPnl / dailyBaseline * 100 : null,
    dailyBaseline, dailyBaselineDate: baselinePoint?.date ?? null,
    assetsCount: positions.length, valuedAt: new Date().toISOString(),
    stalePrices: positions.filter(p => p.stale).map(p => p.symbol),
  };
  for (const p of positions) p.weight = nav && p.marketValue !== null ? p.marketValue / nav * 100 : null;
  return { summary, positions, performance: { equityCurve: state.history, benchmarkCurve: benchmark, asOf: last?.date ?? null },
    risks: portfolioRisk(state.history, benchmark), orders: enrichedOrders };
}

export async function getPositions(userId: string) { return (await getSnapshot(userId)).positions; }
export async function getSummary(userId: string) { return (await getSnapshot(userId)).summary; }

async function resolveExternalOrderBookAndBands(symbol: string): Promise<{
  externalBook: ExternalOrderBookSnapshot | null;
  ceiling: number;
  floor: number;
  ref: number;
  exchange: ExchangeCode;
}> {
  let externalBook: ExternalOrderBookSnapshot | null = null;
  try {
    externalBook = (await aiEngineService.getOrderBook(symbol)) as ExternalOrderBookSnapshot;
  } catch {
    externalBook = null;
  }

  let ceiling = normalizeVndPrice(externalBook?.ceiling);
  let floor = normalizeVndPrice(externalBook?.floor);
  let ref = normalizeVndPrice(externalBook?.ref);
  let exchange: ExchangeCode = 'HOSE';

  if (isRealPrismaClient()) {
    const [secDef, cachedBook, dbStock, latestCloseRows] = await Promise.all([
      redisService.getCache<Record<string, unknown>>(`stock:${symbol}:sec_def`).catch(() => null),
      !externalBook || !Array.isArray(externalBook.bids)
        ? redisService.getCache<ExternalOrderBookSnapshot>(`stock:${symbol}:orderbook`).catch(() => null)
        : Promise.resolve(null),
      prisma.stock
        .findUnique({
          where: { symbol },
          select: { exchange: true, ceiling: true, floor: true, refPrice: true },
        })
        .catch(() => null),
      prisma
        .$queryRawUnsafe<Array<{ close_price: number }>>(
          `SELECT COALESCE(NULLIF(close_unadj, 0), NULLIF(close_adj, 0)) AS close_price
           FROM market_data_daily WHERE ticker = $1 ORDER BY date DESC LIMIT 1`,
          symbol,
        )
        .catch(() => []),
    ]);

    if ((!externalBook || !Array.isArray(externalBook.bids)) && cachedBook) {
      externalBook = cachedBook;
    }
    const rawEx = String(dbStock?.exchange ?? 'HOSE').toUpperCase();
    if (rawEx === 'HNX' || rawEx === 'UPCOM') exchange = rawEx;

    ceiling = ceiling || normalizeVndPrice(secDef?.ceiling ?? secDef?.ceilingPrice ?? dbStock?.ceiling);
    floor = floor || normalizeVndPrice(secDef?.floor ?? secDef?.floorPrice ?? dbStock?.floor);
    ref =
      ref ||
      normalizeVndPrice(
        secDef?.basicPrice ?? secDef?.ref ?? dbStock?.refPrice ?? latestCloseRows[0]?.close_price,
      );

    if ((ceiling <= 0 || floor <= 0) && ref > 0) {
      const bands = computeExchangeBands(ref, exchange);
      ceiling = ceiling || bands.ceiling;
      floor = floor || bands.floor;
    }
  }

  return { externalBook, ceiling, floor, ref, exchange };
}

export async function placeOrder(
  userId: string,
  input: { symbol: string; side: 'BUY' | 'SELL'; orderType: string; price?: number; quantity: number },
) {
  await ensureRestingOrdersHydrated();
  const symbol = input.symbol.trim().toUpperCase();
  const rawType = input.orderType.toUpperCase();
  if (rawType !== 'LO' && rawType !== 'MP' && rawType !== 'ATO' && rawType !== 'ATC') {
    throw new PortfolioError(`Loại lệnh không hợp lệ: ${input.orderType}`);
  }
  const orderType = rawType as OrderType;
  const limitPrice = input.price != null ? Math.round(Number(input.price)) : undefined;

  const { externalBook, ceiling, floor, ref, exchange } = await resolveExternalOrderBookAndBands(symbol);

  const validation = validateExchangeOrder({
    symbol,
    exchange,
    side: input.side,
    orderType,
    price: limitPrice,
    quantity: input.quantity,
    ceiling,
    floor,
  });
  if (!validation.valid) {
    throw new PortfolioError(validation.reason);
  }

  const matchResult = liveOrderGateway.matchIncomingOrder({
    userId,
    symbol,
    exchange,
    side: input.side,
    orderType,
    limitPrice,
    quantity: input.quantity,
    externalBook,
    ceiling,
    floor,
  });

  if (matchResult.status === 'REJECTED') {
    throw new PortfolioError(
      'Sổ lệnh trực tiếp hiện chưa có thanh khoản đối ứng hoặc đang ngoài phiên khớp lệnh liên tục HOSE — không thể khớp lệnh MP. Vui lòng đặt lệnh giới hạn (LO) để xếp hàng chờ khớp.',
    );
  }

  const filledQty = matchResult.filledQuantity;
  const remainingQty = matchResult.remainingQuantity;
  const filledNotional = matchResult.totalNotional;
  const avgFillPrice = matchResult.avgFillPrice;
  const restingPrice =
    matchResult.restingLimitPrice > 0
      ? matchResult.restingLimitPrice
      : limitPrice ?? (input.side === 'BUY' ? ceiling || ref : floor || ref);

  if (input.side === 'BUY' && remainingQty > 0 && (!restingPrice || restingPrice <= 0)) {
    throw new PortfolioError('Chưa xác định được giá trần tham chiếu để tính tiền ký quỹ cho lệnh phiên.');
  }

  const cumFin = computeCumulativeFinancials(input.side, filledNotional);
  if (input.side === 'SELL' && filledQty > 0 && filledNotional <= cumFin.fee + cumFin.tax) {
    throw new PortfolioError('Giá trị bán không đủ bù phí giao dịch và thuế chuyển nhượng.');
  }

  const lockedCash =
    input.side === 'BUY' && remainingQty > 0
      ? computeBuyLockedCash(filledNotional, cumFin.fee, remainingQty, restingPrice)
      : 0;
  const lockedShares = input.side === 'SELL' ? remainingQty : 0;

  const decisionContext: OrderDecisionContext = {
    omsVersion: 2,
    exchange,
    limitPrice: restingPrice,
    filledQuantity: filledQty,
    remainingQuantity: remainingQty,
    avgFillPrice: filledQty > 0 ? Number(avgFillPrice.toFixed(2)) : undefined,
    cumulativeGross: filledNotional,
    cumulativeFee: cumFin.fee,
    cumulativeTax: cumFin.tax,
    cumulativeCashDelta: cumFin.cashDelta,
    lockedCash,
    lockedShares,
    fills: matchResult.fills,
  };

  const createdOrder = await prisma.$transaction(
    async (tx) => {
      await tx.$queryRawUnsafe('SELECT id FROM users WHERE id = $1 FOR UPDATE', userId);
      await tx.stock.upsert({
        where: { symbol },
        create: { symbol, name: symbol, exchange },
        update: {},
      });

      const user = await tx.user.findUnique({ where: { id: userId } });
      if (!user) throw new PortfolioError('Portfolio account not found');
      const cash = Number(user.cashBalance);
      const existing = await tx.position.findFirst({ where: { userId, symbol } });
      const [openOrders, openCtxMap] = await Promise.all([
        tx.order.findMany({
          where: { userId, status: { in: ['PENDING', 'PARTIALLY_FILLED'] } },
        }),
        fetchDecisionContextsByUser(tx, userId),
      ]);

      let existingLockedCash = 0;
      let existingLockedShares = 0;
      for (const openOrd of openOrders) {
        const oCtx = openCtxMap.get(openOrd.id) ?? {};
        const oRem = Math.max(0, Number(oCtx.remainingQuantity ?? openOrd.quantity - Number(oCtx.filledQuantity ?? 0)));
        if (openOrd.side === 'BUY') {
          const oLim = Number(oCtx.limitPrice ?? openOrd.price ?? 0);
          existingLockedCash +=
            oCtx.lockedCash != null && Number(oCtx.lockedCash) > 0
              ? Number(oCtx.lockedCash)
              : computeBuyLockedCash(0, 0, oRem, oLim);
        } else if (openOrd.side === 'SELL' && openOrd.symbol.toUpperCase() === symbol) {
          existingLockedShares +=
            oCtx.lockedShares != null && Number(oCtx.lockedShares) > 0 ? Number(oCtx.lockedShares) : oRem;
        }
      }

      const availableBuyingPower = Math.max(0, cash - existingLockedCash);

      if (input.side === 'BUY') {
        const immediateCost = filledQty > 0 ? Math.abs(cumFin.cashDelta) : 0;
        if (immediateCost + lockedCash > availableBuyingPower + 0.01) {
          throw new PortfolioError('Insufficient buying power including fee');
        }
        if (filledQty > 0) {
          if (existing) {
            const newQty = existing.quantity + filledQty;
            const newAvg = (Number(existing.avgPrice) * existing.quantity + filledNotional) / newQty;
            await tx.position.update({
              where: { id: existing.id },
              data: {
                quantity: newQty,
                avgPrice: new Decimal(newAvg),
                peakPrice: new Decimal(Math.max(Number(existing.peakPrice), avgFillPrice)),
              },
            });
          } else {
            await tx.position.create({
              data: {
                userId,
                symbol,
                quantity: filledQty,
                avgPrice: new Decimal(avgFillPrice),
                peakPrice: new Decimal(avgFillPrice),
              },
            });
          }
          await tx.user.update({
            where: { id: userId },
            data: { cashBalance: { increment: cumFin.cashDelta } },
          });
        }
      } else {
        const availableShares = (existing?.quantity ?? 0) - existingLockedShares;
        if (!existing || availableShares < input.quantity) {
          throw new PortfolioError('Insufficient shares to sell');
        }
        if (filledQty > 0) {
          const newQty = existing.quantity - filledQty;
          if (newQty === 0) await tx.position.delete({ where: { id: existing.id } });
          else await tx.position.update({ where: { id: existing.id }, data: { quantity: newQty } });
          await tx.user.update({
            where: { id: userId },
            data: { cashBalance: { increment: cumFin.cashDelta } },
          });
        }
      }

      const storedPrice =
        matchResult.status === 'FILLED' ? avgFillPrice : restingPrice > 0 ? restingPrice : null;

      const order = await tx.order.create({
        data: {
          userId,
          symbol,
          side: input.side,
          orderType: matchResult.effectiveOrderType,
          price: storedPrice != null ? storedPrice : undefined,
          quantity: input.quantity,
          status: matchResult.status,
        },
      });

      await writeOrderDecisionContext(tx, userId, order.id, decisionContext);

      if (filledQty > 0) {
        const targetRef = limitPrice ?? avgFillPrice;
        const rawSlip =
          targetRef > 0
            ? ((input.side === 'BUY' ? avgFillPrice - targetRef : targetRef - avgFillPrice) / targetRef) * 10_000
            : 0;
        await tx.order_executions.create({
          data: {
            order_id: order.id,
            ticker: symbol,
            action: input.side,
            shares: filledQty,
            executed_price: avgFillPrice,
            target_price: targetRef,
            slippage_bps: Number(rawSlip.toFixed(2)),
            execution_mode: 'LIVE',
            gross_value: cumFin.gross,
            brokerage_fee: cumFin.fee,
            transfer_tax: cumFin.tax,
            cash_delta: cumFin.cashDelta,
          },
        });
      }

      return order;
    },
    { isolationLevel: Prisma.TransactionIsolationLevel.Serializable },
  );

  if (remainingQty > 0 && OPEN_ORDER_STATUSES.has(matchResult.status)) {
    liveOrderGateway.registerRestingOrder({
      orderId: createdOrder.id,
      userId,
      symbol,
      exchange,
      side: input.side,
      orderType: matchResult.effectiveOrderType,
      limitPrice: restingPrice,
      initialQuantity: input.quantity,
      filledQuantity: filledQty,
      remainingQuantity: remainingQty,
      createdAtMs: Date.now(),
    });
    void broadcastSymbolBook(symbol);
  }

  const enriched = enrichOrderRecord(createdOrder, decisionContext);
  socketService.emitOrderUpdated(userId, enriched);
  return enriched;
}

export async function cancelOrder(userId: string, orderId: string) {
  await ensureRestingOrdersHydrated();
  const result = await prisma.$transaction(
    async (tx) => {
      await tx.$queryRawUnsafe('SELECT id FROM users WHERE id = $1 FOR UPDATE', userId);
      const order = await tx.order.findFirst({ where: { id: orderId, userId } });
      if (!order) throw new PortfolioError('Không tìm thấy lệnh giao dịch.');
      if (!OPEN_ORDER_STATUSES.has(order.status)) {
        throw new PortfolioError('Chỉ có thể hủy lệnh đang chờ khớp hoặc khớp một phần.');
      }

      const ctx = await fetchSingleDecisionContext(tx, order.id);
      const nextCtx: OrderDecisionContext = {
        ...ctx,
        remainingQuantity: 0,
        lockedCash: 0,
        lockedShares: 0,
        cancelledAt: new Date().toISOString(),
      };

      const cancelled = await tx.order.update({
        where: { id: order.id },
        data: { status: 'CANCELLED' },
      });
      await writeOrderDecisionContext(tx, userId, order.id, nextCtx);
      return { order: cancelled, ctx: nextCtx };
    },
    { isolationLevel: Prisma.TransactionIsolationLevel.Serializable },
  );

  liveOrderGateway.cancelRestingOrder(orderId);
  const enriched = enrichOrderRecord(result.order, result.ctx);
  socketService.emitOrderUpdated(userId, enriched);
  void broadcastSymbolBook(enriched.symbol);
  return enriched;
}

export async function amendOrder(
  userId: string,
  orderId: string,
  patch: { price?: number; quantity?: number },
) {
  await ensureRestingOrdersHydrated();
  const existingOrder = await prisma.order.findFirst({ where: { id: orderId, userId } });
  if (!existingOrder) throw new PortfolioError('Không tìm thấy lệnh giao dịch.');
  if (!OPEN_ORDER_STATUSES.has(existingOrder.status)) {
    throw new PortfolioError('Chỉ có thể sửa lệnh đang chờ khớp hoặc khớp một phần.');
  }
  if (existingOrder.orderType !== 'LO') {
    throw new PortfolioError('Chỉ hỗ trợ sửa giá/khối lượng đối với lệnh giới hạn (LO).');
  }

  const symbol = existingOrder.symbol.toUpperCase();
  const side: OrderSide = existingOrder.side === 'SELL' ? 'SELL' : 'BUY';
  const ctx = await fetchSingleDecisionContext(prisma, existingOrder.id);
  const filledQty = Number(ctx.filledQuantity ?? 0);
  const currentLimit = Number(ctx.limitPrice ?? existingOrder.price ?? 0);
  const newPrice = patch.price != null ? Math.round(Number(patch.price)) : currentLimit;
  const newTotalQty = patch.quantity != null ? Math.round(Number(patch.quantity)) : existingOrder.quantity;

  if (newTotalQty < filledQty + 100) {
    throw new PortfolioError(
      `Khối lượng mới phải lớn hơn phần đã khớp ít nhất 1 lô 100 CP (tối thiểu ${filledQty + 100} CP).`,
    );
  }

  const { externalBook, ceiling, floor, exchange } = await resolveExternalOrderBookAndBands(symbol);
  const validation = validateExchangeOrder({
    symbol,
    exchange,
    side,
    orderType: 'LO',
    price: newPrice,
    quantity: newTotalQty,
    ceiling,
    floor,
  });
  if (!validation.valid) throw new PortfolioError(validation.reason);

  const newRemaining = newTotalQty - filledQty;
  const prevGross = Number(ctx.cumulativeGross ?? 0);
  const prevFee = Number(ctx.cumulativeFee ?? 0);
  const newLockedCash =
    side === 'BUY' ? computeBuyLockedCash(prevGross, prevFee, newRemaining, newPrice) : 0;
  const newLockedShares = side === 'SELL' ? newRemaining : 0;

  const result = await prisma.$transaction(
    async (tx) => {
      await tx.$queryRawUnsafe('SELECT id FROM users WHERE id = $1 FOR UPDATE', userId);
      const freshOrder = await tx.order.findFirst({ where: { id: orderId, userId } });
      if (!freshOrder || !OPEN_ORDER_STATUSES.has(freshOrder.status)) {
        throw new PortfolioError('Trạng thái lệnh vừa thay đổi, không thể sửa.');
      }

      const user = await tx.user.findUnique({ where: { id: userId } });
      if (!user) throw new PortfolioError('Portfolio account not found');
      const cash = Number(user.cashBalance);

      const [otherOpenOrders, openCtxMap] = await Promise.all([
        tx.order.findMany({
          where: { userId, status: { in: ['PENDING', 'PARTIALLY_FILLED'] }, id: { not: orderId } },
        }),
        fetchDecisionContextsByUser(tx, userId),
      ]);

      if (side === 'BUY') {
        let otherLockedCash = 0;
        for (const o of otherOpenOrders) {
          if (o.side !== 'BUY') continue;
          const oCtx = openCtxMap.get(o.id) ?? {};
          otherLockedCash += Number(oCtx.lockedCash ?? 0);
        }
        if (newLockedCash > cash - otherLockedCash + 0.01) {
          throw new PortfolioError('Sức mua khả dụng không đủ để sửa lên mức giá/khối lượng này.');
        }
      } else {
        const pos = await tx.position.findFirst({ where: { userId, symbol } });
        let otherLockedShares = 0;
        for (const o of otherOpenOrders) {
          if (o.side !== 'SELL' || o.symbol.toUpperCase() !== symbol) continue;
          const oCtx = openCtxMap.get(o.id) ?? {};
          otherLockedShares += Number(oCtx.lockedShares ?? 0);
        }
        if (!pos || pos.quantity - otherLockedShares < newRemaining) {
          throw new PortfolioError('Số dư cổ phiếu khả dụng không đủ để sửa khối lượng bán.');
        }
      }

      const nextCtx: OrderDecisionContext = {
        ...ctx,
        exchange,
        limitPrice: newPrice,
        remainingQuantity: newRemaining,
        lockedCash: newLockedCash,
        lockedShares: newLockedShares,
        amendedAt: new Date().toISOString(),
      };

      const updated = await tx.order.update({
        where: { id: orderId },
        data: {
          price: newPrice,
          quantity: newTotalQty,
        },
      });
      await writeOrderDecisionContext(tx, userId, orderId, nextCtx);
      return { order: updated, ctx: nextCtx };
    },
    { isolationLevel: Prisma.TransactionIsolationLevel.Serializable },
  );

  liveOrderGateway.amendRestingOrder(orderId, newPrice, newRemaining);
  if (externalBook) {
    liveOrderGateway.onExternalOrderBook(symbol, externalBook);
  }
  const latest = await prisma.order.findUnique({ where: { id: orderId } });
  const latestCtx = latest ? await fetchSingleDecisionContext(prisma, orderId) : result.ctx;
  const enriched = enrichOrderRecord(latest ?? result.order, latestCtx);
  socketService.emitOrderUpdated(userId, enriched);
  void broadcastSymbolBook(symbol);
  return enriched;
}

export async function getEngineStats() {
  await ensureRestingOrdersHydrated();
  return liveOrderGateway.getEngineStats();
}

export async function getPerformance(userId: string) { return (await getSnapshot(userId)).performance; }

export async function getOrders(userId: string) {
  await ensureRestingOrdersHydrated();
  const [rows, ctxMap] = await Promise.all([
    prisma.order.findMany({
      where: { userId },
      orderBy: { createdAt: 'desc' },
      take: 2000,
      include: { stock: { select: { name: true, sector: true, exchange: true } } },
    }),
    fetchDecisionContextsByUser(prisma, userId),
  ]);
  return rows.map((r: Record<string, any>) => enrichOrderRecord(r, ctxMap.get(String(r.id))));
}

export async function getRiskMetrics(userId: string) { return (await getSnapshot(userId)).risks; }
