import { redisService } from './redis.service';

const SUBSCRIBED_SYMBOLS_KEY = 'socket:subscribed:symbol-counts:v2';
const SUBSCRIBED_MARKET_KEY = 'socket:subscribed:market';

class SubscriptionService {
  private memSymbols = new Map<string, number>();
  private memMarketCount = 0;

  async addSymbol(symbol: string): Promise<void> {
    const sym = symbol.toUpperCase();
    this.memSymbols.set(sym, (this.memSymbols.get(sym) ?? 0) + 1);
    try {
      await redisService.getClient().hincrby(SUBSCRIBED_SYMBOLS_KEY, sym, 1);
    } catch {}
  }

  async removeSymbol(symbol: string): Promise<void> {
    const sym = symbol.toUpperCase();
    const next = Math.max(0, (this.memSymbols.get(sym) ?? 0) - 1);
    if (next === 0) this.memSymbols.delete(sym);
    else this.memSymbols.set(sym, next);
    try {
      const count = await redisService.getClient().hincrby(SUBSCRIBED_SYMBOLS_KEY, sym, -1);
      if (count <= 0) await redisService.getClient().hdel(SUBSCRIBED_SYMBOLS_KEY, sym);
    } catch {}
  }

  async getSubscribedSymbols(): Promise<string[]> {
    try {
      const counts = await redisService.getClient().hgetall(SUBSCRIBED_SYMBOLS_KEY);
      return Object.entries(counts).filter(([, count]) => Number(count) > 0).map(([symbol]) => symbol);
    } catch {
      return Array.from(this.memSymbols.keys());
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
