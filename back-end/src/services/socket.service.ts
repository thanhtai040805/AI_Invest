import { Server as HttpServer } from 'http';
import { Server, Socket } from 'socket.io';
import { config } from '../config';
import { subscriptionService } from './subscription.service';
import { aiEngineService } from './aiEngine.service';
import prisma from '../config/database';
import { redisService } from './redis.service';
import { bestOrderBookLevel } from '../modules/market/market.utils';
import { hasMarketPrice, marketQuoteSnapshot } from './marketQuote.service';

const MAX_SUBSCRIPTIONS_PER_SOCKET = 400;
const STREAM_CHANGE_BATCH_MS = 50;
const HOSE_INDEXES = new Set(['VNINDEX', 'VN30', 'VN100']);

async function latestDbQuote(symbol: string) {
  const rows = await prisma.$queryRaw<Array<Record<string, any>>>`
    SELECT d.date, d.close_adj, d.volume_total,
           c.close AS raw_close, prev.close AS prior_close,
           s.ref_price, s.ceiling, s.floor
    FROM market_data_daily d
    LEFT JOIN market_data_daily_calculation c ON c.ticker=d.ticker AND c.date=d.date
    LEFT JOIN stocks s ON s.symbol=d.ticker
    LEFT JOIN LATERAL (
      SELECT close FROM market_data_daily_calculation
      WHERE ticker=d.ticker AND date < d.date ORDER BY date DESC LIMIT 1
    ) prev ON TRUE
    WHERE d.ticker=${symbol}
    ORDER BY d.date DESC LIMIT 1
  `;
  return rows[0] ?? null;
}

async function latestDbIndices() {
  return prisma.$queryRaw<Array<Record<string, unknown>>>`
    SELECT c.ticker AS symbol, c.date, c.close AS value,
           CASE WHEN prev.close IS NOT NULL AND prev.close <> 0
             THEN ((c.close - prev.close) / prev.close) * 100 ELSE NULL END AS change_pct
    FROM market_data_daily_calculation c
    JOIN market_data_daily d USING (ticker, date)
    LEFT JOIN LATERAL (
      SELECT close FROM market_data_daily_calculation
      WHERE ticker=c.ticker AND date < c.date ORDER BY date DESC LIMIT 1
    ) prev ON TRUE
    WHERE c.ticker IN ('VNINDEX', 'VN-INDEX', 'VN30', 'VN100')
      AND c.date = (SELECT MAX(date) FROM market_data_daily WHERE ticker IN ('VNINDEX', 'VN-INDEX', 'VN30', 'VN100'))
  `;
}

function dailyQuoteSnapshot(symbol: string, row: Record<string, any>) {
  const toVnd = (value: unknown) => {
    const number = Number(value ?? 0);
    return number > 0 && number < 500 ? number * 1000 : number;
  };
  const rawClose = Number(row.raw_close);
  const priorClose = Number(row.prior_close);
  const price = toVnd(Number.isFinite(rawClose) && rawClose > 0 ? row.raw_close : row.close_adj);
  const ref = toVnd(Number.isFinite(priorClose) && priorClose > 0 ? row.prior_close : row.ref_price);
  const ceiling = toVnd(row.ceiling);
  const floor = toVnd(row.floor);
  const changePct = ref > 0 && price > 0 ? ((price - ref) / ref) * 100 : null;

  return {
    symbol,
    price,
    ref,
    ceiling,
    floor,
    volume: Number(row.volume_total ?? 0),
    change_pct: changePct,
    timestamp: row.date,
    source: 'postgres',
    isSnapshot: true,
  };
}

function orderBookSnapshot(book: Record<string, unknown>) {
  let receivedAt = Number(book.receivedAt ?? 0);
  if (receivedAt > 0 && receivedAt < 1_000_000_000_000) receivedAt *= 1000;
  const age = Date.now() - receivedAt;
  const stale = !receivedAt || age < -5_000 || age >= 15_000;
  return { ...book, receivedAt: receivedAt || null, stale, isSnapshot: true };
}

interface SocketMetadata {
  subscribedSymbols: Set<string>;
  subscribedOhlc: Set<string>;
  pendingSymbols: Set<string>;
  pendingOhlc: Set<string>;
  subscribedMarket: boolean;
  connectedAt: Date;
}

class SocketService {
  private io!: Server;
  private socketMeta = new Map<string, SocketMetadata>();
  private tickerInterval: NodeJS.Timeout | null = null;
  private streamChangeTimer: NodeJS.Timeout | null = null;
  private pendingStreamChanges = new Map<string, boolean>();
  private pendingOhlcChanges = new Map<string, boolean>();
  private pendingMarketOrderBooks = new Map<string, Record<string, unknown>>();
  private marketOrderBookTimer: NodeJS.Timeout | null = null;

  private queueStreamChange(symbol: string, subscribed: boolean): void {
    this.pendingStreamChanges.set(symbol, subscribed);
    if (this.streamChangeTimer) return;
    this.streamChangeTimer = setTimeout(() => void this.flushStreamChanges(), STREAM_CHANGE_BATCH_MS);
  }

  private queueOhlcChange(symbol: string, resolution: string, subscribed: boolean): void {
    this.pendingOhlcChanges.set(`${resolution}:${symbol}`, subscribed);
    if (this.streamChangeTimer) return;
    this.streamChangeTimer = setTimeout(() => void this.flushStreamChanges(), STREAM_CHANGE_BATCH_MS);
  }

  private async flushStreamChanges(): Promise<void> {
    this.streamChangeTimer = null;
    const changes = [...this.pendingStreamChanges];
    const ohlcChanges = [...this.pendingOhlcChanges];
    this.pendingStreamChanges.clear();
    this.pendingOhlcChanges.clear();
    const subscribe = changes.filter(([, active]) => active).map(([symbol]) => symbol);
    const unsubscribe = changes.filter(([, active]) => !active).map(([symbol]) => symbol);
    let failed = false;

    const groups = [
      { keys: subscribe, active: true, run: () => aiEngineService.subscribeStreamSymbols(subscribe) },
      { keys: unsubscribe, active: false, run: () => aiEngineService.unsubscribeStreamSymbols(unsubscribe) },
    ];
    for (const group of groups) {
      if (!group.keys.length) continue;
      try {
        await group.run();
      } catch (err) {
        failed = true;
        for (const symbol of group.keys) {
          if (!this.pendingStreamChanges.has(symbol)) this.pendingStreamChanges.set(symbol, group.active);
        }
        console.warn('[Socket.IO] DNSE symbol subscription failed:', err);
      }
    }
    for (const [key, active] of ohlcChanges) {
      const [resolution, symbol] = key.split(':');
      try {
        if (active) await aiEngineService.subscribeStreamOhlc([symbol], resolution);
        else await aiEngineService.unsubscribeStreamOhlc([symbol], resolution);
      } catch (err) {
        failed = true;
        if (!this.pendingOhlcChanges.has(key)) this.pendingOhlcChanges.set(key, active);
        console.warn('[Socket.IO] DNSE OHLC subscription failed:', err);
      }
    }

    if ((this.pendingStreamChanges.size || this.pendingOhlcChanges.size) && !this.streamChangeTimer) {
      this.streamChangeTimer = setTimeout(() => void this.flushStreamChanges(), failed ? 5000 : STREAM_CHANGE_BATCH_MS);
    }
  }

  init(httpServer: HttpServer): Server {
    this.io = new Server(httpServer, {
      cors: {
        origin: config.corsOrigin,
        methods: ['GET', 'POST'],
      },
      transports: ['websocket', 'polling'],
    });

    this.io.on('connection', (socket: Socket) => {
      const meta: SocketMetadata = {
        subscribedSymbols: new Set(),
        subscribedOhlc: new Set(),
        pendingSymbols: new Set(),
        pendingOhlc: new Set(),
        subscribedMarket: false,
        connectedAt: new Date(),
      };
      this.socketMeta.set(socket.id, meta);

      console.log(`[Socket.IO] Client connected: ${socket.id} (total: ${this.io.engine.clientsCount})`);

      socket.on('subscribe:symbol', async (symbol: string) => {
        if (typeof symbol !== 'string') return;
        const sym = symbol.trim().toUpperCase();
        if (!sym) return;
        const currentMeta = this.socketMeta.get(socket.id);
        if (!currentMeta) return;
        if (currentMeta.subscribedSymbols.has(sym) || currentMeta.pendingSymbols.has(sym)) return;

        if (currentMeta.subscribedSymbols.size >= MAX_SUBSCRIPTIONS_PER_SOCKET) {
          socket.emit('error:limit', {
            message: `Maximum ${MAX_SUBSCRIPTIONS_PER_SOCKET} symbols per connection`,
            limit: MAX_SUBSCRIPTIONS_PER_SOCKET,
          });
          return;
        }

        currentMeta.pendingSymbols.add(sym);
        const stock = await prisma.stock.findUnique({ where: { symbol: sym }, select: { exchange: true } }).catch(() => null);
        if (!socket.connected || !this.socketMeta.has(socket.id) || !currentMeta.pendingSymbols.delete(sym)) return;
        if (stock?.exchange !== 'HOSE') {
          socket.emit('error:symbol', { symbol: sym, message: 'Chỉ hỗ trợ cổ phiếu HOSE có trong dữ liệu.' });
          return;
        }

        const room = `stock:${sym}`;
        socket.join(room);
        currentMeta.subscribedSymbols.add(sym);
        const subscriberCount = await subscriptionService.addSymbol(sym);

        const cachedQuote = await redisService.getCache<Record<string, unknown>>(`stock:${sym}:quote`).catch(() => null);
        if (hasMarketPrice(cachedQuote)) {
          socket.emit(`stock:price:${sym}`, marketQuoteSnapshot(cachedQuote));
        } else {
          latestDbQuote(sym).then((row) => {
            if (row) socket.emit(`stock:price:${sym}`, marketQuoteSnapshot(dailyQuoteSnapshot(sym, row)));
          }).catch(() => {});
        }
        if (config.dnse.enabled) {
          const [book, foreign] = await Promise.all([
            redisService.getCache<Record<string, unknown>>(`stock:${sym}:orderbook`).catch(() => null),
            redisService.getCache<Record<string, unknown>>(`stock:${sym}:foreign`).catch(() => null),
          ]);
          if (book) socket.emit(`stock:orderbook:${sym}`, orderBookSnapshot(book));
          if (foreign) socket.emit(`stock:foreign:${sym}`, { ...foreign, isSnapshot: true });
        }

        if (config.dnse.enabled && subscriberCount === 1) this.queueStreamChange(sym, true);
        console.log(`[Socket.IO] ${socket.id} joined ${room} (${currentMeta.subscribedSymbols.size} symbols)`);
      });

      socket.on('unsubscribe:symbol', async (symbol: string) => {
        if (typeof symbol !== 'string') return;
        const sym = symbol.trim().toUpperCase();
        const currentMeta = this.socketMeta.get(socket.id);
        currentMeta?.pendingSymbols.delete(sym);
        if (!currentMeta?.subscribedSymbols.delete(sym)) return;
        socket.leave(`stock:${sym}`);
        const remaining = await subscriptionService.removeSymbol(sym);
        if (remaining === 0 && config.dnse.enabled) this.queueStreamChange(sym, false);
      });

      socket.on('subscribe:ohlc', async (request: { symbol?: string; resolution?: string }) => {
        const sym = String(request?.symbol ?? '').trim().toUpperCase();
        const resolution = String(request?.resolution ?? '').toUpperCase();
        if (!sym || !['1', '3', '5', '15', '30', '1H', '1D', '1W'].includes(resolution)) return;
        const currentMeta = this.socketMeta.get(socket.id);
        if (!currentMeta) return;
        const key = `${resolution}:${sym}`;
        if (currentMeta.subscribedOhlc.has(key) || currentMeta.pendingOhlc.has(key)) return;
        currentMeta.pendingOhlc.add(key);
        const stock = HOSE_INDEXES.has(sym) ? null : await prisma.stock.findUnique({ where: { symbol: sym }, select: { exchange: true } }).catch(() => null);
        if (!socket.connected || !this.socketMeta.has(socket.id) || !currentMeta.pendingOhlc.delete(key)) return;
        if (!HOSE_INDEXES.has(sym) && stock?.exchange !== 'HOSE') {
          socket.emit('error:symbol', { symbol: sym, message: 'Chỉ hỗ trợ cổ phiếu HOSE có trong dữ liệu.' });
          return;
        }
        currentMeta.subscribedOhlc.add(key);
        socket.join(`ohlc:${sym}`);
        const count = await subscriptionService.addOhlc(sym, resolution);
        if (config.dnse.enabled && count === 1) this.queueOhlcChange(sym, resolution, true);
      });

      socket.on('unsubscribe:ohlc', async (request: { symbol?: string; resolution?: string }) => {
        const sym = String(request?.symbol ?? '').trim().toUpperCase();
        const resolution = String(request?.resolution ?? '').toUpperCase();
        const currentMeta = this.socketMeta.get(socket.id);
        currentMeta?.pendingOhlc.delete(`${resolution}:${sym}`);
        if (!currentMeta?.subscribedOhlc.delete(`${resolution}:${sym}`)) return;
        if (![...currentMeta.subscribedOhlc].some((key) => key.endsWith(`:${sym}`))) socket.leave(`ohlc:${sym}`);
        const remaining = await subscriptionService.removeOhlc(sym, resolution);
        if (config.dnse.enabled && remaining === 0) this.queueOhlcChange(sym, resolution, false);
      });

      socket.on('subscribe:market', async () => {
        socket.join('market:overview');
        const currentMeta = this.socketMeta.get(socket.id);
        if (!currentMeta || currentMeta.subscribedMarket) return;
        currentMeta.subscribedMarket = true;
        await subscriptionService.incrementMarketSubscribers();
        console.log(`[Socket.IO] ${socket.id} joined market:overview`);

        // Prefer the Redis snapshot updated by DNSE; use PostgreSQL as a fallback seed.
        const cached = config.dnse.enabled
          ? await Promise.all(
              ['indices', 'snapshot', 'breadth', 'liquidity', 'heatmap'].map(async (name) => ({
                name,
                data: await redisService.getCache<Record<string, unknown>>(`market:${name}`).catch(() => null),
              })),
            )
          : [];
        for (const { name, data } of cached) {
          if (data) socket.emit(`market:${name}`, { ...data, isSnapshot: true });
        }
        const cachedIndices = cached.find(({ name }) => name === 'indices')?.data;
        if (cachedIndices) {
          return;
        }

        latestDbIndices().then((indices) => {
          if (indices.length > 0) {
            socket.emit('market:indices', {
              indices,
              timestamp: new Date().toISOString(),
              isSnapshot: true,
            });
          }
        }).catch(() => {});
      });

      socket.on('subscribe:market-orderbooks', () => {
        socket.join('market:orderbooks');
      });

      socket.on('unsubscribe:market-orderbooks', () => {
        socket.leave('market:orderbooks');
      });

      socket.on('unsubscribe:market', async () => {
        socket.leave('market:overview');
        const currentMeta = this.socketMeta.get(socket.id);
        if (!currentMeta?.subscribedMarket) return;
        currentMeta.subscribedMarket = false;
        await subscriptionService.decrementMarketSubscribers();
      });

      socket.on('disconnect', async () => {
        const meta = this.socketMeta.get(socket.id);
        this.socketMeta.delete(socket.id);
        if (meta) {
          for (const sym of meta.subscribedSymbols) {
            socket.leave(`stock:${sym}`);
            const remaining = await subscriptionService.removeSymbol(sym);
            if (remaining === 0 && config.dnse.enabled) {
              this.queueStreamChange(sym, false);
            }
          }
          for (const key of meta.subscribedOhlc) {
            const [resolution, sym] = key.split(':');
            const remaining = await subscriptionService.removeOhlc(sym, resolution);
            if (remaining === 0 && config.dnse.enabled) this.queueOhlcChange(sym, resolution, false);
          }
          if (meta.subscribedMarket) {
            socket.leave('market:overview');
            await subscriptionService.decrementMarketSubscribers();
          }
        }
        console.log(`[Socket.IO] Client disconnected: ${socket.id} (cleaned up ${meta?.subscribedSymbols.size ?? 0} subscriptions)`);
      });
    });

    if (!config.dnse.enabled) this.startTicker();
    return this.io;
  }

  private startTicker(): void {
    if (this.tickerInterval) return;
    this.tickerInterval = setInterval(async () => {
      try {
        const activeSymbols = new Set<string>();
        let hasMarketSubscribers = false;

        for (const meta of this.socketMeta.values()) {
          for (const sym of meta.subscribedSymbols) activeSymbols.add(sym);
          if (meta.subscribedMarket) hasMarketSubscribers = true;
        }

        if (hasMarketSubscribers) {
          const indices = await latestDbIndices().catch(() => []);
          if (indices.length > 0) {
            this.emitMarketIndices({ indices, timestamp: new Date().toISOString() });
          }
        }

        for (const sym of activeSymbols) {
          const row = await latestDbQuote(sym).catch(() => null);

          if (row) {
            this.emitStockPrice(sym, dailyQuoteSnapshot(sym, row));
          }
        }
      } catch {
        // silent
      }
    }, 4000);
  }

  getIO(): Server {
    return this.io;
  }

  getActiveConnections(): number {
    return this.io?.engine?.clientsCount ?? 0;
  }

  emitStockPrice(symbol: string, data: unknown): void {
    const sym = symbol.toUpperCase();
    this.io.to(`stock:${sym}`).emit(`stock:price:${sym}`, data);
  }

  emitOrderBook(symbol: string, data: unknown): void {
    const sym = symbol.toUpperCase();
    this.io.to(`stock:${sym}`).emit(`stock:orderbook:${sym}`, data);
    const book = (data ?? {}) as Record<string, unknown>;
    const bid = bestOrderBookLevel(book.bids, true);
    const ask = bestOrderBookLevel(book.asks, false);
    this.pendingMarketOrderBooks.set(sym, {
      symbol: sym,
      bids: bid ? [bid] : [],
      asks: ask ? [ask] : [],
      receivedAt: book.receivedAt,
      lastUpdate: book.lastUpdate,
      source: book.source,
    });
    if (this.marketOrderBookTimer) return;
    this.marketOrderBookTimer = setTimeout(() => {
      this.marketOrderBookTimer = null;
      const updates = [...this.pendingMarketOrderBooks.values()];
      this.pendingMarketOrderBooks.clear();
      for (const update of updates) this.io.to('market:orderbooks').emit('market:orderbook', update);
    }, 100);
  }

  emitTrade(symbol: string, data: unknown): void {
    const sym = symbol.toUpperCase();
    this.io.to(`stock:${sym}`).emit(`stock:trades:${sym}`, data);
  }

  emitTradeExtra(symbol: string, data: unknown): void {
    const sym = symbol.toUpperCase();
    this.io.to(`stock:${sym}`).emit(`stock:tradeExtra:${sym}`, data);
  }

  emitExpectedPrice(symbol: string, data: unknown): void {
    const sym = symbol.toUpperCase();
    this.io.to(`stock:${sym}`).emit(`stock:expectedPrice:${sym}`, data);
  }

  emitForeignTrading(symbol: string, data: unknown): void {
    const sym = symbol.toUpperCase();
    this.io.to(`stock:${sym}`).emit(`stock:foreign:${sym}`, data);
  }

  emitOhlc(symbol: string, data: unknown): void {
    const sym = symbol.toUpperCase();
    this.io.to(`stock:${sym}`).to(`ohlc:${sym}`).emit(`stock:ohlc:${sym}`, data);
  }

  emitOhlcClosed(symbol: string, data: unknown): void {
    const sym = symbol.toUpperCase();
    this.io.to(`stock:${sym}`).to(`ohlc:${sym}`).emit(`stock:ohlcClosed:${sym}`, data);
  }

  emitSecurityDefinition(symbol: string, data: unknown): void {
    const sym = symbol.toUpperCase();
    this.io.to(`stock:${sym}`).emit(`stock:secDef:${sym}`, data);
  }

  emitMarketIndices(data: unknown): void {
    this.io.to('market:overview').emit('market:indices', data);
  }

  emitMarketBreadth(data: unknown): void {
    this.io.to('market:overview').emit('market:breadth', data);
  }

  emitMarketSnapshot(data: unknown): void {
    this.io.to('market:overview').emit('market:snapshot', data);
  }

  emitMarketLiquidity(data: unknown): void {
    this.io.to('market:overview').emit('market:liquidity', data);
  }

  emitMarketHeatmap(data: unknown): void {
    this.io.to('market:overview').emit('market:heatmap', data);
  }

  emitIndexUpdate(name: string, data: unknown): void {
    this.io.to('market:overview').emit(`market:index:${name.toUpperCase()}`, data);
  }

  shutdown(): void {
    if (this.streamChangeTimer) {
      clearTimeout(this.streamChangeTimer);
      this.streamChangeTimer = null;
    }
    if (this.tickerInterval) {
      clearInterval(this.tickerInterval);
      this.tickerInterval = null;
    }
    if (this.marketOrderBookTimer) {
      clearTimeout(this.marketOrderBookTimer);
      this.marketOrderBookTimer = null;
    }
    this.pendingMarketOrderBooks.clear();
    this.io?.close();
    this.socketMeta.clear();
  }
}

export const socketService = new SocketService();
