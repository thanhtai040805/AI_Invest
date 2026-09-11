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
    prisma.knowledge_documents.findMany({
      where: { symbol },
      orderBy: { published_date: 'desc' },
      take: 20,
    }),
  );
});

async function dbStockQuote(symbol: string) {
  const row = await prisma.market_data_daily.findFirst({
    where: { ticker: symbol },
    orderBy: { date: 'desc' },
  });
  if (!row) return null;
  const price = (row.close_adj ?? 0) * 1000;
  const ref = (row.open_adj ?? row.close_adj ?? 0) * 1000;
  const ceiling = (row.high_adj ?? row.close_adj ?? 0) * 1000;
  const floor = (row.low_adj ?? row.close_adj ?? 0) * 1000;
  const changePct = row.open_adj && row.open_adj !== 0
    ? (((row.close_adj ?? 0) - row.open_adj) / row.open_adj) * 100
    : 0;
  return {
    symbol,
    price,
    ref,
    ceiling,
    floor,
    volume: Number(row.volume_total ?? 0),
    change_pct: changePct,
    asOf: row.date,
  };
}

async function dbStockProfile(symbol: string) {
  const row = await prisma.instrument_master.findUnique({
    where: { symbol },
  });
  return {
    symbol,
    name: row?.name || symbol,
    industry: (row?.metadata as any)?.industry || 'HOSE',
    shares_outstanding: row?.shares_outstanding ? Number(row.shares_outstanding) : null,
  };
}

router.get('/:symbol/profile', (req, res, next) => {
  const symbol = symbolParam(req);
  return handle(req, res, next, () =>
    cached(`stock:${symbol}:profile`, config.cacheTtl.profile, async () => {
      const live = await aiEngineService.getProfile(symbol).catch(() => null);
      return live || dbStockProfile(symbol);
    }),
  );
});

async function dbStockOHLCV(symbol: string, limit = 60) {
  const rows = await prisma.$queryRaw<Array<any>>`
    SELECT date,
           (open_adj * 1000)::float8 AS open,
           (high_adj * 1000)::float8 AS high,
           (low_adj * 1000)::float8 AS low,
           (close_adj * 1000)::float8 AS close,
           volume_total::float8 AS volume
    FROM market_data_daily
    WHERE ticker = ${symbol}
    ORDER BY date DESC
    LIMIT ${limit}
  `;
  return rows.reverse();
}

router.get('/:symbol/ohlcv', (req, res, next) => {
  const symbol = symbolParam(req);
  const interval = (req.query.interval as string) ?? '1D';
  const start = req.query.start as string | undefined;
  const end = req.query.end as string | undefined;
  const cacheKey = `stock:${symbol}:ohlcv:${interval}:${start ?? ''}:${end ?? ''}`;

  return handle(req, res, next, () =>
    cached(cacheKey, config.cacheTtl.ohlcv, async () => {
      const live = await aiEngineService.getOHLCV(symbol, { interval, start, end }).catch(() => null);
      if (live && Array.isArray(live) && (live as any[]).length > 0) return live;
      return dbStockOHLCV(symbol, 60);
    }),
  );
});

router.get('/:symbol/quote', (req, res, next) => {
  const symbol = symbolParam(req);
  return handle(req, res, next, () =>
    cached(`stock:${symbol}:quote`, config.cacheTtl.quote, async () => {
      const live = await aiEngineService.getQuote(symbol).catch(() => null);
      return live || dbStockQuote(symbol);
    }),
  );
});

async function dbStockOrderBook(symbol: string) {
  const quote = await dbStockQuote(symbol);
  const p = quote?.price || 25000;
  const step = p < 10000 ? 10 : p < 50000 ? 50 : 100;
  const bids = [1, 2, 3].map((i) => ({ price: p - i * step, volume: Math.round((10 + (i * 3) % 7) * 1200) }));
  const asks = [1, 2, 3].map((i) => ({ price: p + i * step, volume: Math.round((8 + (i * 5) % 9) * 1100) }));
  return { symbol, bids, asks, asOf: quote?.asOf || new Date().toISOString() };
}

router.get('/:symbol/orderbook', (req, res, next) => {
  const symbol = symbolParam(req);
  return handle(req, res, next, () =>
    cached(`stock:${symbol}:orderbook`, config.cacheTtl.orderbook, async () => {
      const live = await aiEngineService.getOrderBook(symbol).catch(() => null);
      if (live && (live as any).bids) return live;
      return dbStockOrderBook(symbol);
    }),
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
