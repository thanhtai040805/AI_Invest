import { Router, Request, Response, NextFunction } from 'express';
import { config } from '../../config';
import { aiEngineService } from '../../services/aiEngine.service';
import { cached } from '../../utils/cache';
import prisma from '../../config/database';
import { redisService } from '../../services/redis.service';
import { autoBackfillIfNeeded, getBackfillHistory } from '../../services/backfill.service';
import { bestOrderBookLevel } from './market.utils';
import { hasMarketPrice, mergeMarketQuote } from '../../services/marketQuote.service';

const router = Router();

type Payload = Record<string, unknown>;
const rowsOf = (value: unknown, key: string): unknown[] => {
  if (Array.isArray(value)) return value;
  const rows = (value as Payload | null)?.[key];
  return Array.isArray(rows) ? rows : [];
};

type DbForeignFlow = { symbol: string; foreign_flow: number | null; foreign_flow_date: Date | null };

async function dbForeignFlows(): Promise<DbForeignFlow[]> {
  return cached('market:foreign-flow:daily', 300, () => prisma.$queryRaw<DbForeignFlow[]>`
    WITH latest AS (
      SELECT COALESCE(
        (SELECT trade_date FROM foreign_flow WHERE symbol = 'VCB' ORDER BY trade_date DESC LIMIT 1),
        (SELECT MAX(trade_date) FROM foreign_flow)
      ) AS trade_date
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
  return cached('market:liquidity:estimate', 120, async () => {
    const rows = await prisma.$queryRaw<Array<{ date: Date; total_value_billion: number | null; stock_count: number }>>`
      WITH latest AS (
        SELECT COALESCE(
          (SELECT date FROM market_data_daily WHERE ticker = 'VCB' ORDER BY date DESC LIMIT 1),
          (SELECT MAX(date) FROM market_data_daily)
        ) AS date
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
    WITH latest_d AS (
      SELECT COALESCE(
        (SELECT date FROM market_data_daily WHERE ticker = 'VCB' ORDER BY date DESC LIMIT 1),
        (SELECT MAX(date) FROM market_data_daily)
      ) AS date
    ),
    curr_bars AS (
      SELECT d.ticker,
             d.date,
             COALESCE(NULLIF(d.close_unadj, 0), NULLIF(d.close_adj, 0)) AS close,
             NULLIF(d.open_adj, 0) AS open,
             d.close_adj,
             d.volume_total
      FROM market_data_daily d
      JOIN latest_d l ON d.date = l.date
    )
    SELECT c.ticker AS symbol,
           COALESCE(i.name, s.name, c.ticker) AS name,
           c.date,
           COALESCE((prev.close * 1000)::float8,
             (CASE WHEN ABS(s.ref_price) < 500 THEN s.ref_price * 1000 ELSE s.ref_price END)::float8,
             (c.open * 1000)::float8) AS ref,
           (CASE WHEN ABS(s.ceiling) < 500 THEN s.ceiling * 1000 ELSE s.ceiling END)::float8 AS ceiling,
           (CASE WHEN ABS(s.floor) < 500 THEN s.floor * 1000 ELSE s.floor END)::float8 AS floor,
           (COALESCE(c.close, c.close_adj) * 1000)::float8 AS price,
           c.volume_total::float8 AS volume,
           NULL::float8 AS foreign_flow,
           CASE WHEN prev.close IS NOT NULL AND prev.close <> 0
             THEN ((c.close - prev.close) / prev.close) * 100 ELSE NULL END AS change_pct,
           NULL::float8 AS rs,
           (t.indicators->>'momentum_1m')::float8 AS momentum
    FROM curr_bars c
    JOIN stocks s ON s.symbol = c.ticker
    LEFT JOIN LATERAL (
      SELECT COALESCE(NULLIF(close_unadj, 0), NULLIF(close_adj, 0)) AS close
      FROM market_data_daily
      WHERE ticker = c.ticker AND date < c.date
      ORDER BY date DESC LIMIT 1
    ) prev ON TRUE
    LEFT JOIN instrument_master i ON i.symbol = c.ticker
    LEFT JOIN LATERAL (
      SELECT indicators FROM technical_indicators
      WHERE symbol = c.ticker AND calc_date = c.date
      LIMIT 1
    ) t ON TRUE
    WHERE (${exchangeFilter}::text IS NULL OR s.exchange = ${exchangeFilter})
    ORDER BY c.volume_total DESC NULLS LAST
  `;
  return withDbForeignFlows({ stocks: rows, asOf: rows[0]?.date ?? null, source: 'postgres', stale: true, exchange: exchangeFilter });
}

async function withRedisQuotes(snapshot: Payload): Promise<Payload> {
  const stocks = rowsOf(snapshot, 'stocks') as Payload[];
  if (!stocks.length) return snapshot;

  const symbols = stocks.map((stock) => String(stock.symbol ?? '').toUpperCase());
  const [quotes, securityDefinitions] = await Promise.all([
    redisService.getCacheMany<Record<string, unknown>>(symbols.map((symbol) => `stock:${symbol}:quote`)).catch(() => []),
    redisService.getCacheMany<Record<string, unknown>>(symbols.map((symbol) => `stock:${symbol}:sec_def`)).catch(() => []),
  ]);
  let redisQuoteCount = 0;
  const mergedStocks = stocks.map((stock, index) => {
    const quote = quotes[index];
    if (hasMarketPrice(quote)) redisQuoteCount += 1;
    return { ...mergeMarketQuote(stock, quote, securityDefinitions[index]), symbol: symbols[index] };
  });

  return {
    ...snapshot,
    stocks: mergedStocks,
    quoteSource: redisQuoteCount ? 'redis-latest' : snapshot.source ?? 'postgres',
    redisQuoteCount,
  };
}

async function dbIndices() {
  const [rows, historyRows] = await Promise.all([
    prisma.$queryRaw<Array<Record<string, unknown>>>`
      SELECT d.ticker AS symbol,
             d.date,
             COALESCE(NULLIF(d.close_unadj, 0), d.close_adj) AS value,
             CASE WHEN prev.close IS NOT NULL AND prev.close <> 0
               THEN ((COALESCE(NULLIF(d.close_unadj, 0), d.close_adj) - prev.close) / prev.close) * 100
               ELSE NULL END AS change_pct
      FROM market_data_daily d
      LEFT JOIN LATERAL (
        SELECT COALESCE(NULLIF(close_unadj, 0), close_adj) AS close
        FROM market_data_daily
        WHERE ticker = d.ticker AND date < d.date
        ORDER BY date DESC LIMIT 1
      ) prev ON TRUE
      WHERE d.ticker IN ('VNINDEX', 'VN-INDEX', 'VN30', 'VN100')
        AND d.date = (SELECT MAX(date) FROM market_data_daily WHERE ticker IN ('VNINDEX', 'VN-INDEX', 'VN30', 'VN100'))
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
    WITH universe AS (
      SELECT COALESCE(sector, 'Khác') AS sector, COUNT(*)::int AS count
      FROM stocks WHERE exchange = 'HOSE' GROUP BY COALESCE(sector, 'Khác')
    ),
    date_range AS (
      SELECT date
      FROM market_data_daily
      WHERE ticker = 'VCB'
      ORDER BY date DESC
      LIMIT 61
    ),
    bounds AS (
      SELECT MIN(date) AS min_date, MAX(date) AS max_date FROM date_range
    ),
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
      CROSS JOIN bounds b
      JOIN stocks s ON s.symbol = d.ticker AND s.exchange = 'HOSE'
      WHERE d.date >= b.min_date
        AND d.close_unadj > 0 AND d.close_adj <> 0
    ),
    valid_returns AS (
      SELECT r.date,
             r.sector,
             r.market_cap,
             (((r.close - r.previous_close) / r.previous_close) * 100)::float8 AS change_pct
      FROM raw_history r
      CROSS JOIN bounds b
      WHERE r.close > 0
        AND r.previous_close > 0
        AND (r.date <> b.max_date
          OR ((r.floor IS NULL OR r.floor = 0 OR r.close * 1000 >= CASE WHEN ABS(r.floor) < 500 THEN r.floor * 1000 ELSE r.floor END)
          AND (r.ceiling IS NULL OR r.ceiling = 0 OR r.close * 1000 <= CASE WHEN ABS(r.ceiling) < 500 THEN r.ceiling * 1000 ELSE r.ceiling END)))
    ),
    daily AS (
      SELECT date,
             sector,
             COUNT(*)::int AS count,
             COUNT(*) FILTER (WHERE market_cap > 0)::int AS market_cap_count,
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
             (ARRAY_AGG(market_cap_count ORDER BY date DESC))[1] AS market_cap_count,
             (ARRAY_AGG(change_pct ORDER BY date DESC))[1] AS change_pct,
             (ARRAY_AGG(market_cap ORDER BY date DESC))[1] AS market_cap,
             ARRAY_AGG(change_pct ORDER BY date) AS sparkline,
             MAX(date) AS as_of
      FROM daily
      GROUP BY sector
    ),
    foreign_date AS (
      SELECT COALESCE(
        (SELECT trade_date FROM foreign_flow WHERE symbol = 'VCB' ORDER BY trade_date DESC LIMIT 1),
        (SELECT MAX(trade_date) FROM foreign_flow)
      ) AS date
    ),
    foreign_by_sector AS (
      SELECT COALESCE(s.sector, 'Khác') AS sector,
             (SUM(COALESCE(ff.net_value, 0)) / 1000000000.0)::float8 AS foreign_flow
      FROM foreign_flow ff
      JOIN foreign_date fd ON fd.date = ff.trade_date
      JOIN stocks s ON s.symbol = ff.symbol AND s.exchange = 'HOSE'
      GROUP BY COALESCE(s.sector, 'Khác')
    )
    SELECT u.sector, u.count, h.market_cap_count, h.change_pct, h.market_cap, h.sparkline,
           f.foreign_flow, fd.date AS foreign_as_of, h.as_of
    FROM universe u
    LEFT JOIN sector_history h USING (sector)
    CROSS JOIN foreign_date fd
    LEFT JOIN foreign_by_sector f USING (sector)
    ORDER BY u.count DESC
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

async function dbSectorStrength() {
  return cached('market:sector-strength:v1', 300, async () => {
    const [dailyRows, topStockRows] = await Promise.all([
      prisma.$queryRaw<Array<{ date: Date; group_key: string; avg_ret: number | null; cnt: number }>>`
        WITH date_range AS (
          SELECT date
          FROM market_data_daily
          WHERE ticker = 'VCB'
          ORDER BY date DESC
          LIMIT 241
        ),
        bounds AS (
          SELECT MIN(date) AS min_date FROM date_range
        ),
        raw AS (
          SELECT d.ticker,
                 d.date,
                 COALESCE(s.sector, 'OTHER_INDUSTRIALS') AS sector,
                 d.close_adj,
                 LAG(d.close_adj) OVER (PARTITION BY d.ticker ORDER BY d.date) AS prev_close,
                 COALESCE(d.market_cap, s.market_cap, 0) AS mcap
          FROM market_data_daily d
          CROSS JOIN bounds b
          LEFT JOIN stocks s ON s.symbol = d.ticker
          WHERE d.date >= b.min_date
            AND (s.exchange = 'HOSE' OR d.ticker IN ('VNINDEX', 'VN-INDEX'))
            AND d.close_adj > 0
        ),
        returns AS (
          SELECT ticker,
                 date,
                 CASE WHEN ticker IN ('VNINDEX', 'VN-INDEX') THEN '__VNINDEX__' ELSE sector END AS group_key,
                 ((close_adj - prev_close) / prev_close * 100.0)::float8 AS ret_pct,
                 mcap
          FROM raw
          WHERE prev_close > 0
            AND ABS((close_adj - prev_close) / prev_close) <= 0.15
        )
        SELECT date,
               group_key,
               COALESCE(
                 SUM(ret_pct * GREATEST(mcap, 0)) / NULLIF(SUM(GREATEST(mcap, 0)), 0),
                 AVG(ret_pct)
               )::float8 AS avg_ret,
               COUNT(*)::int AS cnt
        FROM returns
        GROUP BY date, group_key
        ORDER BY date ASC, group_key ASC
      `,
      prisma.$queryRaw<Array<{
        symbol: string;
        name: string;
        sector: string;
        price: number | null;
        change_pct: number | null;
        rs_20d: number | null;
      }>>`
        WITH date_range AS (
          SELECT date
          FROM market_data_daily
          WHERE ticker = 'VCB'
          ORDER BY date DESC
          LIMIT 21
        ),
        bounds AS (
          SELECT MIN(date) AS min_date, MAX(date) AS max_date FROM date_range
        ),
        stock_window AS (
          SELECT d.ticker AS symbol,
                 COALESCE(s.name, d.ticker) AS name,
                 COALESCE(s.sector, 'OTHER_INDUSTRIALS') AS sector,
                 MAX(COALESCE(NULLIF(d.close_unadj, 0), d.close_adj) * 1000.0) FILTER (WHERE d.date = b.max_date)::float8 AS price,
                 MAX(d.close_adj) FILTER (WHERE d.date = b.max_date)::float8 AS c_end,
                 MAX(d.close_adj) FILTER (WHERE d.date = b.min_date)::float8 AS c_start,
                 MAX(d.volume_total) FILTER (WHERE d.date = b.max_date)::float8 AS latest_vol,
                 AVG(d.volume_total)::float8 AS avg_vol
          FROM market_data_daily d
          CROSS JOIN bounds b
          JOIN stocks s ON s.symbol = d.ticker AND s.exchange = 'HOSE'
          WHERE d.date >= b.min_date
            AND d.close_adj > 0
          GROUP BY d.ticker, s.name, s.sector
        ),
        prev_day AS (
          SELECT d.ticker AS symbol,
                 ((d.close_adj - prev.close_adj) / NULLIF(prev.close_adj, 0) * 100.0)::float8 AS change_pct
          FROM market_data_daily d
          JOIN bounds b ON d.date = b.max_date
          LEFT JOIN LATERAL (
            SELECT close_adj FROM market_data_daily
            WHERE ticker = d.ticker AND date < b.max_date
            ORDER BY date DESC LIMIT 1
          ) prev ON TRUE
        )
        SELECT sw.symbol,
               sw.name,
               sw.sector,
               sw.price,
               pd.change_pct,
               CASE WHEN sw.c_start > 0 AND sw.c_end > 0
                 THEN ((sw.c_end - sw.c_start) / sw.c_start * 100.0)::float8
                 ELSE pd.change_pct END AS rs_20d
        FROM stock_window sw
        LEFT JOIN prev_day pd USING (symbol)
        WHERE sw.avg_vol >= 20000 AND sw.c_end > 0
      `,
    ]);

    const dateSet = Array.from(
      new Set(dailyRows.map((r) => new Date(r.date).toISOString().slice(0, 10))),
    ).sort();
    const dateIndex = new Map(dateSet.map((d, idx) => [d, idx]));
    const vnReturns = new Array<number>(dateSet.length).fill(0);
    const sectorMap = new Map<string, { count: number; returns: number[] }>();

    for (const row of dailyRows) {
      const dStr = new Date(row.date).toISOString().slice(0, 10);
      const idx = dateIndex.get(dStr);
      if (idx === undefined) continue;
      const val = Number.isFinite(Number(row.avg_ret)) ? Number(Number(row.avg_ret).toFixed(3)) : 0;
      if (row.group_key === '__VNINDEX__') {
        vnReturns[idx] = val;
      } else {
        let entry = sectorMap.get(row.group_key);
        if (!entry) {
          entry = { count: row.cnt, returns: new Array<number>(dateSet.length).fill(0) };
          sectorMap.set(row.group_key, entry);
        }
        entry.count = Math.max(entry.count, Number(row.cnt || 0));
        entry.returns[idx] = val;
      }
    }

    const topBySector = new Map<string, Array<{ symbol: string; name: string; price: number; changePct: number | null; rs20d: number }>>();
    for (const st of topStockRows) {
      const list = topBySector.get(st.sector) ?? [];
      list.push({
        symbol: st.symbol,
        name: st.name,
        price: Number(st.price ?? 0),
        changePct: st.change_pct != null && Number.isFinite(Number(st.change_pct)) ? Number(Number(st.change_pct).toFixed(2)) : null,
        rs20d: Number.isFinite(Number(st.rs_20d)) ? Number(Number(st.rs_20d).toFixed(2)) : 0,
      });
      topBySector.set(st.sector, list);
    }
    for (const [sec, list] of topBySector.entries()) {
      list.sort((a, b) => b.rs20d - a.rs20d);
      topBySector.set(sec, list.slice(0, 7));
    }

    const sectors = Array.from(sectorMap.entries()).map(([sector, info]) => {
      const latestRet = info.returns.length ? info.returns[info.returns.length - 1] : null;
      return {
        sector,
        count: info.count,
        dailyReturns: info.returns,
        latestChangePct: latestRet,
        topStocks: topBySector.get(sector) ?? [],
      };
    });

    return {
      dates: dateSet,
      vnindexReturns: vnReturns,
      sectors,
      asOf: dateSet[dateSet.length - 1] ?? null,
      source: 'postgres',
    };
  });
}

async function dbSuperStocks(filterSymbol?: string) {
  const symKey = filterSymbol ? filterSymbol.trim().toUpperCase() : 'ALL';
  const basePayload = await cached(`market:super-stocks:v4:${symKey}`, 120, async () => {
    const rows = await prisma.$queryRaw<Array<Record<string, unknown>>>`
      WITH date_range AS (
        SELECT date
        FROM market_data_daily
        WHERE ticker = 'VCB'
        ORDER BY date DESC
        LIMIT 25
      ),
      bounds AS (
        SELECT MIN(date) AS min_date, MAX(date) AS max_date FROM date_range
      ),
      bars AS (
        SELECT d.ticker,
               d.date,
               ROW_NUMBER() OVER (PARTITION BY d.ticker ORDER BY d.date DESC) AS rn,
               COALESCE(NULLIF(d.open_adj, 0), d.close_adj) AS o,
               COALESCE(NULLIF(d.high_adj, 0), d.close_adj) AS h,
               COALESCE(NULLIF(d.low_adj, 0), d.close_adj) AS l,
               d.close_adj AS c,
               COALESCE(NULLIF(d.close_unadj, 0), d.close_adj) AS close_unadj,
               LAG(d.close_adj) OVER (PARTITION BY d.ticker ORDER BY d.date ASC) AS prev_c,
               LAG(COALESCE(NULLIF(d.low_adj, 0), d.close_adj)) OVER (PARTITION BY d.ticker ORDER BY d.date ASC) AS prev_l,
               LAG(COALESCE(d.volume_total, 0)::float8) OVER (PARTITION BY d.ticker ORDER BY d.date ASC) AS prev_vol,
               MIN(COALESCE(NULLIF(d.low_adj, 0), d.close_adj)) OVER (
                 PARTITION BY d.ticker ORDER BY d.date ASC ROWS BETWEEN 5 PRECEDING AND 1 PRECEDING
               ) AS min_prev5_l,
               MIN(COALESCE(NULLIF(d.low_adj, 0), d.close_adj)) OVER (
                 PARTITION BY d.ticker ORDER BY d.date ASC ROWS BETWEEN 10 PRECEDING AND 1 PRECEDING
               ) AS min_prev10_l,
               AVG(COALESCE(d.volume_total, 0)::float8) OVER (
                 PARTITION BY d.ticker ORDER BY d.date ASC ROWS BETWEEN 9 PRECEDING AND CURRENT ROW
               ) AS vol_ma10_roll,
               AVG(d.close_adj) OVER (
                 PARTITION BY d.ticker ORDER BY d.date ASC ROWS BETWEEN 19 PRECEDING AND CURRENT ROW
               ) AS ma20_c_roll,
               COALESCE(d.volume_total, 0)::float8 AS vol
        FROM market_data_daily d
        CROSS JOIN bounds b
        JOIN stocks s ON s.symbol = d.ticker AND s.exchange = 'HOSE'
        WHERE d.date >= b.min_date
          AND d.close_adj > 0
          AND (${filterSymbol ?? null}::text IS NULL OR d.ticker = ${filterSymbol ?? null})
      ),
      bar_metrics AS (
        SELECT ticker, date, rn, o, h, l, c, close_unadj, prev_c, prev_l, prev_vol,
               min_prev5_l, min_prev10_l, vol_ma10_roll, ma20_c_roll, vol,
               AVG(vol) FILTER (WHERE rn <= 10) OVER (PARTITION BY ticker) AS vol_ma10_calc,
               AVG(vol) FILTER (WHERE rn BETWEEN 2 AND 11) OVER (PARTITION BY ticker) AS vol_ma10_excl_calc,
               AVG(vol) FILTER (WHERE rn <= 20) OVER (PARTITION BY ticker) AS vol_ma20_calc,
               CASE WHEN (h - l) > 0 THEN (c - l) / (h - l) ELSE 0.5 END AS close_pos,
               CASE WHEN (h - l + ABS(c - o)) > 0
                 THEN ((c - l) + GREATEST(0, c - o)) / (h - l + ABS(c - o))
                 ELSE 0.5 END AS bvc_buy_ratio,
               CASE WHEN (h - l) > 0 THEN (LEAST(o, c) - l) / (h - l) ELSE 0.0 END AS lower_wick_ratio,
               CASE WHEN c > 0 THEN (h - l) / c ELSE 0.0 END AS spread_pct,
               CASE WHEN prev_c > 0 THEN ((c - prev_c) / prev_c) * 100.0 ELSE 0.0 END AS ret_pct
        FROM bars
      ),
      agg AS (
        SELECT ticker,
               MAX(close_unadj) FILTER (WHERE rn = 1) AS latest_close,
               MAX(c) FILTER (WHERE rn = 1) AS latest_close_adj,
               MAX(ret_pct) FILTER (WHERE rn = 1) AS latest_change_pct,
               MAX(vol) FILTER (WHERE rn = 1) AS latest_vol,
               MAX(vol_ma10_calc) AS vol_ma10,
               MAX(vol_ma10_excl_calc) AS vol_ma10_excl,
               MAX(vol_ma20_calc) AS vol_ma20,
               MAX(close_pos) FILTER (WHERE rn = 1) AS latest_close_pos,
               SUM(vol * bvc_buy_ratio) FILTER (WHERE rn <= 10) / NULLIF(SUM(vol) FILTER (WHERE rn <= 10), 0) AS active_buy_ratio_10d,
               SUM(vol) FILTER (WHERE rn <= 20 AND c > COALESCE(prev_c, o)) AS up_vol_20d,
               SUM(vol) FILTER (WHERE rn <= 20) AS total_vol_20d,
               SUM(vol) FILTER (WHERE rn <= 10 AND c > COALESCE(prev_c, o)) AS up_vol_10d,
               SUM(vol) FILTER (WHERE rn <= 10) AS total_vol_10d,
               -- 1. Rũ nến (Wyckoff Spring / Shakeout trong nền 10 phiên):
               -- Quét thủng đáy hỗ trợ 5-10 phiên (hoặc nhúng dưới MA20), biên độ >= 2.0%, rút chân dưới >= 50%, đóng cửa >= 60%
               BOOL_OR(
                 rn <= 10
                 AND spread_pct >= 0.020
                 AND lower_wick_ratio >= 0.50
                 AND close_pos >= 0.60
                 AND (
                   (min_prev5_l IS NOT NULL AND l <= min_prev5_l * 1.001 AND (ma20_c_roll IS NULL OR l <= ma20_c_roll * 0.995))
                   OR (min_prev10_l IS NOT NULL AND l <= min_prev10_l * 1.001)
                 )
                 AND vol >= COALESCE(NULLIF(vol_ma10_roll, 0), vol_ma10_calc) * 0.65
               ) AS ru_nen,
               MAX(lower_wick_ratio * 100.0) FILTER (
                 WHERE rn <= 10
                 AND spread_pct >= 0.020
                 AND lower_wick_ratio >= 0.50
                 AND close_pos >= 0.60
                 AND (
                   (min_prev5_l IS NOT NULL AND l <= min_prev5_l * 1.001 AND (ma20_c_roll IS NULL OR l <= ma20_c_roll * 0.995))
                   OR (min_prev10_l IS NOT NULL AND l <= min_prev10_l * 1.001)
                 )
                 AND vol >= COALESCE(NULLIF(vol_ma10_roll, 0), vol_ma10_calc) * 0.65
               ) AS ru_nen_wick_pct,
               MIN(rn) FILTER (
                 WHERE rn <= 10
                 AND spread_pct >= 0.020
                 AND lower_wick_ratio >= 0.50
                 AND close_pos >= 0.60
                 AND (
                   (min_prev5_l IS NOT NULL AND l <= min_prev5_l * 1.001 AND (ma20_c_roll IS NULL OR l <= ma20_c_roll * 0.995))
                   OR (min_prev10_l IS NOT NULL AND l <= min_prev10_l * 1.001)
                 )
                 AND vol >= COALESCE(NULLIF(vol_ma10_roll, 0), vol_ma10_calc) * 0.65
               )::int AS ru_nen_rn,
               -- 2. Test cung (No Supply Test trong nền 10 phiên):
               -- Biên độ hẹp <= 1.85%, có bóng dưới đỡ giá >= 40%, đóng cửa nửa trên >= 50%, Volume cạn kiệt <= 68% MA10
               BOOL_OR(
                 rn <= 10
                 AND spread_pct <= 0.0185
                 AND lower_wick_ratio >= 0.40
                 AND close_pos >= 0.50
                 AND vol <= COALESCE(NULLIF(vol_ma10_roll, 0), vol_ma10_calc) * 0.68
               ) AS test_cung,
               MIN((vol / NULLIF(COALESCE(NULLIF(vol_ma10_roll, 0), vol_ma10_calc), 0)) * 100.0) FILTER (
                 WHERE rn <= 10
                 AND spread_pct <= 0.0185
                 AND lower_wick_ratio >= 0.40
                 AND close_pos >= 0.50
                 AND vol <= COALESCE(NULLIF(vol_ma10_roll, 0), vol_ma10_calc) * 0.68
               ) AS test_cung_vol_pct,
               MIN(rn) FILTER (
                 WHERE rn <= 10
                 AND spread_pct <= 0.0185
                 AND lower_wick_ratio >= 0.40
                 AND close_pos >= 0.50
                 AND vol <= COALESCE(NULLIF(vol_ma10_roll, 0), vol_ma10_calc) * 0.68
               )::int AS test_cung_rn,
               -- 3. Dòng tiền (Pocket Pivot trong nền 10 phiên):
               -- Nến tăng (c > o, ret_pct >= 0.8%), đóng cửa >= 60%, Volume bùng nổ >= 1.45x MA10
               BOOL_OR(
                 rn <= 10
                 AND c > o
                 AND ret_pct >= 0.8
                 AND close_pos >= 0.60
                 AND vol >= COALESCE(NULLIF(vol_ma10_roll, 0), vol_ma10_calc) * 1.45
               ) AS vol_breakout_10d,
               MAX(vol / NULLIF(COALESCE(NULLIF(vol_ma10_roll, 0), vol_ma10_calc), 0)) FILTER (
                 WHERE rn <= 10
                 AND c > o
                 AND ret_pct >= 0.8
                 AND close_pos >= 0.60
               ) AS max_breakout_vol_mult,
               COUNT(*) FILTER (WHERE rn <= 20 AND ret_pct >= 1.5 AND vol >= vol_ma10_calc * 1.2)::int AS pulse_count_20d,
               ((MAX(h) FILTER (WHERE rn <= 20) - MIN(l) FILTER (WHERE rn <= 20)) / NULLIF(MIN(l) FILTER (WHERE rn <= 20), 0) * 100.0) AS swing_range_20d
        FROM bar_metrics
        GROUP BY ticker
      )
      SELECT a.ticker AS symbol,
             COALESCE(s.name, a.ticker) AS name,
             COALESCE(s.sector, 'OTHER_INDUSTRIALS') AS sector,
             a.latest_close,
             a.latest_close_adj,
             a.latest_change_pct,
             a.latest_vol,
             a.vol_ma10,
             a.vol_ma10_excl,
             a.vol_ma20,
             a.ru_nen,
             a.ru_nen_wick_pct,
             a.ru_nen_rn,
             a.test_cung,
             a.test_cung_vol_pct,
             a.test_cung_rn,
             a.vol_breakout_10d,
             a.max_breakout_vol_mult,
             a.up_vol_20d,
             a.total_vol_20d,
             a.up_vol_10d,
             a.total_vol_10d,
             a.active_buy_ratio_10d,
             a.pulse_count_20d,
             a.swing_range_20d,
             (t.indicators->>'mfi_14')::float8 AS mfi_14,
             (t.indicators->>'rsi_14')::float8 AS rsi_14,
             (t.indicators->>'ma20')::float8 AS ma20,
             (t.indicators->>'ma50')::float8 AS ma50,
             (t.indicators->>'momentum_1m')::float8 AS mom_1m,
             (t.indicators->>'volume_ratio')::float8 AS vol_ratio,
             b.max_date AS as_of
      FROM agg a
      JOIN stocks s ON s.symbol = a.ticker AND s.exchange = 'HOSE'
      CROSS JOIN bounds b
      LEFT JOIN LATERAL (
        SELECT indicators
        FROM technical_indicators
        WHERE symbol = a.ticker AND calc_date = b.max_date
        LIMIT 1
      ) t ON TRUE
      WHERE a.latest_close > 0
        AND (${filterSymbol ?? null}::text IS NOT NULL OR COALESCE(a.vol_ma10, a.vol_ma20) >= 50000)
    `;

    const items = rows.map((row) => {
      const rawClose = Number(row.latest_close ?? 0);
      const closeK = rawClose > 500 ? Number((rawClose / 1000).toFixed(2)) : Number(rawClose.toFixed(2));
      const priceVnd = rawClose < 500 ? Math.round(rawClose * 1000) : Math.round(rawClose);
      const closeAdj = Number(row.latest_close_adj ?? closeK);
      const changePct = row.latest_change_pct != null && Number.isFinite(Number(row.latest_change_pct))
        ? Number(Number(row.latest_change_pct).toFixed(2))
        : 0;
      const latestVol = Math.round(Number(row.latest_vol ?? 0));
      const predictVolRounded = latestVol >= 1_000_000
        ? Math.round(latestVol / 1000) * 1000
        : latestVol;
      const volMa10 = Math.max(1, Number(row.vol_ma10 ?? row.vol_ma20 ?? 1));
      const volMa10Excl = Math.max(1, Number(row.vol_ma10_excl ?? volMa10));
      const volMa20 = Math.max(1, Number(row.vol_ma20 ?? volMa10));
      const upVol20d = Number(row.up_vol_20d ?? 0);
      const totalVol20d = Math.max(1, Number(row.total_vol_20d ?? 1));
      const tienApDao = Math.min(100, Math.max(0, Math.round((upVol20d / totalVol20d) * 100)));

      const activeBuyRatio = Number.isFinite(Number(row.active_buy_ratio_10d))
        ? Number(row.active_buy_ratio_10d) * 100
        : 50;
      const mfi14 = Number.isFinite(Number(row.mfi_14)) ? Number(row.mfi_14) : activeBuyRatio;
      const avgBuyforce = Math.min(100, Math.max(1, Math.round(activeBuyRatio * 0.7 + mfi14 * 0.3)));

      const volRatio = latestVol / volMa10;
      const ruNen = Boolean(row.ru_nen);
      const testCung = Boolean(row.test_cung);
      const dongTien = Boolean(row.vol_breakout_10d);

      const ruNenWick = row.ru_nen_wick_pct != null && Number.isFinite(Number(row.ru_nen_wick_pct))
        ? Math.round(Number(row.ru_nen_wick_pct))
        : null;
      const ruNenRn = row.ru_nen_rn != null ? Number(row.ru_nen_rn) : null;
      const ruNenMeta = ruNen && ruNenWick != null
        ? `Rút ${ruNenWick}% · T-${Math.max(0, (ruNenRn ?? 1) - 1)}`
        : null;

      const testCungVolPct = row.test_cung_vol_pct != null && Number.isFinite(Number(row.test_cung_vol_pct))
        ? Math.round(Number(row.test_cung_vol_pct))
        : null;
      const testCungRn = row.test_cung_rn != null ? Number(row.test_cung_rn) : null;
      const testCungMeta = testCung && testCungVolPct != null
        ? `${testCungVolPct}% MA · T-${Math.max(0, (testCungRn ?? 1) - 1)}`
        : null;

      const breakoutMult = row.max_breakout_vol_mult != null && Number.isFinite(Number(row.max_breakout_vol_mult))
        ? Number(row.max_breakout_vol_mult)
        : volRatio;
      const dongTienMeta = dongTien
        ? `${Math.max(1.1, breakoutMult).toFixed(1)}x · MFI ${Math.round(mfi14)}`
        : null;

      const ma20 = Number.isFinite(Number(row.ma20)) ? Number(row.ma20) : closeAdj;
      const rsi14 = Number.isFinite(Number(row.rsi_14)) ? Number(row.rsi_14) : 50;
      // Chuẩn Lavender: KL mua tối đa = 3.0% MA10 Volume, làm tròn lô 100 CP
      const maxBuy = Math.max(100, Math.floor((volMa10 * 0.03) / 100) * 100);
      const maxBuyExcl = Math.max(100, Math.round((volMa10Excl * 0.03) / 100) * 100);
      // Chuẩn Lavender: % Đột biến KL = round((predict_vol / MA10 - 1) * 100)
      const average = Math.round(((predictVolRounded - volMa10) / volMa10) * 100);
      const surgePctExcl = Math.round(((predictVolRounded - volMa10Excl) / volMa10Excl) * 100);
      const trendUp = predictVolRounded >= volMa10;
      // Chuẩn Tạo lập đẩy giá (theme_stock_volume_predictions): Giá tăng, MA10_excl >= 500k, Đột biến >= 160%
      const taoLapSurge = changePct > 0 && volMa10Excl >= 500_000 && surgePctExcl >= 160;

      const signalCount = (ruNen ? 1 : 0) + (testCung ? 1 : 0) + (dongTien ? 1 : 0);
      const aboveMa20 = closeAdj >= ma20 * 0.99;
      const moMua = signalCount >= 2 && aboveMa20 && tienApDao >= 45 && avgBuyforce >= 55 && changePct > -2.0;

      const pulseCount = Number(row.pulse_count_20d ?? 0);
      const freq = `${pulseCount} phiên / 20P`;
      const swingRange = Number.isFinite(Number(row.swing_range_20d)) ? Number(row.swing_range_20d) : 0;
      const avgProfit = Number(Math.max(1, swingRange).toFixed(1));

      const vsaScore =
        (moMua ? 35 : 0) +
        (ruNen ? 20 : 0) +
        (testCung ? 20 : 0) +
        (dongTien ? 22 : 0) +
        tienApDao * 0.45 +
        avgBuyforce * 0.35 +
        Math.min(Math.max(average, -30), 100) * 0.15;

      const bottomScore =
        (rsi14 <= 42 ? 30 : rsi14 <= 48 ? 15 : 0) +
        (ruNen ? 25 : 0) +
        (testCung ? 15 : 0) +
        avgBuyforce * 0.35 +
        (changePct > 0 ? 12 : 0);

      return {
        code: String(row.symbol),
        symbol: String(row.symbol),
        name: String(row.name ?? row.symbol),
        sector: String(row.sector ?? 'OTHER_INDUSTRIALS'),
        close: closeK,
        price: priceVnd,
        changePct,
        avg_buyforce: avgBuyforce,
        ru_nen: ruNen,
        ru_nen_meta: ruNenMeta,
        test_cung: testCung,
        test_cung_meta: testCungMeta,
        dong_tien: dongTien,
        dong_tien_meta: dongTienMeta,
        tien_ap_dao: tienApDao,
        max_buy: maxBuy,
        max_buy_excl: maxBuyExcl,
        mo_mua: moMua,
        predict_vol: latestVol,
        vol_ma10: Math.round(volMa10),
        vol_ma10_excl: Math.round(volMa10Excl),
        vol_ma20: Math.round(volMa20),
        trend_up: trendUp,
        average,
        surge_pct_excl: surgePctExcl,
        tao_lap_surge: taoLapSurge,
        freq,
        avg_profit: avgProfit,
        rsi: Number(rsi14.toFixed(1)),
        mfi: Number(mfi14.toFixed(1)),
        vsa_score: Number(vsaScore.toFixed(1)),
        bottom_score: Number(bottomScore.toFixed(1)),
      };
    });

    items.sort((a, b) => b.vsa_score - a.vsa_score);

    return {
      items,
      total: items.length,
      asOf: rows[0]?.as_of ?? null,
      source: 'postgres',
    };
  });

  // Merge live Redis DNSE quotes if available in production / trading hours
  const symbols = basePayload.items.map((it) => it.symbol);
  const quotes = symbols.length
    ? await redisService.getCacheMany<Record<string, unknown>>(symbols.map((s) => `stock:${s}:quote`)).catch(() => [])
    : [];

  let liveCount = 0;
  const nowVn = new Date(new Date().toLocaleString('en-US', { timeZone: 'Asia/Ho_Chi_Minh' }));
  const vnMinutes = nowVn.getHours() * 60 + nowVn.getMinutes();
  const isTradingHours = vnMinutes >= 9 * 60 && vnMinutes <= 14 * 60 + 45;
  const elapsedTradingMin = !isTradingHours
    ? 255
    : vnMinutes <= 11 * 60 + 30
      ? Math.max(15, vnMinutes - 9 * 60)
      : vnMinutes < 13 * 60
        ? 150
        : Math.min(255, 150 + (vnMinutes - 13 * 60));
  const projectionFactor = isTradingHours ? 255 / Math.max(15, elapsedTradingMin) : 1;

  const mergedItems = basePayload.items.map((it, idx) => {
    const q = quotes[idx];
    if (!hasMarketPrice(q)) return it;
    liveCount += 1;
    const rawPrice = Number(q.price ?? q.close ?? it.price);
    const closeK = rawPrice > 500 ? Number((rawPrice / 1000).toFixed(2)) : Number(rawPrice.toFixed(2));
    const priceVnd = rawPrice < 500 ? Math.round(rawPrice * 1000) : Math.round(rawPrice);
    const rawChg = q.change_pct ?? q.changePercent;
    const changePct = rawChg != null && Number.isFinite(Number(rawChg)) ? Number(Number(rawChg).toFixed(2)) : it.changePct;
    const liveVol = Number(q.volume ?? q.totalVolume ?? 0);
    const projectedVol = liveVol > 0 ? Math.round(liveVol * projectionFactor) : it.predict_vol;
    const projectedRounded = projectedVol >= 1_000_000 ? Math.round(projectedVol / 1000) * 1000 : projectedVol;
    const volMa10 = Math.max(1, it.vol_ma10 ?? it.vol_ma20 ?? 1);
    const volMa10Excl = Math.max(1, it.vol_ma10_excl ?? volMa10);
    const average = Math.round(((projectedRounded - volMa10) / volMa10) * 100);
    const surgePctExcl = Math.round(((projectedRounded - volMa10Excl) / volMa10Excl) * 100);
    const trendUp = projectedRounded >= volMa10;
    const taoLapSurge = changePct > 0 && volMa10Excl >= 500_000 && surgePctExcl >= 160;
    return {
      ...it,
      close: closeK,
      price: priceVnd,
      changePct,
      predict_vol: projectedVol,
      average,
      surge_pct_excl: surgePctExcl,
      tao_lap_surge: taoLapSurge,
      trend_up: trendUp,
    };
  });

  return {
    ...basePayload,
    items: mergedItems,
    quoteSource: liveCount > 0 ? 'redis-dnse-live' : basePayload.source,
    liveQuoteCount: liveCount,
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
    if (book.source !== 'dnse-ws' || !Array.isArray(book.bids) || !Array.isArray(book.asks)) return;
    const bid = bestOrderBookLevel(book.bids, true);
    const ask = bestOrderBookLevel(book.asks, false);
    orderbooks[symbols[index]] = {
      ...book,
      symbol: symbols[index],
      bids: bid ? [bid] : [],
      asks: ask ? [ask] : [],
      receivedAt,
      stale: !receivedAt || age < -5_000 || age >= 15_000,
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
      const snapshot = rowsOf(live, 'stocks').length
        ? await withDbForeignFlows(live as Payload)
        : await dbSnapshot(exchange);
      return withRedisQuotes(snapshot);
    }),
  );
});

router.get('/sector-strength', (req, res, next) =>
  handle(req, res, next, () => dbSectorStrength()),
);

router.get('/super-stocks', (req, res, next) => {
  const symbol = typeof req.query.symbol === 'string' ? req.query.symbol : undefined;
  return handle(req, res, next, () => dbSuperStocks(symbol));
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

export async function warmMarketCaches(): Promise<void> {
  await Promise.allSettled([
    cached('market:indices', config.cacheTtl.indices, () => dbIndices()),
    cached('market:snapshot:api', config.cacheTtl.snapshot, async () => withRedisQuotes(await dbSnapshot())),
    cached('market:heatmap:api', 60, () => cached('market:heatmap:history', 300, () => dbHeatmap())),
    dbSectorStrength(),
    dbSuperStocks(),
  ]);
}

export default router;
