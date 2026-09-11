import { redisService } from './redis.service';

const SUBSCRIBED_SYMBOLS_KEY = 'socket:subscribed:symbols';
const SUBSCRIBED_MARKET_KEY = 'socket:subscribed:market';

class SubscriptionService {
  private memSymbols = new Set<string>();
  private memMarketCount = 0;

  async addSymbol(symbol: string): Promise<void> {
    const sym = symbol.toUpperCase();
    this.memSymbols.add(sym);
    try {
      await redisService.getClient().sadd(SUBSCRIBED_SYMBOLS_KEY, sym);
    } catch {}
  }

  async removeSymbol(symbol: string): Promise<void> {
    const sym = symbol.toUpperCase();
    this.memSymbols.delete(sym);
    try {
      await redisService.getClient().srem(SUBSCRIBED_SYMBOLS_KEY, sym);
    } catch {}
  }

  async getSubscribedSymbols(): Promise<string[]> {
    try {
      return await redisService.getClient().smembers(SUBSCRIBED_SYMBOLS_KEY);
    } catch {
      return Array.from(this.memSymbols);
    }
  }

  async incrementMarketSubscribers(): Promise<void> {
    this.memMarketCount++;
    try {
      await redisService.getClient().incr(SUBSCRIBED_MARKET_KEY);
    } catch {}
  }

  async decrementMarketSubscribers(): Promise<void> {
    this.memMarketCount = Math.max(0, this.memMarketCount - 1);
    try {
      const count = await redisService.getClient().decr(SUBSCRIBED_MARKET_KEY);
      if (count < 0) {
        await redisService.getClient().set(SUBSCRIBED_MARKET_KEY, '0');
      }
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
