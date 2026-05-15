import Redis from 'ioredis';
import { config } from '../config';
import { redisService } from './redis.service';
import { socketService } from './socket.service';

/**
 * Subscribes to DNSE events published by the AI engine WebSocket hub.
 * Channel pattern: dnse:event:{type} e.g. dnse:event:quote:FPT
 */
class DnseRelayService {
  private subscriber: Redis | null = null;

  async start(): Promise<void> {
    if (!config.dnse.enabled) {
      console.log('[DNSE Relay] Disabled — using legacy poll scheduler');
      return;
    }

    const url = new URL(config.redisUrl);
    this.subscriber = new Redis({
      host: url.hostname || 'localhost',
      port: parseInt(url.port || '6379', 10),
    });

    const pattern = `${config.dnse.redisChannelPrefix}:*`;
    await this.subscriber.psubscribe(pattern);

    this.subscriber.on('pmessage', (_pattern, channel, message) => {
      try {
        this.handleMessage(channel, JSON.parse(message));
      } catch (err) {
        console.error('[DNSE Relay] Invalid message:', err);
      }
    });

    console.log(`[DNSE Relay] Listening on ${pattern}`);
  }

  private handleMessage(channel: string, data: unknown): void {
    const prefix = `${config.dnse.redisChannelPrefix}:`;
    if (!channel.startsWith(prefix)) return;
    const suffix = channel.slice(prefix.length);

    if (suffix === 'indices') {
      socketService.emitMarketIndices(data);
      void redisService.setCache('market:indices', data, config.cacheTtl.indices);
      return;
    }

    if (suffix === 'breadth') {
      socketService.emitMarketBreadth(data);
      void redisService.setCache('market:breadth', data, config.cacheTtl.breadth);
      return;
    }

    if (suffix === 'snapshot') {
      socketService.emitMarketSnapshot(data);
      void redisService.setCache('market:snapshot', data, config.cacheTtl.snapshot);
      return;
    }

    if (suffix.startsWith('quote:')) {
      const symbol = suffix.replace('quote:', '').toUpperCase();
      socketService.emitStockPrice(symbol, data);
      return;
    }

    if (suffix.startsWith('orderbook:')) {
      const symbol = suffix.replace('orderbook:', '').toUpperCase();
      socketService.emitOrderBook(symbol, data);
    }
  }

  async stop(): Promise<void> {
    if (this.subscriber) {
      await this.subscriber.quit();
      this.subscriber = null;
    }
  }
}

export const dnseRelayService = new DnseRelayService();
