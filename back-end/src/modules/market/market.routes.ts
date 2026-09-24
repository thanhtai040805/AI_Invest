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
  const rows = await prisma.$queryRaw<Array<Record<string, unknown>>>`
    SELECT d.ticker AS symbol,
           COALESCE(i.name, d.ticker) AS name,
           d.date,
           COALESCE((d.open_adj * 1000)::float8, (d.close_adj * 1000)::float8) AS ref,
           COALESCE((d.high_adj * 1000)::float8, (d.close_adj * 1000)::float8) AS ceiling,
           COALESCE((d.low_adj * 1000)::float8, (d.close_adj * 1000)::float8) AS floor,
           (d.close_adj * 1000)::float8 AS price,
           d.volume_total::float8 AS volume,
           COALESCE((d.foreign_net_vol * d.close_adj * 1000 / 1e9)::float8, 0) AS foreign_flow,
           CASE WHEN d.open_adj IS NOT NULL AND d.open_adj <> 0
             THEN ((d.close_adj - d.open_adj) / d.open_adj) * 100 ELSE 0 END AS change_pct,
           COALESCE((t.indicators->>'rsi_14')::float8, 50) AS rs,
           COALESCE((t.indicators->>'momentum_1m')::float8, 0) AS momentum
    FROM market_data_daily d
    LEFT JOIN instrument_master i ON i.symbol = d.ticker
    LEFT JOIN technical_indicators t ON t.symbol = d.ticker AND t.calc_date = d.date
    WHERE d.date = (SELECT MAX(date) FROM market_data_daily)
    ORDER BY d.volume_total DESC NULLS LAST
    LIMIT 100
  `;
  return { stocks: rows, asOf: rows[0]?.date ?? null, source: 'postgres', stale: true, exchange: exchange ?? null };
}

async function dbIndices() {
  const [rows, historyRows] = await Promise.all([
    prisma.$queryRaw<Array<Record<string, unknown>>>`
      SELECT ticker AS symbol, date, close_adj AS value,
             CASE WHEN open_adj IS NOT NULL AND open_adj <> 0
               THEN ((close_adj - open_adj) / open_adj) * 100 ELSE 0 END AS change_pct
      FROM market_data_daily
      WHERE ticker IN ('VNINDEX', 'VN-INDEX', 'VN30', 'HNXINDEX', 'UPCOMINDEX')
        AND date = (SELECT MAX(date) FROM market_data_daily WHERE ticker IN ('VNINDEX', 'VN-INDEX', 'VN30', 'HNXINDEX', 'UPCOMINDEX'))
    `,
    prisma.$queryRaw<Array<{ ticker: string; date: Date; close_adj: number }>>`
      SELECT ticker, date, close_adj
      FROM market_data_daily
      WHERE ticker IN ('VNINDEX', 'VN-INDEX', 'VN30')
      ORDER BY date DESC
      LIMIT 60
    `.catch(() => [] as Array<{ ticker: string; date: Date; close_adj: number }>),
  ]);

  const historyMap: Record<string, number[]> = { VNINDEX: [], VN30: [] };
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
  const [sectors, histories] = await Promise.all([
    prisma.$queryRaw<Array<Record<string, unknown>>>`
      WITH latest AS (SELECT MAX(date) AS date FROM market_data_daily)
      SELECT COALESCE(s.sector, 'Khác') AS sector,
             COUNT(*)::int AS count,
             AVG(CASE WHEN d.open_adj IS NOT NULL AND d.open_adj <> 0 THEN ((d.close_adj-d.open_adj)/d.open_adj)*100 ELSE 0 END)::float8 AS change_pct,
             SUM(COALESCE(d.market_cap, 0))::float8 AS market_cap,
             SUM(COALESCE(d.foreign_net_vol, 0))::float8 AS foreign_flow
      FROM market_data_daily d JOIN latest l ON d.date=l.date
      LEFT JOIN stocks s ON s.symbol=d.ticker GROUP BY COALESCE(s.sector, 'Khác')
      ORDER BY market_cap DESC
    `,
    prisma.$queryRaw<Array<{ sector: string; sparkline: number[] }>>`
      WITH recent_dates AS (
        SELECT DISTINCT date FROM market_data_daily ORDER BY date DESC LIMIT 15
      ),
      sector_daily AS (
        SELECT COALESCE(s.sector, 'Khác') AS sector, d.date, AVG(d.close_adj * 1000)::float8 AS avg_price
        FROM market_data_daily d
        JOIN recent_dates r ON d.date = r.date
        JOIN stocks s ON s.symbol = d.ticker
        GROUP BY COALESCE(s.sector, 'Khác'), d.date
      )
      SELECT sector, json_agg(avg_price ORDER BY date ASC) AS sparkline
      FROM sector_daily
      GROUP BY sector
    `.catch(() => [] as Array<{ sector: string; sparkline: number[] }>),
  ]);

  const historyMap = new Map<string, number[]>();
  for (const h of histories) {
    if (h.sector && Array.isArray(h.sparkline)) {
      historyMap.set(h.sector, h.sparkline);
    }
  }

  const enriched = sectors.map((s) => ({
    ...s,
    sparkline: historyMap.get(String(s.sector)) ?? [],
  }));

  return { sectors: enriched, asOf: sectors[0] ? await prisma.market_data_daily.findFirst({ orderBy: { date: 'desc' }, select: { date: true } }).then(x => x?.date ?? null) : null, source: 'postgres', stale: true };
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
