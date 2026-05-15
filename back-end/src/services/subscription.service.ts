import { redisService } from './redis.service';

const SUBSCRIBED_SYMBOLS_KEY = 'socket:subscribed:symbols';
const SUBSCRIBED_MARKET_KEY = 'socket:subscribed:market';

class SubscriptionService {
  async addSymbol(symbol: string): Promise<void> {
    await redisService.getClient().sadd(SUBSCRIBED_SYMBOLS_KEY, symbol.toUpperCase());
  }

  async removeSymbol(symbol: string): Promise<void> {
    await redisService.getClient().srem(SUBSCRIBED_SYMBOLS_KEY, symbol.toUpperCase());
  }

  async getSubscribedSymbols(): Promise<string[]> {
    return redisService.getClient().smembers(SUBSCRIBED_SYMBOLS_KEY);
  }

  async incrementMarketSubscribers(): Promise<void> {
    await redisService.getClient().incr(SUBSCRIBED_MARKET_KEY);
  }

  async decrementMarketSubscribers(): Promise<void> {
    const count = await redisService.getClient().decr(SUBSCRIBED_MARKET_KEY);
    if (count < 0) {
      await redisService.getClient().set(SUBSCRIBED_MARKET_KEY, '0');
    }
  }

  async hasMarketSubscribers(): Promise<boolean> {
    const count = await redisService.getClient().get(SUBSCRIBED_MARKET_KEY);
    return parseInt(count ?? '0', 10) > 0;
  }
}

export const subscriptionService = new SubscriptionService();
