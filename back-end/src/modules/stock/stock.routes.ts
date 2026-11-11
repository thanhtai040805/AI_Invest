import { Router, Request, Response, NextFunction } from 'express';
import { config } from '../../config';
import { aiEngineService } from '../../services/aiEngine.service';
import { cached } from '../../utils/cache';
import prisma from '../../config/database';
import { redisService } from '../../services/redis.service';
import { hasMarketPrice, marketQuoteSnapshot, mergeMarketQuote } from '../../services/marketQuote.service';
import { liveOrderGateway, ExternalOrderBookSnapshot } from '../../services/matchingEngine';

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

const INDEX_SYMBOLS = new Set([
  'VNINDEX',
  'VN-INDEX',
  '^VNINDEX',
  'VN30',
  'VN100',
  'HNX',
  'HNXINDEX',
  'HNX30',
  'UPCOM',
  'UPCOMINDEX',
]);

function isIndexSymbol(symbol: string): boolean {
  return INDEX_SYMBOLS.has(symbol.trim().toUpperCase());
}

function indexTickerCandidates(symbol: string): string[] {
  const clean = symbol.trim().toUpperCase();
  if (clean === 'VNINDEX' || clean === 'VN-INDEX' || clean === '^VNINDEX') {
    return ['VNINDEX', 'VN-INDEX', '^VNINDEX'];
  }
  if (clean === 'HNX' || clean === 'HNXINDEX') {
    return ['HNXINDEX', 'HNX', '^HNX'];
  }
  if (clean === 'UPCOM' || clean === 'UPCOMINDEX') {
    return ['UPCOMINDEX', 'UPCOM', '^UPCOM'];
  }
  return [clean];
}

async function dbStockQuote(symbol: string) {
  const isIndex = isIndexSymbol(symbol);
  const candidates = isIndex ? indexTickerCandidates(symbol) : [symbol];
  return cached(`stock:v2:${symbol}:db-quote`, 15, async () => {
    const rows = await prisma.$queryRaw<Array<any>>`
      SELECT d.date,
             d.close_adj,
             d.volume_total,
             COALESCE(NULLIF(d.close_unadj, 0), NULLIF(d.close_adj, 0)) AS raw_close,
             prev.close AS prior_close,
             s.ref_price,
             s.ceiling,
             s.floor
      FROM market_data_daily d
      LEFT JOIN stocks s ON s.symbol = d.ticker
      LEFT JOIN LATERAL (
        SELECT COALESCE(NULLIF(close_unadj, 0), NULLIF(close_adj, 0)) AS close
        FROM market_data_daily
        WHERE ticker = d.ticker AND date < d.date
        ORDER BY date DESC LIMIT 1
      ) prev ON TRUE
      WHERE d.ticker = ANY(${candidates}::text[])
      ORDER BY d.date DESC LIMIT 1
    `;
    const row = rows[0];
    if (!row) return null;
    const toScale = (value: unknown) => {
      const n = Number(value ?? 0);
      if (!Number.isFinite(n) || n <= 0) return 0;
      if (isIndex) return Number(n.toFixed(2));
      return n < 500 ? n * 1000 : n;
    };
    const rawClose = Number(row.raw_close);
    const priorClose = Number(row.prior_close);
    const price = toScale(Number.isFinite(rawClose) && rawClose > 0 ? row.raw_close : row.close_adj);
    const ref = Number.isFinite(priorClose) && priorClose > 0 ? toScale(row.prior_close) : toScale(row.ref_price);
    const ceiling = isIndex ? 0 : toScale(row.ceiling);
    const floor = isIndex ? 0 : toScale(row.floor);
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
  });
}

async function dbStockProfile(symbol: string) {
  if (isIndexSymbol(symbol)) {
    const labelMap: Record<string, string> = {
      VNINDEX: 'Chỉ số VN-Index (HOSE)',
      'VN-INDEX': 'Chỉ số VN-Index (HOSE)',
      VN30: 'Chỉ số VN30 (HOSE)',
      VN100: 'Chỉ số VN100 (HOSE)',
      HNXINDEX: 'Chỉ số HNX-Index',
      UPCOMINDEX: 'Chỉ số UPCoM-Index',
    };
    return {
      symbol,
      name: labelMap[symbol] || symbol,
      industry: 'INDEX',
      shares_outstanding: null,
    };
  }
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
  const isIndex = isIndexSymbol(symbol);
  if (isIndex) {
    const candidates = indexTickerCandidates(symbol);
    const rows = await prisma.$queryRaw<Array<any>>`
      SELECT date,
             COALESCE(NULLIF(open_adj, 0), NULLIF(close_unadj, 0), close_adj)::float8 AS open,
             COALESCE(NULLIF(high_adj, 0), NULLIF(close_unadj, 0), close_adj)::float8 AS high,
             COALESCE(NULLIF(low_adj, 0), NULLIF(close_unadj, 0), close_adj)::float8 AS low,
             COALESCE(NULLIF(close_unadj, 0), close_adj)::float8 AS close,
             COALESCE(volume_total, 0)::float8 AS volume
      FROM market_data_daily
      WHERE ticker = ANY(${candidates}::text[])
        AND COALESCE(NULLIF(close_unadj, 0), close_adj, 0) > 0
      ORDER BY date DESC
      LIMIT ${limit}
    `;
    if (rows.length > 0) {
      return rows.reverse().map((r) => ({
        date: r.date,
        open: r.open > 100_000 ? Number((r.open / 1000).toFixed(2)) : Number(Number(r.open).toFixed(2)),
        high: r.high > 100_000 ? Number((r.high / 1000).toFixed(2)) : Number(Number(r.high).toFixed(2)),
        low: r.low > 100_000 ? Number((r.low / 1000).toFixed(2)) : Number(Number(r.low).toFixed(2)),
        close: r.close > 100_000 ? Number((r.close / 1000).toFixed(2)) : Number(Number(r.close).toFixed(2)),
        volume: Number(r.volume ?? 0),
      }));
    }
    const viewRows = await prisma.$queryRaw<Array<any>>`
      SELECT time AS date,
             open::float8 AS open,
             high::float8 AS high,
             low::float8 AS low,
             close::float8 AS close,
             COALESCE(volume, 0)::float8 AS volume
      FROM ohlcv_unadjusted
      WHERE symbol = ANY(${candidates}::text[])
        AND COALESCE(close, 0) > 0
      ORDER BY time DESC
      LIMIT ${limit}
    `.catch(() => []);
    return viewRows.reverse();
  }

  const rows = await prisma.$queryRaw<Array<any>>`
    SELECT date,
           (CASE WHEN close_adj > 0 AND close_adj < 1000 THEN open_adj * 1000 ELSE open_adj END)::float8 AS open,
           (CASE WHEN close_adj > 0 AND close_adj < 1000 THEN high_adj * 1000 ELSE high_adj END)::float8 AS high,
           (CASE WHEN close_adj > 0 AND close_adj < 1000 THEN low_adj * 1000 ELSE low_adj END)::float8 AS low,
           (CASE WHEN close_adj > 0 AND close_adj < 1000 THEN close_adj * 1000 ELSE close_adj END)::float8 AS close,
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
  const requestedLimit = typeof req.query.limit === 'string' ? Number(req.query.limit) : NaN;
  const limit = Number.isSafeInteger(requestedLimit) && requestedLimit > 0
    ? Math.min(requestedLimit, 1000)
    : 300;
  const cacheKey = `stock:v2:${symbol}:ohlcv:${interval}:${start ?? ''}:${end ?? ''}:${limit}`;

  return handle(req, res, next, () =>
    cached(cacheKey, config.cacheTtl.ohlcv, async () => {
      const live = await aiEngineService.getOHLCV(symbol, { interval, start, end, limit }).catch(() => null);
      const candles = Array.isArray(live) ? live : (live as { data?: unknown } | null)?.data;
      if (Array.isArray(candles) && candles.length > 0) return candles;
      return interval.toUpperCase() === '1D' ? dbStockOHLCV(symbol, limit) : [];
    }),
  );
});

router.get('/:symbol/quote', (req, res, next) => {
  const symbol = symbolParam(req);
  return handle(req, res, next, () =>
    (async () => {
      const [latest, security] = await redisService.getCacheMany<Record<string, unknown>>(
        [`stock:${symbol}:quote`, `stock:${symbol}:sec_def`],
      ).catch(() => []);
      if (hasMarketPrice(latest)) return mergeMarketQuote({}, latest, security);
      const fallback = await dbStockQuote(symbol);
      return fallback ? mergeMarketQuote(marketQuoteSnapshot(fallback), null, security) : null;
    })(),
  );
});

router.get('/:symbol/orderbook', (req, res, next) => {
  const symbol = symbolParam(req);
  return handle(req, res, next, async () => {
    const external = await cached(`api:stock:${symbol}:orderbook`, config.cacheTtl.orderbook, async () => {
      const live = await aiEngineService.getOrderBook(symbol).catch(() => null);
      if (live && Array.isArray((live as ExternalOrderBookSnapshot).bids)) return live as ExternalOrderBookSnapshot;
      const redisBook = await redisService.getCache<ExternalOrderBookSnapshot>(`stock:${symbol}:orderbook`).catch(() => null);
      if (redisBook && Array.isArray(redisBook.bids)) return redisBook;
      return { symbol, bids: [], asks: [], stale: true } as ExternalOrderBookSnapshot;
    });
    return liveOrderGateway.getMergedOrderBook(symbol, external);
  });
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
