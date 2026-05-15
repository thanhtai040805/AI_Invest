import { io, Socket } from 'socket.io-client';

const SOCKET_URL = process.env.NEXT_PUBLIC_SOCKET_URL || 'http://localhost:3001';

class SocketClient {
  private socket: Socket | null = null;
  private listeners = new Map<string, Set<(data: any) => void>>();

  connect(): void {
    if (this.socket?.connected) return;

    this.socket = io(SOCKET_URL, {
      transports: ['websocket', 'polling'],
      reconnection: true,
      reconnectionAttempts: 10,
      reconnectionDelay: 1000,
    });

    this.socket.on('connect', () => {
      console.log('[Socket] Connected:', this.socket?.id);
      // Re-subscribe to previously active rooms
      this.listeners.forEach((_callbacks, event) => {
        if (event.startsWith('stock:price:')) {
          const symbol = event.replace('stock:price:', '');
          this.socket?.emit('subscribe:symbol', symbol);
        }
      });
    });

    this.socket.on('disconnect', (reason) => {
      console.log('[Socket] Disconnected:', reason);
    });
  }

  disconnect(): void {
    this.socket?.disconnect();
    this.socket = null;
  }

  // ── Market Subscriptions ─────────────────────────────

  subscribeMarket(callback: (data: any) => void): () => void {
    this.connect();
    this.socket?.emit('subscribe:market');

    const handler = (data: any) => callback(data);
    this.socket?.on('market:indices', handler);

    return () => {
      this.socket?.off('market:indices', handler);
      this.socket?.emit('unsubscribe:market');
    };
  }

  subscribeBreadth(callback: (data: any) => void): () => void {
    this.connect();
    this.socket?.emit('subscribe:market');
    const handler = (data: any) => callback(data);
    this.socket?.on('market:breadth', handler);
    return () => this.socket?.off('market:breadth', handler);
  }

  subscribeSnapshot(callback: (data: any) => void): () => void {
    this.connect();
    this.socket?.emit('subscribe:market');
    const handler = (data: any) => callback(data);
    this.socket?.on('market:snapshot', handler);
    return () => this.socket?.off('market:snapshot', handler);
  }

  // ── Stock Subscriptions ──────────────────────────────

  subscribeStock(symbol: string, callbacks: {
    onPrice?: (data: any) => void;
    onOrderBook?: (data: any) => void;
    onTrade?: (data: any) => void;
  }): () => void {
    this.connect();
    const sym = symbol.toUpperCase();
    this.socket?.emit('subscribe:symbol', sym);

    const handlers: Array<[string, (data: any) => void]> = [];

    if (callbacks.onPrice) {
      const event = `stock:price:${sym}`;
      this.socket?.on(event, callbacks.onPrice);
      handlers.push([event, callbacks.onPrice]);
    }
    if (callbacks.onOrderBook) {
      const event = `stock:orderbook:${sym}`;
      this.socket?.on(event, callbacks.onOrderBook);
      handlers.push([event, callbacks.onOrderBook]);
    }
    if (callbacks.onTrade) {
      const event = `stock:trades:${sym}`;
      this.socket?.on(event, callbacks.onTrade);
      handlers.push([event, callbacks.onTrade]);
    }

    // Return unsubscribe function
    return () => {
      handlers.forEach(([event, handler]) => {
        this.socket?.off(event, handler);
      });
      this.socket?.emit('unsubscribe:symbol', sym);
    };
  }

  // ── Alert Subscription ──────────────────────────────

  subscribeAlerts(callback: (data: any) => void): () => void {
    this.connect();
    const handler = (data: any) => callback(data);
    this.socket?.on('alert:triggered', handler);
    return () => this.socket?.off('alert:triggered', handler);
  }

  isConnected(): boolean {
    return this.socket?.connected ?? false;
  }
}

// Singleton
export const socketClient = new SocketClient();
