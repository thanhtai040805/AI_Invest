import { Router, Request, Response, NextFunction } from 'express';
import { config } from '../../config';
import { aiEngineService } from '../../services/aiEngine.service';
import { cached } from '../../utils/cache';
import prisma from '../../config/database';
import { redisService } from '../../services/redis.service';
import { autoBackfillIfNeeded, getBackfillHistory } from '../../services/backfill.service';
import { bestOrderBookLevel } from './market.utils';

const router = Router();

type Payload = Record<string, unknown>;
const rowsOf = (value: unknown, key: string): unknown[] => {
  if (Array.isArray(value)) return value;
  const rows = (value as Payload | null)?.[key];
  return Array.isArray(rows) ? rows : [];
};

type DbForeignFlow = { symbol: string; foreign_flow: number | null; foreign_flow_date: Date | null };

async function dbForeignFlows(): Promise<DbForeignFlow[]> {
  return cached('market:foreign-flow:daily', 60, () => prisma.$queryRaw<DbForeignFlow[]>`
    WITH latest AS (
      SELECT MAX(ff.trade_date) AS trade_date
      FROM foreign_flow ff
      JOIN stocks s ON s.symbol = ff.symbol
      WHERE s.exchange = 'HOSE'
    )
    SELECT ff.symbol,
           (COALESCE(ff.net_value, 0) / 1000000000.0)::float8 AS foreign_flow,
           ff.trade_date AS foreign_flow_date
    FROM foreign_flow ff
    JOIN stocks s ON s.symbol = ff.symbol
    JOIN latest l ON l.trade_date = ff.trade_date
    WHERE s.exchange = 'HOSE'
  `);
}

async function withDbForeignFlows(snapshot: Payload): Promise<Payload> {
  const flows = await dbForeignFlows().catch(() => []);
  const bySymbol = new Map(flows.map((flow) => [flow.symbol, flow]));
  const stocks = rowsOf(snapshot, 'stocks').map((value) => {
    const stock = value as Payload;
    const symbol = String(stock.symbol ?? '').toUpperCase();
    const flow = bySymbol.get(symbol);
    const current = stock.foreign_flow ?? stock.foreignFlow;
    if (current != null && Number.isFinite(Number(current))) return stock;
    return flow ? { ...stock, foreign_flow: flow.foreign_flow, foreign_flow_date: flow.foreign_flow_date } : stock;
  });
  return { ...snapshot, stocks, foreignFlowAsOf: flows[0]?.foreign_flow_date ?? null };
}

async function dbLiquidity(): Promise<Payload> {
  return cached('market:liquidity:estimate', 60, async () => {
    const rows = await prisma.$queryRaw<Array<{ date: Date; total_value_billion: number | null; stock_count: number }>>`
      WITH latest AS (
        SELECT MAX(d.date) AS date
        FROM market_data_daily d
        JOIN stocks s ON s.symbol = d.ticker
        WHERE s.exchange = 'HOSE'
      )
      SELECT d.date,
             (SUM(COALESCE(d.volume_total, 0)::numeric * COALESCE(NULLIF(d.close_unadj, 0), NULLIF(d.close_adj, 0))::numeric * 1000)
               / 1000000000.0)::float8 AS total_value_billion,
             COUNT(*) FILTER (
               WHERE COALESCE(d.volume_total, 0) > 0
                 AND COALESCE(NULLIF(d.close_unadj, 0), NULLIF(d.close_adj, 0)) > 0
             )::int AS stock_count
      FROM market_data_daily d
      JOIN stocks s ON s.symbol = d.ticker AND s.exchange = 'HOSE'
      JOIN latest l ON l.date = d.date
      GROUP BY d.date
    `;
    const row = rows[0];
    return {
      totalValueBillion: row?.total_value_billion && row.total_value_billion > 0 ? row.total_value_billion : null,
      stockCount: row?.stock_count ?? 0,
      lastUpdate: row?.date ?? null,
      asOf: row?.date ?? null,
      source: 'postgres-close-estimate',
      approximate: true,
      stale: true,
    };
  });
}

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
  return withDbForeignFlows({ stocks: rows, asOf: rows[0]?.date ?? null, source: 'postgres', stale: true, exchange: exchangeFilter });
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
    WITH date_range AS (
      SELECT DISTINCT d.date
      FROM market_data_daily d
      JOIN stocks s ON s.symbol = d.ticker
      WHERE s.exchange = 'HOSE'
      ORDER BY d.date DESC
      LIMIT 61
    ),
    latest AS (SELECT MAX(date) AS date FROM date_range),
    raw_history AS (
      SELECT d.ticker,
             d.date,
             COALESCE(s.sector, 'Khác') AS sector,
             CASE WHEN d.close_unadj > 0 AND d.close_adj <> 0 THEN d.close_unadj END AS close,
             CASE WHEN d.close_unadj > 0 AND d.close_adj <> 0
               THEN d.market_cap * d.close_unadj / NULLIF(d.close_adj, 0) END AS market_cap,
             s.floor,
             s.ceiling,
             LAG(CASE WHEN d.close_unadj > 0 AND d.close_adj <> 0 THEN d.close_unadj END)
               OVER (PARTITION BY d.ticker ORDER BY d.date) AS previous_close
      FROM market_data_daily d
      JOIN date_range dr ON dr.date = d.date
      JOIN stocks s ON s.symbol = d.ticker AND s.exchange = 'HOSE'
      WHERE d.close_unadj > 0 AND d.close_adj <> 0
    ),
    valid_returns AS (
      SELECT date,
             sector,
             market_cap,
             (((close - previous_close) / previous_close) * 100)::float8 AS change_pct
      FROM raw_history
      WHERE close > 0
        AND previous_close > 0
        AND (date <> (SELECT date FROM latest)
          OR ((floor IS NULL OR floor = 0 OR close * 1000 >= CASE WHEN ABS(floor) < 500 THEN floor * 1000 ELSE floor END)
          AND (ceiling IS NULL OR ceiling = 0 OR close * 1000 <= CASE WHEN ABS(ceiling) < 500 THEN ceiling * 1000 ELSE ceiling END)))
    ),
    daily AS (
      SELECT date,
             sector,
             COUNT(*)::int AS count,
             COALESCE(
               SUM(change_pct * GREATEST(COALESCE(market_cap, 0), 0))
                 / NULLIF(SUM(GREATEST(COALESCE(market_cap, 0), 0)), 0),
               AVG(change_pct)
             )::float8 AS change_pct,
             SUM(GREATEST(COALESCE(market_cap, 0), 0))::float8 AS market_cap
      FROM valid_returns
      GROUP BY date, sector
    ),
    sector_history AS (
      SELECT sector,
             (ARRAY_AGG(count ORDER BY date DESC))[1] AS count,
             (ARRAY_AGG(change_pct ORDER BY date DESC))[1] AS change_pct,
             (ARRAY_AGG(market_cap ORDER BY date DESC))[1] AS market_cap,
             ARRAY_AGG(change_pct ORDER BY date) AS sparkline,
             MAX(date) AS as_of
      FROM daily
      GROUP BY sector
    ),
    foreign_date AS (
      SELECT MAX(ff.trade_date) AS date
      FROM foreign_flow ff
      JOIN stocks s ON s.symbol = ff.symbol
      WHERE s.exchange = 'HOSE'
    ),
    foreign_by_sector AS (
      SELECT COALESCE(s.sector, 'Khác') AS sector,
             (SUM(COALESCE(ff.net_value, 0)) / 1000000000.0)::float8 AS foreign_flow
      FROM foreign_flow ff
      JOIN foreign_date fd ON fd.date = ff.trade_date
      JOIN stocks s ON s.symbol = ff.symbol AND s.exchange = 'HOSE'
      GROUP BY COALESCE(s.sector, 'Khác')
    )
    SELECT h.sector, h.count, h.change_pct, h.market_cap, h.sparkline,
           f.foreign_flow, fd.date AS foreign_as_of, h.as_of
    FROM sector_history h
    CROSS JOIN foreign_date fd
    LEFT JOIN foreign_by_sector f USING (sector)
    ORDER BY h.market_cap DESC NULLS LAST
  `;

  const normalizedSectors: Payload[] = sectors.map((sector): Payload => {
    const dailyReturns = Array.isArray(sector.sparkline) ? sector.sparkline.map(Number).filter(Number.isFinite) : [];
    let indexValue = 100;
    const sparkline = dailyReturns.map((change) => {
      indexValue *= 1 + change / 100;
      return Number(indexValue.toFixed(2));
    });
    return { ...sector, sparkline };
  });
  return {
    sectors: normalizedSectors,
    asOf: normalizedSectors[0]?.as_of ?? null,
    foreignFlowAsOf: normalizedSectors[0]?.foreign_as_of ?? null,
    source: 'postgres',
    stale: true,
  };
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
  handle(req, res, next, async () => {
    const live = await aiEngineService.getLiquidity().catch(() => null) as Payload | null;
    const value = live?.totalValueBillion;
    const updatedAt = Date.parse(String(live?.lastUpdate ?? ''));
    const fresh = updatedAt > 0 && Date.now() - updatedAt < 15_000;
    if (value != null && Number.isFinite(Number(value)) && Number(value) > 0 && fresh) {
      return { ...live, approximate: false, stale: false };
    }
    return dbLiquidity();
  }),
);

router.get('/orderbooks', (req, res, next) => handle(req, res, next, async () => {
  const rows = await prisma.$queryRaw<Array<{ symbol: string }>>`
    SELECT symbol FROM stocks WHERE exchange = 'HOSE' ORDER BY symbol
  `;
  const symbols = rows.map((row) => row.symbol.toUpperCase());
  const books = await redisService.getCacheMany<Payload>(symbols.map((symbol) => `stock:${symbol}:orderbook`)).catch(() => []);
  const now = Date.now();
  const orderbooks: Record<string, Payload> = {};
  books.forEach((book, index) => {
    if (!book) return;
    let receivedAt = Number(book.receivedAt ?? 0);
    if (receivedAt > 0 && receivedAt < 1_000_000_000_000) receivedAt *= 1000;
    if (!receivedAt) receivedAt = Date.parse(String(book.lastUpdate ?? '')) || 0;
    const age = now - receivedAt;
    if (!receivedAt || age < -5_000 || age >= 15_000) return;
    const bid = bestOrderBookLevel(book.bids, true);
    const ask = bestOrderBookLevel(book.asks, false);
    orderbooks[symbols[index]] = {
      ...book,
      symbol: symbols[index],
      bids: bid ? [bid] : [],
      asks: ask ? [ask] : [],
      receivedAt: receivedAt || null,
      stale: false,
    };
  });
  return { orderbooks, source: 'redis' };
}));

router.get('/snapshot', (req, res, next) => {
  const exchange = req.query.exchange as string | undefined;
  const cacheKey = exchange ? `market:snapshot:api:${exchange.toUpperCase()}` : 'market:snapshot:api';
  return handle(req, res, next, () =>
    cached(cacheKey, config.cacheTtl.snapshot, async () => {
      const live = await aiEngineService.getMarketSnapshot(exchange).catch(() => null);
      return rowsOf(live, 'stocks').length ? withDbForeignFlows(live as Payload) : dbSnapshot(exchange);
    }),
  );
});

router.get('/heatmap', (req, res, next) => handle(req, res, next, () =>
  cached('market:heatmap:api', 60, async () => {
    const [live, history] = await Promise.all([
      aiEngineService.getHeatmap().catch(() => null),
      cached('market:heatmap:history', 300, () => dbHeatmap()).catch(() => ({ sectors: [], source: 'postgres', stale: true })),
    ]);
    const historySectors = rowsOf(history, 'sectors') as Payload[];
    if (!rowsOf(live, 'sectors').length) return history;
    const historyByName = new Map(historySectors.map((sector) => [String(sector.sector ?? sector.name ?? ''), sector]));
    const sectors = rowsOf(live, 'sectors').map((value) => {
      const sector = value as Payload;
      const historySector = historyByName.get(String(sector.name ?? sector.sector ?? ''));
      return {
        ...sector,
        sparkline: Array.isArray(historySector?.sparkline) && historySector.sparkline.length > 1
          ? historySector.sparkline
          : sector.sparkline ?? [],
        // DB flow is the complete daily sector total; a live heatmap can cover only part of a sector.
        foreign_flow: historySector?.foreign_flow ?? null,
      };
    });
    return {
      ...(live as Payload),
      sectors,
      historyAsOf: (history as Payload | null)?.asOf ?? null,
      foreignFlowAsOf: (history as Payload | null)?.foreignFlowAsOf ?? null,
    };
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
