import { Server as HttpServer } from 'http';
import { Server, Socket } from 'socket.io';
import { config } from '../config';
import { subscriptionService } from './subscription.service';
import { aiEngineService } from './aiEngine.service';

class SocketService {
  private io!: Server;

  init(httpServer: HttpServer): Server {
    this.io = new Server(httpServer, {
      cors: {
        origin: config.corsOrigin,
        methods: ['GET', 'POST'],
      },
      transports: ['websocket', 'polling'],
    });

    this.io.on('connection', (socket: Socket) => {
      console.log(`[Socket.IO] Client connected: ${socket.id}`);

      socket.on('subscribe:symbol', async (symbol: string) => {
        const sym = symbol.toUpperCase();
        const room = `stock:${sym}`;
        socket.join(room);
        await subscriptionService.addSymbol(sym);
        if (config.dnse.enabled) {
          aiEngineService.subscribeStreamSymbols([sym]).catch((err) => {
            console.warn(`[Socket.IO] DNSE subscribe ${sym}:`, err.message);
          });
        }
        console.log(`[Socket.IO] ${socket.id} joined ${room}`);
      });

      socket.on('unsubscribe:symbol', async (symbol: string) => {
        const sym = symbol.toUpperCase();
        socket.leave(`stock:${sym}`);
        await subscriptionService.removeSymbol(sym);
      });

      socket.on('subscribe:market', async () => {
        socket.join('market:overview');
        await subscriptionService.incrementMarketSubscribers();
        console.log(`[Socket.IO] ${socket.id} joined market:overview`);
      });

      socket.on('unsubscribe:market', async () => {
        socket.leave('market:overview');
        await subscriptionService.decrementMarketSubscribers();
      });

      socket.on('disconnect', () => {
        console.log(`[Socket.IO] Client disconnected: ${socket.id}`);
      });
    });

    return this.io;
  }

  getIO(): Server {
    return this.io;
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

  emitMarketIndices(data: unknown): void {
    this.io.to('market:overview').emit('market:indices', data);
  }

  emitMarketBreadth(data: unknown): void {
    this.io.to('market:overview').emit('market:breadth', data);
  }

  emitMarketSnapshot(data: unknown): void {
    this.io.to('market:overview').emit('market:snapshot', data);
  }

  emitAlert(userId: string, data: unknown): void {
    this.io.to(`user:${userId}`).emit('alert:triggered', data);
  }

  shutdown(): void {
    this.io?.close();
  }
}

export const socketService = new SocketService();
