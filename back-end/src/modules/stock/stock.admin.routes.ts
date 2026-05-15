import { Router, Request, Response, NextFunction } from 'express';
import { z } from 'zod';
import { queueOhlcvBackfill } from '../../services/scheduler.service';
import { syncStocksFromEngine } from '../../services/stockSync.service';

const router = Router();

const backfillSchema = z.object({
  symbol: z.string().min(1),
  interval: z.string().optional(),
  start: z.string().optional(),
  end: z.string().optional(),
});

async function handle(
  req: Request,
  res: Response,
  next: NextFunction,
  fn: () => Promise<unknown>,
): Promise<void> {
  try {
    res.json(await fn());
  } catch (err) {
    next(err);
  }
}

/** Trigger manual stock master sync from vnstock */
router.post('/sync/stocks', (req, res, next) =>
  handle(req, res, next, async () => {
    const count = await syncStocksFromEngine();
    return { synced: count };
  }),
);

/** Queue OHLCV history backfill job */
router.post('/sync/ohlcv', (req, res, next) => {
  const body = backfillSchema.parse(req.body);
  return handle(req, res, next, async () => {
    const jobId = await queueOhlcvBackfill(body.symbol, {
      interval: body.interval,
      start: body.start,
      end: body.end,
    });
    return { jobId, status: 'queued' };
  });
});

export default router;
