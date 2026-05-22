import { Decimal } from '@prisma/client/runtime/library';
import prisma from '../config/database';
import { aiEngineService } from './aiEngine.service';
import { syncStocksFromEngine, backfillOhlcv } from './stockSync.service';

const VIETNAM_TIMEZONE_OFFSET = 7;

function getVietnamDate(): Date {
  const now = new Date();
  const utc = now.getTime() + now.getTimezoneOffset() * 60_000;
  return new Date(utc + VIETNAM_TIMEZONE_OFFSET * 3_600_000);
}

function isTradingDay(date: Date = getVietnamDate()): boolean {
  const day = date.getDay();
  if (day === 0 || day === 6) return false;

  const dateStr = date.toISOString().split('T')[0];
  const holidays = getVietnamHolidays(date.getFullYear());
  return !holidays.includes(dateStr);
}

function isMarketClosed(date: Date = getVietnamDate()): boolean {
  const hours = date.getHours();
  const minutes = date.getMinutes();
  const totalMinutes = hours * 60 + minutes;

  return totalMinutes >= 14 * 60 + 45;
}

function getVietnamHolidays(year: number): string[] {
  const holidays: string[] = [];

  const add = (month: number, day: number) => {
    holidays.push(`${year}-${String(month).padStart(2, '0')}-${String(day).padStart(2, '0')}`);
  };

  add(1, 1);
  add(1, 2);
  add(1, 3);
  add(4, 30);
  add(5, 1);
  add(9, 2);
  add(9, 3);

  return holidays;
}

async function getTodaySessionLog(): Promise<any | null> {
  const vnDate = getVietnamDate();
  const startOfDay = new Date(vnDate);
  startOfDay.setHours(0, 0, 0, 0);

  return prisma.marketSessionLog.findFirst({
    where: {
      sessionDate: {
        gte: startOfDay,
        lt: new Date(startOfDay.getTime() + 24 * 60 * 60 * 1000),
      },
    },
  });
}

async function createSessionLog(): Promise<any> {
  const vnDate = getVietnamDate();
  const startOfDay = new Date(vnDate);
  startOfDay.setHours(0, 0, 0, 0);

  return prisma.marketSessionLog.create({
    data: {
      sessionDate: startOfDay,
      status: 'PENDING',
    },
  });
}

async function updateSessionLog(
  logId: string,
  data: { status: string; stockCount?: number; ohlcvCount?: number; error?: string }
): Promise<void> {
  await prisma.marketSessionLog.update({
    where: { id: logId },
    data: {
      ...data,
      completedAt: data.status === 'COMPLETED' || data.status === 'FAILED' ? new Date() : undefined,
    },
  });
}

async function backfillTodaySnapshot(): Promise<number> {
  const count = await syncStocksFromEngine();
  return count;
}

async function backfillTodayOhlcv(symbols: string[]): Promise<number> {
  const vnDate = getVietnamDate();
  const todayStr = vnDate.toISOString().split('T')[0];

  let total = 0;
  for (const symbol of symbols) {
    try {
      const count = await backfillOhlcv(symbol, '1D', todayStr, todayStr);
      total += count;
    } catch {
      // skip individual symbol failures
    }
  }
  return total;
}

export async function autoBackfillIfNeeded(): Promise<{ triggered: boolean; reason: string }> {
  const vnDate = getVietnamDate();
  const dateStr = vnDate.toISOString().split('T')[0];

  if (!isTradingDay(vnDate)) {
    return { triggered: false, reason: `${dateStr} is not a trading day (weekend/holiday)` };
  }

  const existingLog = await getTodaySessionLog();
  if (existingLog) {
    if (existingLog.status === 'COMPLETED') {
      return { triggered: false, reason: `${dateStr} session already backfilled (${existingLog.stockCount} stocks, ${existingLog.ohlcvCount} candles)` };
    }
    if (existingLog.status === 'BACKFILLING') {
      return { triggered: false, reason: `${dateStr} session backfill in progress` };
    }
  }

  if (!isMarketClosed(vnDate)) {
    return { triggered: false, reason: `Market still open (${vnDate.getHours()}:${String(vnDate.getMinutes()).padStart(2, '0')} VN time)` };
  }

  console.log(`[Backfill] Triggering auto-backfill for ${dateStr}...`);

  let log: any;
  try {
    log = existingLog || await createSessionLog();
    await updateSessionLog(log.id, { status: 'BACKFILLING' });

    const stockCount = await backfillTodaySnapshot();
    await updateSessionLog(log.id, { status: 'BACKFILLING', stockCount });

    const stocks = await prisma.stock.findMany({ select: { symbol: true } });
    const ohlcvCount = await backfillTodayOhlcv(stocks.map((s: any) => s.symbol));

    await updateSessionLog(log.id, {
      status: 'COMPLETED',
      stockCount,
      ohlcvCount,
    });

    console.log(`[Backfill] ${dateStr} completed: ${stockCount} stocks, ${ohlcvCount} candles`);
    return { triggered: true, reason: `Backfilled ${stockCount} stocks, ${ohlcvCount} candles for ${dateStr}` };
  } catch (err: any) {
    if (log) {
      await updateSessionLog(log.id, { status: 'FAILED', error: err.message });
    }
    console.error(`[Backfill] ${dateStr} failed:`, err.message);
    return { triggered: false, reason: `Backfill failed: ${err.message}` };
  }
}

export async function getBackfillHistory(limit = 30): Promise<any[]> {
  return prisma.marketSessionLog.findMany({
    orderBy: { sessionDate: 'desc' },
    take: limit,
  });
}
