import { io, Socket } from 'socket.io-client';

export type ConnectionStatus = 'connecting' | 'connected' | 'disconnected' | 'error';

const SOCKET_URL = process.env.NEXT_PUBLIC_SOCKET_URL || 'http://localhost:3001';

type EventCallback = (data: unknown) => void;

class SocketClient {
  private socket: Socket | null = null;
  private listeners = new Map<string, Set<EventCallback>>();
  private _status: ConnectionStatus = 'disconnected';
  private _statusListeners = new Set<(status: ConnectionStatus) => void>();
  private reconnectAttempts = 0;
  private maxReconnectAttempts = 10;
  private subscribedSymbols = new Set<string>();
  private subscribedMarket = false;
  private symbolRefCounts = new Map<string, number>();
  private marketRefCount = 0;
  private _indexListeners = new Set<EventCallback>();

  get status(): ConnectionStatus {
    return this._status;
  }

  private setStatus(status: ConnectionStatus) {
    this._status = status;
    this._statusListeners.forEach((cb) => cb(status));
  }

  onStatusChange(callback: (status: ConnectionStatus) => void): () => void {
    this._statusListeners.add(callback);
    return () => this._statusListeners.delete(callback);
  }

  connect(): void {
    if (this.socket?.connected) return;
    if (this.socket) {
      this.socket.connect();
      return;
    }

    this.setStatus('connecting');

    this.socket = io(SOCKET_URL, {
      transports: ['websocket', 'polling'],
      reconnection: true,
      reconnectionAttempts: this.maxReconnectAttempts,
      reconnectionDelay: 1000,
      reconnectionDelayMax: 5000,
      timeout: 20000,
    });

    this.socket.on('connect', () => {
      console.log('[Socket] Connected:', this.socket?.id);
      this.setStatus('connected');
      this.reconnectAttempts = 0;
      this.resubscribe();
    });

    this.socket.on('disconnect', (reason) => {
      console.log('[Socket] Disconnected:', reason);
      this.setStatus('disconnected');
    });

    this.socket.on('connect_error', (error) => {
      console.error('[Socket] Connection error:', error.message);
      this.reconnectAttempts++;
      if (this.reconnectAttempts >= this.maxReconnectAttempts) {
        this.setStatus('error');
      }
    });

    this.socket.on('error', (error) => {
      console.error('[Socket] Error:', error);
    });

    // Capture market:index:* events for individual index updates
    this.socket.onAny((event, ...args) => {
      if (event.startsWith('market:index:')) {
        this._indexListeners.forEach((cb) => cb({ name: event.replace('market:index:', ''), data: args[0] }));
      }
    });
  }

  private resubscribe() {
    if (this.subscribedMarket) {
      this.socket?.emit('subscribe:market');
    }
    this.subscribedSymbols.forEach((sym) => {
      this.socket?.emit('subscribe:symbol', sym);
    });
  }

  disconnect(): void {
    this.socket?.disconnect();
    this.socket = null;
    this.setStatus('disconnected');
  }

  subscribeMarket(callback: EventCallback): () => void {
    this.connect();

    const handlers = [
      { event: 'market:indices', handler: callback },
      { event: 'market:breadth', handler: callback },
      { event: 'market:snapshot', handler: callback },
    ];

    handlers.forEach(({ event, handler }) => {
      const wrappedHandler = (data: unknown) => handler(data);
      this.socket?.on(event, wrappedHandler);
      this.registerListener(event, wrappedHandler);
    });

    this.marketRefCount++;
    if (!this.subscribedMarket) {
      this.subscribedMarket = true;
      this.socket?.emit('subscribe:market');
    }

    return () => {
      handlers.forEach(({ event, handler }) => {
        this.socket?.off(event, handler);
        this.removeListener(event, handler);
      });

      this.marketRefCount--;
      if (this.marketRefCount <= 0) {
        this.marketRefCount = 0;
        this.subscribedMarket = false;
        this.socket?.emit('unsubscribe:market');
      }
    };
  }

  subscribeIndices(callback: EventCallback): () => void {
    this.connect();

    this.marketRefCount++;
    if (!this.subscribedMarket) {
      this.subscribedMarket = true;
      this.socket?.emit('subscribe:market');
    }

    const handler = (data: unknown) => callback(data);
    this.socket?.on('market:indices', handler);
    this.registerListener('market:indices', handler);
    return () => {
      this.socket?.off('market:indices', handler);
      this.removeListener('market:indices', handler);
      this.marketRefCount--;
      if (this.marketRefCount <= 0) {
        this.marketRefCount = 0;
        this.subscribedMarket = false;
        this.socket?.emit('unsubscribe:market');
      }
    };
  }

  subscribeBreadth(callback: EventCallback): () => void {
    this.connect();

    this.marketRefCount++;
    if (!this.subscribedMarket) {
      this.subscribedMarket = true;
      this.socket?.emit('subscribe:market');
    }

    const handler = (data: unknown) => callback(data);
    this.socket?.on('market:breadth', handler);
    this.registerListener('market:breadth', handler);
    return () => {
      this.socket?.off('market:breadth', handler);
      this.removeListener('market:breadth', handler);
      this.marketRefCount--;
      if (this.marketRefCount <= 0) {
        this.marketRefCount = 0;
        this.subscribedMarket = false;
        this.socket?.emit('unsubscribe:market');
      }
    };
  }

  subscribeSnapshot(callback: EventCallback): () => void {
    this.connect();

    this.marketRefCount++;
    if (!this.subscribedMarket) {
      this.subscribedMarket = true;
      this.socket?.emit('subscribe:market');
    }

    const handler = (data: unknown) => callback(data);
    this.socket?.on('market:snapshot', handler);
    this.registerListener('market:snapshot', handler);
    return () => {
      this.socket?.off('market:snapshot', handler);
      this.removeListener('market:snapshot', handler);
      this.marketRefCount--;
      if (this.marketRefCount <= 0) {
        this.marketRefCount = 0;
        this.subscribedMarket = false;
        this.socket?.emit('unsubscribe:market');
      }
    };
  }

  subscribeLiquidity(callback: EventCallback): () => void {
    this.connect();

    this.marketRefCount++;
    if (!this.subscribedMarket) {
      this.subscribedMarket = true;
      this.socket?.emit('subscribe:market');
    }

    const handler = (data: unknown) => callback(data);
    this.socket?.on('market:liquidity', handler);
    this.registerListener('market:liquidity', handler);
    return () => {
      this.socket?.off('market:liquidity', handler);
      this.removeListener('market:liquidity', handler);
      this.marketRefCount--;
      if (this.marketRefCount <= 0) {
        this.marketRefCount = 0;
        this.subscribedMarket = false;
        this.socket?.emit('unsubscribe:market');
      }
    };
  }

  subscribeHeatmap(callback: EventCallback): () => void {
    this.connect();

    this.marketRefCount++;
    if (!this.subscribedMarket) {
      this.subscribedMarket = true;
      this.socket?.emit('subscribe:market');
    }

    const handler = (data: unknown) => callback(data);
    this.socket?.on('market:heatmap', handler);
    this.registerListener('market:heatmap', handler);
    return () => {
      this.socket?.off('market:heatmap', handler);
      this.removeListener('market:heatmap', handler);
      this.marketRefCount--;
      if (this.marketRefCount <= 0) {
        this.marketRefCount = 0;
        this.subscribedMarket = false;
        this.socket?.emit('unsubscribe:market');
      }
    };
  }

  subscribeIndexUpdates(callback: (update: { name: string; data: unknown }) => void): () => void {
    this.connect();

    this.marketRefCount++;
    if (!this.subscribedMarket) {
      this.subscribedMarket = true;
      this.socket?.emit('subscribe:market');
    }

    this._indexListeners.add(callback);
    this.marketRefCount++;
    return () => {
      this._indexListeners.delete(callback);
      this.marketRefCount--;
      if (this.marketRefCount <= 0) {
        this.marketRefCount = 0;
        this.subscribedMarket = false;
        this.socket?.emit('unsubscribe:market');
      }
    };
  }

  subscribeStock(
    symbol: string,
    callbacks: {
      onPrice?: EventCallback;
      onOrderBook?: EventCallback;
      onTrade?: EventCallback;
      onTradeExtra?: EventCallback;
      onExpectedPrice?: EventCallback;
      onForeign?: EventCallback;
      onOhlc?: EventCallback;
      onOhlcClosed?: EventCallback;
      onSecDef?: EventCallback;
    }
  ): () => void {
    this.connect();
    const sym = symbol.toUpperCase();

    const handlers: Array<[string, EventCallback]> = [];

    const channelMap: Record<string, EventCallback | undefined> = {
      [`stock:price:${sym}`]: callbacks.onPrice,
      [`stock:orderbook:${sym}`]: callbacks.onOrderBook,
      [`stock:trades:${sym}`]: callbacks.onTrade,
      [`stock:tradeExtra:${sym}`]: callbacks.onTradeExtra,
      [`stock:expectedPrice:${sym}`]: callbacks.onExpectedPrice,
      [`stock:foreign:${sym}`]: callbacks.onForeign,
      [`stock:ohlc:${sym}`]: callbacks.onOhlc,
      [`stock:ohlcClosed:${sym}`]: callbacks.onOhlcClosed,
      [`stock:secDef:${sym}`]: callbacks.onSecDef,
    };

    for (const [event, cb] of Object.entries(channelMap)) {
      if (cb) {
        const handler = (data: unknown) => cb(data);
        this.socket?.on(event, handler);
        this.registerListener(event, handler);
        handlers.push([event, handler]);
      }
    }

    const count = this.symbolRefCounts.get(sym) ?? 0;
    this.symbolRefCounts.set(sym, count + 1);

    if (!this.subscribedSymbols.has(sym)) {
      this.subscribedSymbols.add(sym);
      this.socket?.emit('subscribe:symbol', sym);
    }

    return () => {
      handlers.forEach(([event, handler]) => {
        this.socket?.off(event, handler);
        this.removeListener(event, handler);
      });

      const currentCount = this.symbolRefCounts.get(sym) ?? 1;
      if (currentCount <= 1) {
        this.symbolRefCounts.delete(sym);
        this.subscribedSymbols.delete(sym);
        this.socket?.emit('unsubscribe:symbol', sym);
      } else {
        this.symbolRefCounts.set(sym, currentCount - 1);
      }
    };
  }

  subscribeAlerts(callback: EventCallback): () => void {
    this.connect();
    const handler = (data: unknown) => callback(data);
    this.socket?.on('alert:triggered', handler);
    this.registerListener('alert:triggered', handler);
    return () => {
      this.socket?.off('alert:triggered', handler);
      this.removeListener('alert:triggered', handler);
    };
  }

  private registerListener(event: string, callback: EventCallback) {
    if (!this.listeners.has(event)) {
      this.listeners.set(event, new Set());
    }
    this.listeners.get(event)!.add(callback);
  }

  private removeListener(event: string, callback: EventCallback) {
    this.listeners.get(event)?.delete(callback);
  }

  isConnected(): boolean {
    return this.socket?.connected ?? false;
  }

  getSocket(): Socket | null {
    return this.socket;
  }

  emit(event: string, ...args: unknown[]): void {
    if (this.socket?.connected) {
      this.socket.emit(event, ...args);
    }
  }
}

export const socketClient = new SocketClient();
