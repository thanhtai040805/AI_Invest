import { redisService } from './redis.service';

const SUBSCRIBED_SYMBOLS_KEY = 'socket:subscribed:symbol-counts:v2';
const SUBSCRIBED_OHLC_KEY = 'socket:subscribed:ohlc-counts:v1';
const SUBSCRIBED_MARKET_KEY = 'socket:subscribed:market';

class SubscriptionService {
  private memSymbols = new Map<string, number>();
  private memOhlc = new Map<string, number>();
  private memMarketCount = 0;

  constructor() {
    redisService.onConnected(() => this.syncToRedis());
  }

  private async syncToRedis(): Promise<void> {
    const client = redisService.getClient();
    const pipeline = client.pipeline().del(SUBSCRIBED_SYMBOLS_KEY, SUBSCRIBED_OHLC_KEY);
    for (const [symbol, count] of this.memSymbols) pipeline.hset(SUBSCRIBED_SYMBOLS_KEY, symbol, count);
    for (const [key, count] of this.memOhlc) pipeline.hset(SUBSCRIBED_OHLC_KEY, key, count);
    pipeline.set(SUBSCRIBED_MARKET_KEY, this.memMarketCount);
    await pipeline.exec();
  }

  async addSymbol(symbol: string): Promise<number> {
    const sym = symbol.toUpperCase();
    const next = (this.memSymbols.get(sym) ?? 0) + 1;
    this.memSymbols.set(sym, next);
    try {
      await redisService.getClient().hset(SUBSCRIBED_SYMBOLS_KEY, sym, next);
    } catch {}
    return next;
  }

  async removeSymbol(symbol: string): Promise<number> {
    const sym = symbol.toUpperCase();
    const next = Math.max(0, (this.memSymbols.get(sym) ?? 0) - 1);
    if (next === 0) this.memSymbols.delete(sym);
    else this.memSymbols.set(sym, next);
    try {
      if (next === 0) await redisService.getClient().hdel(SUBSCRIBED_SYMBOLS_KEY, sym);
      else await redisService.getClient().hset(SUBSCRIBED_SYMBOLS_KEY, sym, next);
    } catch {}
    return next;
  }

  async getSubscribedSymbols(): Promise<string[]> {
    try {
      const counts = await redisService.getClient().hgetall(SUBSCRIBED_SYMBOLS_KEY);
      return Object.entries(counts).filter(([, count]) => Number(count) > 0).map(([symbol]) => symbol);
    } catch {
      return Array.from(this.memSymbols.keys());
    }
  }

  async addOhlc(symbol: string, resolution: string): Promise<number> {
    const key = `${resolution}:${symbol.toUpperCase()}`;
    const next = (this.memOhlc.get(key) ?? 0) + 1;
    this.memOhlc.set(key, next);
    try {
      await redisService.getClient().hset(SUBSCRIBED_OHLC_KEY, key, next);
    } catch {}
    return next;
  }

  async removeOhlc(symbol: string, resolution: string): Promise<number> {
    const key = `${resolution}:${symbol.toUpperCase()}`;
    const next = Math.max(0, (this.memOhlc.get(key) ?? 0) - 1);
    if (next === 0) this.memOhlc.delete(key);
    else this.memOhlc.set(key, next);
    try {
      if (next === 0) await redisService.getClient().hdel(SUBSCRIBED_OHLC_KEY, key);
      else await redisService.getClient().hset(SUBSCRIBED_OHLC_KEY, key, next);
    } catch {}
    return next;
  }

  async incrementMarketSubscribers(): Promise<void> {
    this.memMarketCount++;
    try {
      await redisService.getClient().set(SUBSCRIBED_MARKET_KEY, this.memMarketCount);
    } catch {}
  }

  async decrementMarketSubscribers(): Promise<void> {
    this.memMarketCount = Math.max(0, this.memMarketCount - 1);
    try {
      await redisService.getClient().set(SUBSCRIBED_MARKET_KEY, this.memMarketCount);
    } catch {}
  }

  async hasMarketSubscribers(): Promise<boolean> {
    try {
      const count = await redisService.getClient().get(SUBSCRIBED_MARKET_KEY);
      return parseInt(count ?? '0', 10) > 0;
    } catch {
      return this.memMarketCount > 0;
    }
  }
}

export const subscriptionService = new SubscriptionService();
