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
    prisma.$queryRawUnsafe(
      `SELECT id, symbol, title, url, published_date, article_content, article_pdf_text, sentiment_score
       FROM news_events
       WHERE symbol = $1
       ORDER BY published_date DESC
       LIMIT 20`,
      [symbol],
    )
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
  const start = req.query.start as string | undefined;
  const end = req.query.end as string | undefined;
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

router.get('/:symbol/technical-indicators', (req, res, next) => {
  const symbol = symbolParam(req);
  return handle(req, res, next, () =>
    cached(`stock:${symbol}:technical`, 60, () =>
      aiEngineService.getTechnicalIndicators(symbol),
    ),
  );
});

router.get('/:symbol/ai-context', (req, res, next) => {
  const symbol = symbolParam(req);
  return handle(req, res, next, () =>
    cached(`stock:${symbol}:ai-context`, 120, () =>
      aiEngineService.getAIContext(symbol),
    ),
  );
});

router.get('/:symbol/factor-scores', (req, res, next) => {
  const symbol = symbolParam(req);
  return handle(req, res, next, () =>
    cached(`stock:${symbol}:factor-scores`, 300, () =>
      aiEngineService.getFactorScores(symbol),
    ),
  );
});

router.get('/:symbol/foreign-flow', (req, res, next) => {
  const symbol = symbolParam(req);
  return handle(req, res, next, () =>
    cached(`stock:${symbol}:foreign-flow`, 60, () =>
      aiEngineService.getForeignFlow(symbol),
    ),
  );
});

router.get('/:symbol/dividends', (req, res, next) => {
  const symbol = symbolParam(req);
  return handle(req, res, next, () =>
    cached(`stock:${symbol}:dividends`, 3600, () =>
      aiEngineService.getDividends(symbol),
    ),
  );
});

router.get('/:symbol/market-extras', (req, res, next) => {
  const symbol = symbolParam(req);
  return handle(req, res, next, () =>
    cached(`stock:${symbol}:market-extras`, 60, () =>
      aiEngineService.getMarketExtras(symbol),
    ),
  );
});

router.get('/:symbol/sentiment', (req, res, next) => {
  const symbol = symbolParam(req);
  return handle(req, res, next, () =>
    cached(`stock:${symbol}:sentiment`, 300, () =>
      aiEngineService.getSentiment(symbol),
    ),
  );
});

router.get('/macro', (req, res, next) => {
  return handle(req, res, next, () =>
    cached('stock:macro', 600, () =>
      aiEngineService.getMacro(),
    ),
  );
});

export default router;
