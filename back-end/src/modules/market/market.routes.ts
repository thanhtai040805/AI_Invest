import { Router, Request, Response, NextFunction } from 'express';
import { config } from '../../config';
import { aiEngineService } from '../../services/aiEngine.service';
import { cached } from '../../utils/cache';

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

router.get('/search', (req, res, next) => {
  const q = (req.query.q as string) ?? '';
  if (!q.trim()) {
    res.json([]);
    return;
  }
  return handle(req, res, next, () => aiEngineService.searchSymbols(q));
});

export default router;
