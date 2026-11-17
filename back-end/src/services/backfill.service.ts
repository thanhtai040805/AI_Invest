import { Decimal } from '@prisma/client/runtime/library';
import prisma from '../config/database';
import { config } from '../config';
import { aiEngineService } from './aiEngine.service';
import { syncStocksFromEngine, backfillOhlcv } from './stockSync.service';

const VIETNAM_TIMEZONE_OFFSET = 7;

function getVietnamParts(now: Date = new Date()) {
  const vn = new Date(now.getTime() + VIETNAM_TIMEZONE_OFFSET * 3_600_000);
  return {
    year: vn.getUTCFullYear(),
    dayOfWeek: vn.getUTCDay(),
    hours: vn.getUTCHours(),
    minutes: vn.getUTCMinutes(),
    dateStr: vn.toISOString().slice(0, 10),
  };
}

function isTradingDay(vn = getVietnamParts()): boolean {
  if (vn.dayOfWeek === 0 || vn.dayOfWeek === 6) return false;
  const holidays = getVietnamHolidays(vn.year);
  return !holidays.includes(vn.dateStr);
}

function isMarketClosed(vn = getVietnamParts()): boolean {
  const totalMinutes = vn.hours * 60 + vn.minutes;

  // Wait until 15:00 ICT (after HOSE 14:30-14:45 ATC + 14:45-15:00 Negotiated/PLO settlement)
  return totalMinutes >= 15 * 60;
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
  const { dateStr } = getVietnamParts();
  const startOfDay = new Date(`${dateStr}T00:00:00.000Z`);

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
  const { dateStr } = getVietnamParts();
  const startOfDay = new Date(`${dateStr}T00:00:00.000Z`);

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
  const { dateStr: todayStr } = getVietnamParts();

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
  if (!config.dnse.enabled) {
    return { triggered: false, reason: 'Auto-backfill skipped (DNSE_ENABLED=false)' };
  }
  const vn = getVietnamParts();
  const { dateStr } = vn;

  if (!isTradingDay(vn)) {
    return { triggered: false, reason: `${dateStr} is not a trading day (weekend/holiday)` };
  }

  const existingLog = await getTodaySessionLog();
  if (existingLog) {
    const minExpectedCandles = Math.max(1, Math.floor((existingLog.stockCount || 350) * 0.8));
    if (existingLog.status === 'COMPLETED' && existingLog.ohlcvCount >= minExpectedCandles) {
      return { triggered: false, reason: `${dateStr} session already backfilled (${existingLog.stockCount} stocks, ${existingLog.ohlcvCount} candles)` };
    }
    if (existingLog.status === 'BACKFILLING') {
      return { triggered: false, reason: `${dateStr} session backfill in progress` };
    }
  }

  if (!isMarketClosed(vn)) {
    return { triggered: false, reason: `Market still open (${vn.hours}:${String(vn.minutes).padStart(2, '0')} VN time)` };
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
    if (ohlcvCount === 0) {
      throw new Error('No OHLCV candles were backfilled; the session remains retryable');
    }

    const minCompleteThreshold = Math.max(1, Math.floor((stocks.length || stockCount || 350) * 0.8));
    const finalStatus = ohlcvCount >= minCompleteThreshold ? 'COMPLETED' : 'PARTIAL';

    await updateSessionLog(log.id, {
      status: finalStatus,
      stockCount,
      ohlcvCount,
    });

    console.log(`[Backfill] ${dateStr} ${finalStatus.toLowerCase()}: ${stockCount} stocks, ${ohlcvCount} candles`);
    return { triggered: true, reason: `Backfilled ${stockCount} stocks, ${ohlcvCount} candles (${finalStatus}) for ${dateStr}` };
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
