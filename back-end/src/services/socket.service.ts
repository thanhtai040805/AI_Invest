import { Server as HttpServer } from 'http';
import { Server, Socket } from 'socket.io';
import { config } from '../config';
import { subscriptionService } from './subscription.service';
import { aiEngineService } from './aiEngine.service';
import prisma from '../config/database';

const MAX_SUBSCRIPTIONS_PER_SOCKET = 50;

async function latestDbQuote(symbol: string) {
  const rows = await prisma.$queryRaw<Array<Record<string, any>>>`
    SELECT d.date, d.close_adj, d.open_adj, d.high_adj, d.low_adj, d.volume_total,
           c.open AS raw_open, c.close AS raw_close
    FROM market_data_daily d
    LEFT JOIN market_data_daily_calculation c ON c.ticker=d.ticker AND c.date=d.date
    WHERE d.ticker=${symbol}
    ORDER BY d.date DESC LIMIT 1
  `;
  return rows[0] ?? null;
}

interface SocketMetadata {
  subscribedSymbols: Set<string>;
  subscribedMarket: boolean;
  connectedAt: Date;
}

class SocketService {
  private io!: Server;
  private socketMeta = new Map<string, SocketMetadata>();
  private tickerInterval: NodeJS.Timeout | null = null;

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
        subscribedMarket: false,
        connectedAt: new Date(),
      };
      this.socketMeta.set(socket.id, meta);

      console.log(`[Socket.IO] Client connected: ${socket.id} (total: ${this.io.engine.clientsCount})`);

      socket.on('subscribe:symbol', async (symbol: string) => {
        const sym = symbol.toUpperCase();
        const currentMeta = this.socketMeta.get(socket.id);
        if (!currentMeta) return;
        if (currentMeta.subscribedSymbols.has(sym)) return;

        if (currentMeta.subscribedSymbols.size >= MAX_SUBSCRIPTIONS_PER_SOCKET) {
          socket.emit('error:limit', {
            message: `Maximum ${MAX_SUBSCRIPTIONS_PER_SOCKET} symbols per connection`,
            limit: MAX_SUBSCRIPTIONS_PER_SOCKET,
          });
          return;
        }

        const room = `stock:${sym}`;
        socket.join(room);
        currentMeta.subscribedSymbols.add(sym);
        await subscriptionService.addSymbol(sym);

        // Immediately push latest quote from DB to this socket
        latestDbQuote(sym).then((row) => {
          if (row) {
            const price = (row.close_adj ?? 0) * 1000;
            const ref = (row.open_adj ?? row.close_adj ?? 0) * 1000;
            const ceiling = (row.high_adj ?? row.close_adj ?? 0) * 1000;
            const floor = (row.low_adj ?? row.close_adj ?? 0) * 1000;
            const changePct = row.raw_open && row.raw_open !== 0 && row.raw_close != null
              ? ((row.raw_close - row.raw_open) / row.raw_open) * 100
              : 0;

            socket.emit(`stock:price:${sym}`, {
              symbol: sym,
              price,
              ref,
              ceiling,
              floor,
              volume: Number(row.volume_total ?? 0),
              change_pct: changePct,
              timestamp: new Date().toISOString(),
            });
          }
        }).catch(() => {});

        if (config.dnse.enabled) {
          aiEngineService.subscribeStreamSymbols([sym]).catch((err) => {
            console.warn(`[Socket.IO] DNSE subscribe ${sym}:`, err.message);
          });
        }
        console.log(`[Socket.IO] ${socket.id} joined ${room} (${currentMeta.subscribedSymbols.size} symbols)`);
      });

      socket.on('unsubscribe:symbol', async (symbol: string) => {
        const sym = symbol.toUpperCase();
        const currentMeta = this.socketMeta.get(socket.id);
        if (!currentMeta?.subscribedSymbols.delete(sym)) return;
        socket.leave(`stock:${sym}`);
        const remaining = await subscriptionService.removeSymbol(sym);
        if (remaining === 0 && config.dnse.enabled) {
          aiEngineService.unsubscribeStreamSymbols([sym]).catch((err) => {
            console.warn(`[Socket.IO] DNSE unsubscribe ${sym}:`, err.message);
          });
        }
      });

      socket.on('subscribe:market', async () => {
        socket.join('market:overview');
        const currentMeta = this.socketMeta.get(socket.id);
        if (!currentMeta || currentMeta.subscribedMarket) return;
        currentMeta.subscribedMarket = true;
        await subscriptionService.incrementMarketSubscribers();
        console.log(`[Socket.IO] ${socket.id} joined market:overview`);

        // Immediately push latest indices from DB to this socket
        prisma.$queryRaw<Array<Record<string, unknown>>>`
          SELECT c.ticker AS symbol, c.date, d.close_adj AS value,
                 CASE WHEN c.open IS NOT NULL AND c.open <> 0
                   THEN ((c.close - c.open) / c.open) * 100 ELSE 0 END AS change_pct
          FROM market_data_daily_calculation c
          JOIN market_data_daily d USING (ticker, date)
          WHERE c.ticker IN ('VNINDEX', 'VN-INDEX', 'VN30', 'HNXINDEX', 'UPCOMINDEX')
            AND c.date = (SELECT MAX(date) FROM market_data_daily WHERE ticker IN ('VNINDEX', 'VN-INDEX', 'VN30', 'HNXINDEX', 'UPCOMINDEX'))
        `.then((indices) => {
          if (indices.length > 0) {
            socket.emit('market:indices', { indices, timestamp: new Date().toISOString() });
          }
        }).catch(() => {});
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
        if (meta) {
          for (const sym of meta.subscribedSymbols) {
            socket.leave(`stock:${sym}`);
            const remaining = await subscriptionService.removeSymbol(sym);
            if (remaining === 0 && config.dnse.enabled) {
              aiEngineService.unsubscribeStreamSymbols([sym]).catch((err) => {
                console.warn(`[Socket.IO] DNSE unsubscribe ${sym}:`, err.message);
              });
            }
          }
          if (meta.subscribedMarket) {
            socket.leave('market:overview');
            await subscriptionService.decrementMarketSubscribers();
          }
          this.socketMeta.delete(socket.id);
        }
        console.log(`[Socket.IO] Client disconnected: ${socket.id} (cleaned up ${meta?.subscribedSymbols.size ?? 0} subscriptions)`);
      });
    });

    this.startTicker();
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
          const indices = await prisma.$queryRaw<Array<Record<string, unknown>>>`
            SELECT c.ticker AS symbol, c.date, d.close_adj AS value,
                   CASE WHEN c.open IS NOT NULL AND c.open <> 0
                     THEN ((c.close - c.open) / c.open) * 100 ELSE 0 END AS change_pct
            FROM market_data_daily_calculation c
            JOIN market_data_daily d USING (ticker, date)
            WHERE c.ticker IN ('VNINDEX', 'VN-INDEX', 'VN30', 'HNXINDEX', 'UPCOMINDEX')
              AND c.date = (SELECT MAX(date) FROM market_data_daily WHERE ticker IN ('VNINDEX', 'VN-INDEX', 'VN30', 'HNXINDEX', 'UPCOMINDEX'))
          `.catch(() => []);
          if (indices.length > 0) {
            this.emitMarketIndices({ indices, timestamp: new Date().toISOString() });
          }
        }

        for (const sym of activeSymbols) {
          const row = await latestDbQuote(sym).catch(() => null);

          if (row) {
            const price = (row.close_adj ?? 0) * 1000;
            const ref = (row.open_adj ?? row.close_adj ?? 0) * 1000;
            const ceiling = (row.high_adj ?? row.close_adj ?? 0) * 1000;
            const floor = (row.low_adj ?? row.close_adj ?? 0) * 1000;
            const changePct = row.raw_open && row.raw_open !== 0 && row.raw_close != null
              ? ((row.raw_close - row.raw_open) / row.raw_open) * 100
              : 0;

            this.emitStockPrice(sym, {
              symbol: sym,
              price,
              ref,
              ceiling,
              floor,
              volume: Number(row.volume_total ?? 0),
              change_pct: changePct,
              timestamp: new Date().toISOString(),
            });
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
    this.io.to(`stock:${sym}`).emit(`stock:ohlc:${sym}`, data);
  }

  emitOhlcClosed(symbol: string, data: unknown): void {
    const sym = symbol.toUpperCase();
    this.io.to(`stock:${sym}`).emit(`stock:ohlcClosed:${sym}`, data);
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
    if (this.tickerInterval) {
      clearInterval(this.tickerInterval);
      this.tickerInterval = null;
    }
    this.io?.close();
    this.socketMeta.clear();
  }
}

export const socketService = new SocketService();
