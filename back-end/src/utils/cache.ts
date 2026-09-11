import { redisService } from '../services/redis.service';

export async function cached<T>(
  key: string,
  ttlSeconds: number,
  fetcher: () => Promise<T>,
): Promise<T> {
  // Redis is an optional acceleration layer. Market and workspace reads must
  // continue from PostgreSQL/upstream when Redis is unavailable or misconfigured.
  const hit = await redisService.getCache<T>(key).catch(() => null);
  if (hit !== null) return hit;

  const data = await fetcher();
  await redisService.setCache(key, data, ttlSeconds).catch(() => undefined);
  return data;
}
