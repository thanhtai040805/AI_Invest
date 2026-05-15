import { Queue, Worker } from 'bullmq';
import { config } from '../config';
import { redisService } from './redis.service';
import { aiEngineService } from './aiEngine.service';
import { socketService } from './socket.service';
import { subscriptionService } from './subscription.service';
import { syncStocksFromEngine, backfillOhlcv } from './stockSync.service';

function parseRedisConnection(): { host: string; port: number } {
  const url = new URL(config.redisUrl);
  return {
    host: url.hostname || 'localhost',
    port: parseInt(url.port || '6379', 10),
  };
}

const REDIS_CONNECTION = parseRedisConnection();

const marketQueue = new Queue('market-sync', { connection: REDIS_CONNECTION });
const stockQueue = new Queue('stock-sync', { connection: REDIS_CONNECTION });
const ohlcvQueue = new Queue('ohlcv-backfill', { connection: REDIS_CONNECTION });

let marketWorker: Worker | null = null;
let stockWorker: Worker | null = null;
let ohlcvWorker: Worker | null = null;

const MAX_REALTIME_SYMBOLS = 30;

export function initScheduler(): void {
  marketWorker = new Worker(
    'market-sync',
    async (job) => {
      const { type } = job.data;

      if (type === 'indices') {
        const data = await aiEngineService.getIndices();
        await redisService.setCache('market:indices', data, config.cacheTtl.indices);
        socketService.emitMarketIndices(data);
      }

      if (type === 'breadth') {
        const data = await aiEngineService.getMarketBreadth();
        await redisService.setCache('market:breadth', data, config.cacheTtl.breadth);
        socketService.emitMarketBreadth(data);
      }

      if (type === 'snapshot') {
        const data = await aiEngineService.getMarketSnapshot();
        await redisService.setCache('market:snapshot', data, config.cacheTtl.snapshot);
        socketService.emitMarketSnapshot(data);
      }

      if (type === 'stock-ticks') {
        const symbols = (await subscriptionService.getSubscribedSymbols()).slice(0, MAX_REALTIME_SYMBOLS);
        if (symbols.length === 0) return;

        await Promise.all(
          symbols.map(async (symbol) => {
            try {
              const [quote, orderbook] = await Promise.all([
                aiEngineService.getQuote(symbol),
                aiEngineService.getOrderBook(symbol),
              ]);
              await redisService.setCache(`stock:${symbol}:quote`, quote, config.cacheTtl.quote);
              await redisService.setCache(`stock:${symbol}:orderbook`, orderbook, config.cacheTtl.orderbook);
              socketService.emitStockPrice(symbol, quote);
              socketService.emitOrderBook(symbol, orderbook);
            } catch (err) {
              console.error(`[Scheduler] Realtime tick failed for ${symbol}:`, err);
            }
          }),
        );
      }
    },
    { connection: REDIS_CONNECTION, concurrency: 3 },
  );

  stockWorker = new Worker(
    'stock-sync',
    async (job) => {
      const { type } = job.data;
      if (type === 'daily') {
        const count = await syncStocksFromEngine();
        console.log(`[Scheduler] Synced ${count} stocks to database`);
      }
    },
    { connection: REDIS_CONNECTION, concurrency: 1 },
  );

  ohlcvWorker = new Worker(
    'ohlcv-backfill',
    async (job) => {
      const { symbol, interval, start, end } = job.data;
      const count = await backfillOhlcv(symbol, interval ?? '1D', start, end);
      console.log(`[Scheduler] Backfilled ${count} OHLCV rows for ${symbol}`);
      return { symbol, count };
    },
    { connection: REDIS_CONNECTION, concurrency: 2 },
  );

  marketWorker.on('failed', (job, err) => {
    console.error(`[Scheduler] market-sync job ${job?.id} failed:`, err.message);
  });

  scheduleRecurringJobs();
  console.log('[Scheduler] Workers initialized');
}

async function scheduleRecurringJobs(): Promise<void> {
  for (const queue of [marketQueue, stockQueue]) {
    const existing = await queue.getRepeatableJobs();
    for (const job of existing) {
      await queue.removeRepeatableByKey(job.key);
    }
  }

  // When DNSE WebSocket relay is active, skip HTTP polling (avoids rate limits)
  if (!config.dnse.enabled) {
    await marketQueue.add('sync-indices', { type: 'indices' }, {
      repeat: { every: 3000 },
      removeOnComplete: 10,
      removeOnFail: 5,
    });

    await marketQueue.add('sync-breadth', { type: 'breadth' }, {
      repeat: { every: 10000 },
      removeOnComplete: 10,
      removeOnFail: 5,
    });

    await marketQueue.add('sync-snapshot', { type: 'snapshot' }, {
      repeat: { every: 5000 },
      removeOnComplete: 10,
      removeOnFail: 5,
    });

    await marketQueue.add('stock-ticks', { type: 'stock-ticks' }, {
      repeat: { every: 1000 },
      removeOnComplete: 5,
      removeOnFail: 5,
    });
    console.log('[Scheduler] Legacy poll jobs scheduled (DNSE disabled)');
  } else {
    console.log('[Scheduler] Market poll skipped — DNSE WebSocket relay active');
  }

  await stockQueue.add('daily-stock-sync', { type: 'daily' }, {
    repeat: { every: 24 * 60 * 60 * 1000 },
    removeOnComplete: 3,
    removeOnFail: 3,
  });

  console.log('[Scheduler] Recurring jobs scheduled');
}

/** Queue OHLCV history backfill for a symbol */
export async function queueOhlcvBackfill(
  symbol: string,
  options?: { interval?: string; start?: string; end?: string },
): Promise<string> {
  const job = await ohlcvQueue.add('backfill', {
    symbol: symbol.toUpperCase(),
    interval: options?.interval ?? '1D',
    start: options?.start,
    end: options?.end,
  });
  return job.id ?? '';
}

export function shutdownScheduler(): void {
  marketWorker?.close();
  stockWorker?.close();
  ohlcvWorker?.close();
}
