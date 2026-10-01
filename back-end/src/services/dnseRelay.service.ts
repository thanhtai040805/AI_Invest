import Redis from 'ioredis';
import { config } from '../config';
import { redisService } from './redis.service';
import { socketService } from './socket.service';

const CACHE_TTL: Record<string, number> = {
  indices: 15,
  breadth: 5,
  snapshot: 3,
  liquidity: 5,
  heatmap: 10,
  trade: 0,
  tradeExtra: 2,
  foreign: 5,
  expectedPrice: 2,
  ohlc: 2,
  ohlcClosed: 10,
  secDef: 3600,
};

const STREAM_KEYS: Record<string, string> = {
  trade: 'dnse:stream:trade:',
  ohlcClosed: 'dnse:stream:ohlc_closed:',
};

class DnseRelayService {
  private subscriber: Redis | null = null;
  private retryTimer: NodeJS.Timeout | null = null;
  private starting = false;
  private stopping = false;

  private retry(): void {
    if (this.stopping || this.retryTimer) return;
    this.retryTimer = setTimeout(() => {
      this.retryTimer = null;
      void this.start();
    }, 5000);
  }

  async start(): Promise<void> {
    if (!config.dnse.enabled) {
      console.log('[DNSE Relay] Disabled — using legacy poll scheduler');
      return;
    }

    if (this.starting || this.subscriber) return;
    this.starting = true;
    const url = new URL(config.redisUrl);
    let subscriber: Redis | null = null;
    try {
      subscriber = new Redis({
        host: url.hostname || 'localhost',
        port: parseInt(url.port || '6379', 10),
        username: url.username || undefined,
        password: url.password ? decodeURIComponent(url.password) : undefined,
        db: url.pathname.length > 1 ? Number(url.pathname.slice(1)) : undefined,
        maxRetriesPerRequest: 1,
        retryStrategy: () => null,
        lazyConnect: true,
        enableOfflineQueue: false,
      });

      subscriber.on('error', () => {});
      await subscriber.connect();
      this.subscriber = subscriber;
      subscriber.on('end', () => {
        if (this.subscriber === subscriber) this.subscriber = null;
        this.retry();
      });
      subscriber.on('pmessage', (_pattern, channel, message) => {
        try {
          this.handleMessage(channel, JSON.parse(message));
        } catch (err) {
          console.error('[DNSE Relay] Invalid message:', err);
        }
      });
      const pattern = `${config.dnse.redisChannelPrefix}:*`;
      await this.replayMissedStreams();
      await subscriber.psubscribe(pattern);
      console.log('[DNSE Relay] Redis subscriber connected successfully');
      console.log(`[DNSE Relay] Listening on ${pattern}`);
    } catch {
      console.log('[DNSE Relay] Redis offline — WebSocket relay standing by');
      if (subscriber) subscriber.disconnect();
      if (this.subscriber === subscriber) this.subscriber = null;
      this.retry();
    } finally {
      this.starting = false;
    }
  }

  private async replayMissedStreams(): Promise<void> {
    if (!this.subscriber) return;

    console.log('[DNSE Relay] Restoring latest Redis Stream values...');
    let totalReplayed = 0;

    for (const prefix of Object.values(STREAM_KEYS)) {
      try {
        let cursor = '0';
        do {
          const [next, keys] = await this.subscriber.scan(cursor, 'MATCH', `${prefix}*`, 'COUNT', 100);
          cursor = next;
          for (const key of keys) {
            const [entry] = await this.subscriber.xrevrange(key, '+', '-', 'COUNT', 1);
            if (!entry) continue;
            const fieldsArr = entry[1];
            const fields: Record<string, string> = {};
            for (let i = 0; i < fieldsArr.length; i += 2) fields[fieldsArr[i]] = fieldsArr[i + 1];
            try {
              const data = { ...JSON.parse(fields.data), isSnapshot: true };
              const suffix = key.replace('dnse:stream:', '');
              this.handleMessage(`${config.dnse.redisChannelPrefix}:${suffix}`, data);
              totalReplayed++;
            } catch {
              // skip malformed entries
            }
          }
        } while (cursor !== '0');
      } catch (err) {
        console.warn(`[DNSE Relay] Stream replay failed for ${prefix}:`, err);
      }
    }

    if (totalReplayed > 0) {
      console.log(`[DNSE Relay] Restored ${totalReplayed} latest values from Redis Streams`);
    } else {
      console.log('[DNSE Relay] No missed messages in Redis Streams');
    }
  }

  private handleMessage(channel: string, data: unknown): void {
    const prefix = `${config.dnse.redisChannelPrefix}:`;
    if (!channel.startsWith(prefix)) return;
    const suffix = channel.slice(prefix.length);

    switch (true) {
      case suffix === 'indices':
        socketService.emitMarketIndices(data);
        void redisService.setCache('market:indices', data, CACHE_TTL.indices);
        break;

      case suffix === 'breadth':
        socketService.emitMarketBreadth(data);
        void redisService.setCache('market:breadth', data, CACHE_TTL.breadth);
        break;

      case suffix === 'snapshot':
        socketService.emitMarketSnapshot(data);
        void redisService.setCache('market:snapshot', data, CACHE_TTL.snapshot);
        break;

      case suffix === 'liquidity':
        socketService.emitMarketLiquidity(data);
        void redisService.setCache('market:liquidity', data, CACHE_TTL.liquidity);
        break;

      case suffix === 'heatmap':
        socketService.emitMarketHeatmap(data);
        void redisService.setCache('market:heatmap', data, CACHE_TTL.heatmap);
        break;

      case suffix.startsWith('index:'): {
        const name = suffix.replace('index:', '').toUpperCase();
        socketService.emitIndexUpdate(name, data);
        void redisService.setCache(`index:${name}`, data, CACHE_TTL.indices);
        break;
      }

      case suffix.startsWith('trade:'): {
        const symbol = suffix.replace('trade:', '').toUpperCase();
        socketService.emitStockPrice(symbol, data);
        if (!(data as { isSnapshot?: boolean }).isSnapshot) socketService.emitTrade(symbol, data);
        void redisService.setCache(`stock:${symbol}:quote`, data, CACHE_TTL.trade);
        break;
      }

      case suffix.startsWith('trade_extra:'): {
        const symbol = suffix.replace('trade_extra:', '').toUpperCase();
        socketService.emitTradeExtra(symbol, data);
        void redisService.setCache(`stock:${symbol}:tradeExtra`, data, CACHE_TTL.tradeExtra);
        break;
      }

      case suffix.startsWith('orderbook:'): {
        const symbol = suffix.replace('orderbook:', '').toUpperCase();
        socketService.emitOrderBook(symbol, data);
        break;
      }

      case suffix.startsWith('foreign:'): {
        const symbol = suffix.replace('foreign:', '').toUpperCase();
        socketService.emitForeignTrading(symbol, data);
        void redisService.setCache(`stock:${symbol}:foreign`, data, CACHE_TTL.foreign);
        break;
      }

      case suffix.startsWith('expected_price:'): {
        const symbol = suffix.replace('expected_price:', '').toUpperCase();
        socketService.emitExpectedPrice(symbol, data);
        void redisService.setCache(`stock:${symbol}:expectedPrice`, data, CACHE_TTL.expectedPrice);
        break;
      }

      case suffix.startsWith('ohlc_closed:'): {
        const symbol = suffix.replace('ohlc_closed:', '').toUpperCase();
        socketService.emitOhlcClosed(symbol, data);
        void redisService.setCache(`stock:${symbol}:ohlcClosed`, data, CACHE_TTL.ohlcClosed);
        break;
      }

      case suffix.startsWith('ohlc:'): {
        const symbol = suffix.replace('ohlc:', '').toUpperCase();
        socketService.emitOhlc(symbol, data);
        void redisService.setCache(`stock:${symbol}:ohlc`, data, CACHE_TTL.ohlc);
        break;
      }

      case suffix.startsWith('sec_def:'): {
        const symbol = suffix.replace('sec_def:', '').toUpperCase();
        socketService.emitSecurityDefinition(symbol, data);
        void redisService.setCache(`stock:${symbol}:secDef`, data, CACHE_TTL.secDef);
        break;
      }

      default:
        console.warn(`[DNSE Relay] Unhandled channel: ${suffix}`);
    }
  }

  async stop(): Promise<void> {
    this.stopping = true;
    if (this.retryTimer) clearTimeout(this.retryTimer);
    this.retryTimer = null;
    if (this.subscriber) {
      await this.subscriber.quit();
      this.subscriber = null;
    }
  }
}

export const dnseRelayService = new DnseRelayService();
