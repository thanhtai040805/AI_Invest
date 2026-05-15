import { Decimal } from '@prisma/client/runtime/library';
import prisma from '../config/database';
import { aiEngineService } from './aiEngine.service';

interface SnapshotRow {
  symbol: string;
  name?: string;
  exchange?: string;
  industry?: string;
  price?: number;
  change?: number;
  changePercent?: number;
  volume?: number;
  open?: number;
  high?: number;
  low?: number;
  prevClose?: number;
  ceiling?: number;
  floor?: number;
  marketCap?: number;
}

export async function syncStocksFromEngine(): Promise<number> {
  const list = await aiEngineService.getStockList();
  const stocks: SnapshotRow[] = list.stocks ?? list ?? [];

  let count = 0;
  for (const row of stocks) {
    if (!row.symbol) continue;
    await prisma.stock.upsert({
      where: { symbol: row.symbol },
      create: {
        symbol: row.symbol,
        name: row.name ?? row.symbol,
        exchange: row.exchange ?? 'HOSE',
        industry: row.industry,
        ceiling: row.ceiling != null ? new Decimal(row.ceiling) : undefined,
        floor: row.floor != null ? new Decimal(row.floor) : undefined,
        refPrice: row.prevClose != null ? new Decimal(row.prevClose) : undefined,
        marketCap: row.marketCap != null ? BigInt(Math.round(row.marketCap)) : undefined,
      },
      update: {
        name: row.name ?? row.symbol,
        exchange: row.exchange ?? 'HOSE',
        industry: row.industry,
        ceiling: row.ceiling != null ? new Decimal(row.ceiling) : undefined,
        floor: row.floor != null ? new Decimal(row.floor) : undefined,
        refPrice: row.prevClose != null ? new Decimal(row.prevClose) : undefined,
        marketCap: row.marketCap != null ? BigInt(Math.round(row.marketCap)) : undefined,
      },
    });
    count++;
  }
  return count;
}

export async function backfillOhlcv(
  symbol: string,
  interval = '1D',
  start?: string,
  end?: string,
): Promise<number> {
  const data = await aiEngineService.getOHLCV(symbol, { interval, start, end });
  const candles = data.data ?? [];

  if (candles.length === 0) return 0;

  type CandleRow = { time: string; open: number; high: number; low: number; close: number; volume: number };
  const rows = candles.map((c: CandleRow) => ({
    time: new Date(c.time),
    symbol: symbol.toUpperCase(),
    open: new Decimal(c.open),
    high: new Decimal(c.high),
    low: new Decimal(c.low),
    close: new Decimal(c.close),
    volume: BigInt(c.volume ?? 0),
  }));

  for (const row of rows) {
    await prisma.ohlcv.upsert({
      where: { time_symbol: { time: row.time, symbol: row.symbol } },
      create: row,
      update: {
        open: row.open,
        high: row.high,
        low: row.low,
        close: row.close,
        volume: row.volume,
      },
    });
  }

  return rows.length;
}
