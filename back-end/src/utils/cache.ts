import { redisService } from '../services/redis.service';

interface L1Entry<T = unknown> {
  value: T;
  expiresAt: number;
  staleUntil: number;
}

const MAX_L1_ENTRIES = 500;
const l1Cache = new Map<string, L1Entry>();
const inFlight = new Map<string, Promise<unknown>>();

function setL1<T>(key: string, value: T, ttlSeconds: number): void {
  if (value === null || value === undefined) return;
  const now = Date.now();
  const freshMs = Math.max(1_000, ttlSeconds * 1_000);
  // Keep a generous stale window (at least 90s or 6x TTL) for instant SWR reads
  const staleMs = Math.max(90_000, freshMs * 6);
  if (!l1Cache.has(key) && l1Cache.size >= MAX_L1_ENTRIES) {
    const oldestKey = l1Cache.keys().next().value;
    if (oldestKey !== undefined) l1Cache.delete(oldestKey);
  }
  l1Cache.set(key, {
    value,
    expiresAt: now + freshMs,
    staleUntil: now + staleMs,
  });
}

function fetchAndStore<T>(
  key: string,
  ttlSeconds: number,
  fetcher: () => Promise<T>,
): Promise<T> {
  const existing = inFlight.get(key) as Promise<T> | undefined;
  if (existing) return existing;

  const promise = (async () => {
    try {
      const data = await fetcher();
      setL1(key, data, ttlSeconds);
      await redisService.setCache(key, data, ttlSeconds).catch(() => undefined);
      return data;
    } finally {
      inFlight.delete(key);
    }
  })();

  inFlight.set(key, promise);
  return promise;
}

export async function cached<T>(
  key: string,
  ttlSeconds: number,
  fetcher: () => Promise<T>,
): Promise<T> {
  const now = Date.now();
  const l1 = l1Cache.get(key) as L1Entry<T> | undefined;

  if (l1) {
    if (now < l1.expiresAt) {
      return l1.value;
    }
    if (now < l1.staleUntil) {
      // Stale-While-Revalidate: return cached value immediately and refresh asynchronously
      void fetchAndStore(key, ttlSeconds, fetcher).catch(() => undefined);
      return l1.value;
    }
    l1Cache.delete(key);
  }

  // L2 Redis check (optional acceleration layer when Redis is online)
  const hit = await redisService.getCache<T>(key).catch(() => null);
  if (hit !== null) {
    setL1(key, hit, ttlSeconds);
    return hit;
  }

  return fetchAndStore(key, ttlSeconds, fetcher);
}
