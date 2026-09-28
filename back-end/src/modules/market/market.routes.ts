import { Router, Request, Response, NextFunction } from 'express';
import { config } from '../../config';
import { aiEngineService } from '../../services/aiEngine.service';
import { cached } from '../../utils/cache';
import prisma from '../../config/database';
import { autoBackfillIfNeeded, getBackfillHistory } from '../../services/backfill.service';

const router = Router();

type Payload = Record<string, unknown>;
const rowsOf = (value: unknown, key: string): unknown[] => {
  if (Array.isArray(value)) return value;
  const rows = (value as Payload | null)?.[key];
  return Array.isArray(rows) ? rows : [];
};

async function dbSnapshot(exchange?: string) {
  const exchangeFilter = exchange?.toUpperCase() ?? 'HOSE';
  const rows = await prisma.$queryRaw<Array<Record<string, unknown>>>`
    SELECT d.ticker AS symbol,
           COALESCE(i.name, s.name, d.ticker) AS name,
           d.date,
           COALESCE((prev.close * 1000)::float8,
             (CASE WHEN ABS(s.ref_price) < 500 THEN s.ref_price * 1000 ELSE s.ref_price END)::float8,
             (c.open * 1000)::float8) AS ref,
           (CASE WHEN ABS(s.ceiling) < 500 THEN s.ceiling * 1000 ELSE s.ceiling END)::float8 AS ceiling,
           (CASE WHEN ABS(s.floor) < 500 THEN s.floor * 1000 ELSE s.floor END)::float8 AS floor,
           (COALESCE(c.close, d.close_adj) * 1000)::float8 AS price,
           d.volume_total::float8 AS volume,
           NULL::float8 AS foreign_flow,
           CASE WHEN prev.close <> 0
             THEN ((c.close - prev.close) / prev.close) * 100 ELSE NULL END AS change_pct,
           NULL::float8 AS rs,
           (t.indicators->>'momentum_1m')::float8 AS momentum
    FROM market_data_daily d
    JOIN stocks s ON s.symbol = d.ticker
    LEFT JOIN market_data_daily_calculation c ON c.ticker = d.ticker AND c.date = d.date
    LEFT JOIN LATERAL (
      SELECT close FROM market_data_daily_calculation
      WHERE ticker=d.ticker AND date < d.date ORDER BY date DESC LIMIT 1
    ) prev ON TRUE
    LEFT JOIN instrument_master i ON i.symbol = d.ticker
    LEFT JOIN technical_indicators t ON t.symbol = d.ticker AND t.calc_date = d.date
    WHERE d.date = (SELECT MAX(md.date) FROM market_data_daily md JOIN stocks ss ON ss.symbol=md.ticker WHERE ss.exchange='HOSE')
      AND (${exchangeFilter}::text IS NULL OR s.exchange = ${exchangeFilter})
    ORDER BY d.volume_total DESC NULLS LAST
  `;
  return { stocks: rows, asOf: rows[0]?.date ?? null, source: 'postgres', stale: true, exchange: exchangeFilter };
}

async function dbIndices() {
  const [rows, historyRows] = await Promise.all([
    prisma.$queryRaw<Array<Record<string, unknown>>>`
      SELECT c.ticker AS symbol, c.date, COALESCE(c.close, d.close_adj) AS value,
             CASE WHEN prev.close <> 0
               THEN ((c.close - prev.close) / prev.close) * 100 ELSE NULL END AS change_pct
      FROM market_data_daily_calculation c
      JOIN market_data_daily d USING (ticker, date)
      LEFT JOIN LATERAL (
        SELECT close FROM market_data_daily_calculation
        WHERE ticker=c.ticker AND date < c.date ORDER BY date DESC LIMIT 1
      ) prev ON TRUE
      WHERE c.ticker IN ('VNINDEX', 'VN-INDEX', 'VN30', 'VN100')
        AND c.date = (SELECT MAX(date) FROM market_data_daily WHERE ticker IN ('VNINDEX', 'VN-INDEX', 'VN30', 'VN100'))
    `,
    prisma.$queryRaw<Array<{ ticker: string; date: Date; close_adj: number }>>`
      SELECT ticker, date, close_adj
      FROM market_data_daily
      WHERE ticker IN ('VNINDEX', 'VN-INDEX', 'VN30', 'VN100')
      ORDER BY date DESC
      LIMIT 60
    `.catch(() => [] as Array<{ ticker: string; date: Date; close_adj: number }>),
  ]);

  const historyMap: Record<string, number[]> = { VNINDEX: [], VN30: [], VN100: [] };
  const sorted = [...historyRows].sort((a, b) => new Date(a.date).getTime() - new Date(b.date).getTime());
  for (const r of sorted) {
    const sym = r.ticker === 'VN-INDEX' ? 'VNINDEX' : r.ticker;
    if (historyMap[sym]) {
      historyMap[sym].push(Number(r.close_adj));
    }
  }

  return {
    indices: rows,
    history: historyMap,
    asOf: rows[0]?.date ?? null,
    source: 'postgres',
    stale: true,
  };
}

async function dbHeatmap() {
  const sectors = await prisma.$queryRaw<Array<Record<string, unknown>>>`
      WITH latest AS (SELECT MAX(date) AS date FROM market_data_daily)
      SELECT COALESCE(s.sector, 'Khác') AS sector,
             COUNT(*)::int AS count,
             COALESCE((SUM(CASE WHEN prev.close <> 0
               THEN ((c.close-prev.close)/prev.close)*100*COALESCE(c.market_cap, 0) ELSE 0 END)
               / NULLIF(SUM(COALESCE(c.market_cap, 0)), 0)),
               AVG(CASE WHEN prev.close <> 0 THEN ((c.close-prev.close)/prev.close)*100 END))::float8 AS change_pct,
             SUM(COALESCE(c.market_cap, 0))::float8 AS market_cap,
             NULL::float8 AS foreign_flow
      FROM market_data_daily d
      JOIN market_data_daily_calculation c ON c.ticker=d.ticker AND c.date=d.date
      JOIN latest l ON d.date=l.date
      JOIN stocks s ON s.symbol=d.ticker
      LEFT JOIN LATERAL (
        SELECT close FROM market_data_daily_calculation
        WHERE ticker=d.ticker AND date < d.date ORDER BY date DESC LIMIT 1
      ) prev ON TRUE
      WHERE s.exchange = 'HOSE'
      GROUP BY COALESCE(s.sector, 'Khác')
      ORDER BY market_cap DESC
    `;

  return { sectors, asOf: sectors[0] ? await prisma.market_data_daily.findFirst({ orderBy: { date: 'desc' }, select: { date: true } }).then(x => x?.date ?? null) : null, source: 'postgres', stale: true };
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

router.get('/indices', (req, res, next) => handle(req, res, next, () =>
  cached('market:indices', config.cacheTtl.indices, async () => {
    const live = await aiEngineService.getIndices().catch(() => null);
    return rowsOf(live, 'indices').length ? live : dbIndices();
  }),
));

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
    cached(cacheKey, config.cacheTtl.snapshot, async () => {
      const live = await aiEngineService.getMarketSnapshot(exchange).catch(() => null);
      return rowsOf(live, 'stocks').length ? live : dbSnapshot(exchange);
    }),
  );
});

router.get('/heatmap', (req, res, next) => handle(req, res, next, () =>
  cached('market:heatmap', config.cacheTtl.snapshot, async () => {
    const live = await aiEngineService.getHeatmap().catch(() => null);
    return rowsOf(live, 'sectors').length ? live : dbHeatmap();
  }),
));

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

router.get('/stream-assignment/:symbol', (req, res, next) => {
  const symbol = String(req.params.symbol ?? '').trim().toUpperCase();
  if (!/^[A-Z0-9]{1,10}$/.test(symbol)) {
    res.status(400).json({ error: 'Invalid HOSE symbol' });
    return;
  }
  return handle(req, res, next, () => aiEngineService.getStreamAssignment(symbol));
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
