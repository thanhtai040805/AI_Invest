import Redis from 'ioredis';
import { config } from '../config';
import { redisService } from './redis.service';
import { socketService } from './socket.service';

const CACHE_TTL: Record<string, number> = {
  indices: 3,
  breadth: 5,
  snapshot: 3,
  liquidity: 5,
  heatmap: 10,
  trade: 2,
  tradeExtra: 2,
  orderbook: 2,
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

const MAX_REPLAY_PER_STREAM = 50;

class DnseRelayService {
  private subscriber: Redis | null = null;
  private lastStreamIds: Map<string, string> = new Map();

  async start(): Promise<void> {
    if (!config.dnse.enabled) {
      console.log('[DNSE Relay] Disabled — using legacy poll scheduler');
      return;
    }

    const url = new URL(config.redisUrl);
    this.subscriber = new Redis({
      host: url.hostname || 'localhost',
      port: parseInt(url.port || '6379', 10),
      maxRetriesPerRequest: 3,
      lazyConnect: true,
    });

    this.subscriber.on('error', (err) => {
      console.error('[DNSE Relay] Redis subscriber error:', err.message);
    });

    await this.replayMissedStreams();

    const pattern = `${config.dnse.redisChannelPrefix}:*`;
    await this.subscriber.psubscribe(pattern);

    this.subscriber.on('pmessage', (_pattern, channel, message) => {
      try {
        const data = JSON.parse(message);
        this.handleMessage(channel, data);
      } catch (err) {
        console.error('[DNSE Relay] Invalid message:', err);
      }
    });

    console.log(`[DNSE Relay] Listening on ${pattern}`);
  }

  private async replayMissedStreams(): Promise<void> {
    if (!this.subscriber) return;

    console.log('[DNSE Relay] Checking Redis Streams for missed messages...');
    let totalReplayed = 0;

    for (const [type, prefix] of Object.entries(STREAM_KEYS)) {
      try {
        const keys = await this.subscriber.keys(`${prefix}*`);
        for (const key of keys) {
          const lastId = this.lastStreamIds.get(key) || '0';
          const entries = await this.subscriber.xrange(key, lastId, '+', 'COUNT', MAX_REPLAY_PER_STREAM);

          if (entries && Array.isArray(entries)) {
            for (const entry of entries) {
              try {
                const [id, fieldsArr] = entry as [string, string[]];
                const fields: Record<string, string> = {};
                for (let i = 0; i < fieldsArr.length; i += 2) {
                  fields[fieldsArr[i]] = fieldsArr[i + 1];
                }
                const data = JSON.parse(fields.data);
                const suffix = key.replace('dnse:stream:', '');
                this.handleMessage(`${config.dnse.redisChannelPrefix}:${suffix}`, data);
                totalReplayed++;
                this.lastStreamIds.set(key, id);
              } catch {
                // skip malformed entries
              }
            }
          }
        }
      } catch (err) {
        console.warn(`[DNSE Relay] Stream replay failed for ${prefix}:`, err);
      }
    }

    if (totalReplayed > 0) {
      console.log(`[DNSE Relay] Replayed ${totalReplayed} missed messages from Redis Streams`);
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
        socketService.emitTrade(symbol, data);
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
        void redisService.setCache(`stock:${symbol}:orderbook`, data, CACHE_TTL.orderbook);
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
    if (this.subscriber) {
      await this.subscriber.quit();
      this.subscriber = null;
    }
  }
}

export const dnseRelayService = new DnseRelayService();
