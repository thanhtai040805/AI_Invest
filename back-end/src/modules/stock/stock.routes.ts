import { Router, Request, Response, NextFunction } from 'express';
import { config } from '../../config';
import { aiEngineService } from '../../services/aiEngine.service';
import { cached } from '../../utils/cache';
import prisma from '../../config/database';

const router = Router();

function symbolParam(req: Request): string {
  return (req.params.symbol ?? '').toUpperCase();
}

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

router.get('/:symbol/news', (req, res, next) => {
  const symbol = symbolParam(req);
  return handle(req, res, next, () =>
    prisma.news.findMany({
      where: { symbol },
      orderBy: { publishDate: 'desc' },
      take: 20
    })
  );
});

router.get('/:symbol/profile', (req, res, next) => {
  const symbol = symbolParam(req);
  return handle(req, res, next, () =>
    cached(`stock:${symbol}:profile`, config.cacheTtl.profile, () =>
      aiEngineService.getProfile(symbol),
    ),
  );
});

router.get('/:symbol/ohlcv', (req, res, next) => {
  const symbol = symbolParam(req);
  const interval = (req.query.interval as string) ?? '1D';
  const start = req.query.from as string | undefined;
  const end = req.query.to as string | undefined;
  const cacheKey = `stock:${symbol}:ohlcv:${interval}:${start ?? ''}:${end ?? ''}`;

  return handle(req, res, next, () =>
    cached(cacheKey, config.cacheTtl.ohlcv, () =>
      aiEngineService.getOHLCV(symbol, { interval, start, end }),
    ),
  );
});

router.get('/:symbol/quote', (req, res, next) => {
  const symbol = symbolParam(req);
  return handle(req, res, next, () =>
    cached(`stock:${symbol}:quote`, config.cacheTtl.quote, () => aiEngineService.getQuote(symbol)),
  );
});

router.get('/:symbol/orderbook', (req, res, next) => {
  const symbol = symbolParam(req);
  return handle(req, res, next, () =>
    cached(`stock:${symbol}:orderbook`, config.cacheTtl.orderbook, () =>
      aiEngineService.getOrderBook(symbol),
    ),
  );
});

router.get('/:symbol/trades', (req, res, next) => {
  const symbol = symbolParam(req);
  return handle(req, res, next, () => aiEngineService.getTrades(symbol));
});

router.get('/:symbol/fundamentals', (req, res, next) => {
  const symbol = symbolParam(req);
  return handle(req, res, next, () =>
    cached(`stock:${symbol}:fundamentals`, config.cacheTtl.fundamentals, () =>
      aiEngineService.getFundamentals(symbol),
    ),
  );
});

export default router;
