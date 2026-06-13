import { Router, Request, Response, NextFunction } from 'express';
import { config } from '../../config';
import { aiEngineService } from '../../services/aiEngine.service';
import { cached } from '../../utils/cache';
import prisma from '../../config/database';
import { autoBackfillIfNeeded, getBackfillHistory } from '../../services/backfill.service';

const router = Router();

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

router.get('/indices', (req, res, next) =>
  handle(req, res, next, () =>
    cached('market:indices', config.cacheTtl.indices, () => aiEngineService.getIndices()),
  ),
);

router.get('/breadth', (req, res, next) =>
  handle(req, res, next, () =>
    cached('market:breadth', config.cacheTtl.breadth, () => aiEngineService.getMarketBreadth()),
  ),
);

router.get('/liquidity', (req, res, next) =>
  handle(req, res, next, () =>
    cached('market:liquidity', config.cacheTtl.snapshot, () => aiEngineService.getLiquidity()),
  ),
);

router.get('/snapshot', (req, res, next) => {
  const exchange = req.query.exchange as string | undefined;
  const cacheKey = exchange ? `market:snapshot:${exchange}` : 'market:snapshot';
  return handle(req, res, next, () =>
    cached(cacheKey, config.cacheTtl.snapshot, () => aiEngineService.getMarketSnapshot(exchange)),
  );
});

router.get('/heatmap', (req, res, next) =>
  handle(req, res, next, () =>
    cached('market:heatmap', config.cacheTtl.snapshot, () => aiEngineService.getHeatmap()),
  ),
);

router.get('/news', (req, res, next) => {
  const symbol = req.query.symbol as string | undefined;
  const limit = Math.min(parseInt(req.query.limit as string, 10) || 30, 200);
  return handle(req, res, next, async () => {
    const where = symbol ? `WHERE symbol = $1` : ``;
    const sql = `SELECT id, symbol, title, url, published_date, article_content, article_pdf_text, sentiment_score FROM news_events ${where} ORDER BY published_date DESC LIMIT $${symbol ? 2 : 1}::int`;
    const params = symbol ? [symbol.toUpperCase(), limit] : [limit];
    return prisma.$queryRawUnsafe(sql, ...params);
  });
});

router.get('/search', (req, res, next) => {
  const q = (req.query.q as string) ?? '';
  if (!q.trim()) {
    res.json([]);
    return;
  }
  return handle(req, res, next, () => aiEngineService.searchSymbols(q));
});

router.post('/backfill/trigger', async (req, res, next) => {
  try {
    const result = await autoBackfillIfNeeded();
    res.json(result);
  } catch (err) {
    next(err);
  }
});

router.get('/backfill/history', async (req, res, next) => {
  try {
    const limit = parseInt(req.query.limit as string, 10) || 30;
    const history = await getBackfillHistory(limit);
    res.json(history);
  } catch (err) {
    next(err);
  }
});

export default router;
