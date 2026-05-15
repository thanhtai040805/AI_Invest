import { MarketIndex, MarketBreadth, LiquidityPoint, SectorPerformance } from '@/types/market';
import { StockQuote, AISignal } from '@/types/stock';

function trendFromChange(pct: number): 'up' | 'down' | 'steady' {
  if (pct > 0) return 'up';
  if (pct < 0) return 'down';
  return 'steady';
}

function signalFromChange(pct: number): AISignal {
  if (pct > 2) return 'MUA';
  if (pct < -2) return 'BÁN';
  return 'THEO DÕI';
}

export function mapIndicesResponse(data: { indices?: MarketIndex[] } | MarketIndex[]): MarketIndex[] {
  const list = Array.isArray(data) ? data : data?.indices ?? [];
  return list.map((idx) => ({
    ...idx,
    trend: idx.trend ?? trendFromChange(idx.changePercent ?? 0),
  }));
}

export function mapBreadthResponse(data: MarketBreadth): MarketBreadth {
  return {
    advancers: data.advancers ?? 0,
    decliners: data.decliners ?? 0,
    unchanged: data.unchanged ?? 0,
    lastUpdate: data.lastUpdate ?? new Date().toISOString(),
  };
}

export function mapLiquidityResponse(data: {
  points?: LiquidityPoint[];
  current?: number;
}): LiquidityPoint[] {
  if (data.points?.length) return data.points;
  return [];
}

export function mapHeatmapSectors(data: {
  sectors?: Array<{ name: string; change: number; weight?: number; color?: string }>;
}): SectorPerformance[] {
  return (data.sectors ?? []).map((s) => ({
    name: s.name,
    change: s.change,
    weight: s.weight ?? 10,
    color: s.color ?? (s.change >= 0 ? 'bg-secondary' : 'bg-error'),
  }));
}

export function mapSnapshotToQuotes(data: { stocks?: Record<string, unknown>[] }): StockQuote[] {
  return (data.stocks ?? []).map(mapRowToQuote).filter((q) => q.symbol);
}

export function mapRowToQuote(row: Record<string, unknown>): StockQuote {
  const price = Number(row.price ?? 0);
  const change = Number(row.change ?? 0);
  const changePercent = Number(row.changePercent ?? 0);
  const prevClose = Number(row.prevClose ?? price - change);
  const volume = Number(row.volume ?? 0);

  return {
    symbol: String(row.symbol ?? ''),
    name: String(row.name ?? row.symbol ?? ''),
    price,
    change,
    changePercent,
    volume,
    tradingValue: Number(row.tradingValue ?? price * volume),
    open: Number(row.open ?? price),
    high: Number(row.high ?? price),
    low: Number(row.low ?? price),
    prevClose,
    ceiling: Number(row.ceiling ?? 0),
    floor: Number(row.floor ?? 0),
    avgVolume: Number(row.avgVolume ?? volume),
    marketCap: Number(row.marketCap ?? 0),
    foreignNetBuy: Number(row.foreignNetBuy ?? 0),
    signal: signalFromChange(changePercent),
    trend: trendFromChange(changePercent),
    lastUpdate: String(row.lastUpdate ?? new Date().toISOString()),
  };
}

export function mapQuoteResponse(row: Record<string, unknown>): StockQuote {
  return mapRowToQuote(row);
}
