import { Server as HttpServer } from 'http';
import { Server, Socket } from 'socket.io';
import { config } from '../config';
import { subscriptionService } from './subscription.service';
import { aiEngineService } from './aiEngine.service';

const MAX_SUBSCRIPTIONS_PER_SOCKET = 50;

interface SocketMetadata {
  subscribedSymbols: Set<string>;
  subscribedMarket: boolean;
  connectedAt: Date;
}

class SocketService {
  private io!: Server;
  private socketMeta = new Map<string, SocketMetadata>();

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
        if (currentMeta) {
          currentMeta.subscribedSymbols.delete(sym);
        }
        socket.leave(`stock:${sym}`);
        await subscriptionService.removeSymbol(sym);
      });

      socket.on('subscribe:market', async () => {
        socket.join('market:overview');
        const currentMeta = this.socketMeta.get(socket.id);
        if (currentMeta) {
          currentMeta.subscribedMarket = true;
        }
        await subscriptionService.incrementMarketSubscribers();
        console.log(`[Socket.IO] ${socket.id} joined market:overview`);
      });

      socket.on('unsubscribe:market', async () => {
        socket.leave('market:overview');
        const currentMeta = this.socketMeta.get(socket.id);
        if (currentMeta) {
          currentMeta.subscribedMarket = false;
        }
        await subscriptionService.decrementMarketSubscribers();
      });

      socket.on('disconnect', async () => {
        const meta = this.socketMeta.get(socket.id);
        if (meta) {
          for (const sym of meta.subscribedSymbols) {
            socket.leave(`stock:${sym}`);
            await subscriptionService.removeSymbol(sym);
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

    return this.io;
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

  emitAlert(userId: string, data: unknown): void {
    this.io.to(`user:${userId}`).emit('alert:triggered', data);
  }

  emitSystemAlert(data: unknown): void {
    this.io.to('market:overview').emit('alert:triggered', data);
  }

  shutdown(): void {
    this.io?.close();
    this.socketMeta.clear();
  }
}

export const socketService = new SocketService();
