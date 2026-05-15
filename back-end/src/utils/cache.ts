import { redisService } from '../services/redis.service';

export async function cached<T>(
  key: string,
  ttlSeconds: number,
  fetcher: () => Promise<T>,
): Promise<T> {
  const hit = await redisService.getCache<T>(key);
  if (hit !== null) return hit;

  const data = await fetcher();
  await redisService.setCache(key, data, ttlSeconds);
  return data;
}
