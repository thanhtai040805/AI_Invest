import Redis from 'ioredis';
import { config } from '../config';

class RedisService {
  private client: Redis | null = null;
  private retryTimer: NodeJS.Timeout | null = null;
  private stopping = false;
  private connectedHandlers: Array<() => Promise<void>> = [];

  onConnected(handler: () => Promise<void>): void {
    this.connectedHandlers.push(handler);
  }

  private retry(): void {
    if (this.stopping || this.retryTimer) return;
    this.retryTimer = setTimeout(() => {
      this.retryTimer = null;
      void this.connect();
    }, 5000);
  }

  async connect(): Promise<void> {
    if (this.client) return;

    const client = new Redis(config.redisUrl, {
      maxRetriesPerRequest: 1,
      retryStrategy: () => null,
      lazyConnect: true,
      enableOfflineQueue: false,
    });

    this.client = client;
    client.on('error', () => {});
    client.on('end', () => {
      if (this.client === client) this.client = null;
      this.retry();
    });

    try {
      await client.connect();
      console.log('[Redis] Connected');
      for (const handler of this.connectedHandlers) await handler().catch((err) => console.warn('[Redis] Registry sync failed:', err));
    } catch {
      console.log('[Redis] Redis offline — running in direct PostgreSQL mode');
      client.disconnect();
      if (this.client === client) this.client = null;
      this.retry();
    }
  }

  getClient(): Redis {
    if (!this.client) {
      throw new Error('Redis not connected. Call connect() first.');
    }
    return this.client;
  }

  async getCache<T>(key: string): Promise<T | null> {
    const raw = await this.getClient().get(key);
    if (!raw) return null;
    try {
      return JSON.parse(raw) as T;
    } catch {
      return null;
    }
  }

  async getCacheMany<T>(keys: string[]): Promise<(T | null)[]> {
    if (!keys.length) return [];
    const rawValues = await this.getClient().mget(...keys);
    return rawValues.map((raw) => {
      if (!raw) return null;
      try {
        return JSON.parse(raw) as T;
      } catch {
        return null;
      }
    });
  }

  async setCache(key: string, value: unknown, ttlSeconds: number): Promise<void> {
    await this.getClient().set(key, JSON.stringify(value), 'EX', ttlSeconds);
  }

  async deleteCache(key: string): Promise<void> {
    await this.getClient().del(key);
  }

  async disconnect(): Promise<void> {
    this.stopping = true;
    if (this.retryTimer) clearTimeout(this.retryTimer);
    this.retryTimer = null;
    if (this.client) {
      await this.client.quit();
      this.client = null;
    }
  }
}

export const redisService = new RedisService();
