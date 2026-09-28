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
  const rows = await prisma.$queryRaw<Array<any>>`
    SELECT d.date, d.close_adj, d.volume_total,
           c.close AS raw_close, prev.close AS prior_close,
           s.ref_price, s.ceiling, s.floor
    FROM market_data_daily d
    LEFT JOIN market_data_daily_calculation c ON c.ticker=d.ticker AND c.date=d.date
    LEFT JOIN stocks s ON s.symbol=d.ticker
    LEFT JOIN LATERAL (
      SELECT close FROM market_data_daily_calculation
      WHERE ticker=d.ticker AND date < d.date ORDER BY date DESC LIMIT 1
    ) prev ON TRUE
    WHERE d.ticker=${symbol}
    ORDER BY d.date DESC LIMIT 1
  `;
  const row = rows[0];
  if (!row) return null;
  const toVnd = (value: unknown) => {
    const n = Number(value ?? 0);
    return n > 0 && n < 500 ? n * 1000 : n;
  };
  const price = toVnd(row.raw_close ?? row.close_adj);
  const ref = row.prior_close != null ? toVnd(row.prior_close) : toVnd(row.ref_price);
  const ceiling = toVnd(row.ceiling);
  const floor = toVnd(row.floor);
  const changePct = ref > 0 && price > 0 ? ((price - ref) / ref) * 100 : null;
  return {
    symbol,
    price,
    ref,
    ceiling,
    floor,
    volume: Number(row.volume_total ?? 0),
    change_pct: changePct,
    asOf: row.date,
    source: 'postgres',
    stale: true,
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

async function dbStockOHLCV(symbol: string, limit = 300) {
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
      const candles = Array.isArray(live) ? live : (live as { data?: unknown } | null)?.data;
      if (Array.isArray(candles) && candles.length > 0) return candles;
      return interval.toUpperCase() === '1D' ? dbStockOHLCV(symbol, 300) : [];
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

router.get('/:symbol/orderbook', (req, res, next) => {
  const symbol = symbolParam(req);
  return handle(req, res, next, () =>
    cached(`stock:${symbol}:orderbook`, config.cacheTtl.orderbook, async () => {
      const live = await aiEngineService.getOrderBook(symbol).catch(() => null);
      if (live && (live as any).bids) return live;
      return { symbol, bids: [], asks: [] };
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
