import Redis from 'ioredis';
import { config } from '../config';

class RedisService {
  private client: Redis | null = null;

  async connect(): Promise<void> {
    if (this.client) return;

    this.client = new Redis(config.redisUrl, {
      maxRetriesPerRequest: 3,
      lazyConnect: true,
    });

    await this.client.connect();
    console.log('[Redis] Connected');
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

  async setCache(key: string, value: unknown, ttlSeconds: number): Promise<void> {
    await this.getClient().set(key, JSON.stringify(value), 'EX', ttlSeconds);
  }

  async deleteCache(key: string): Promise<void> {
    await this.getClient().del(key);
  }

  async disconnect(): Promise<void> {
    if (this.client) {
      await this.client.quit();
      this.client = null;
    }
  }
}

export const redisService = new RedisService();
