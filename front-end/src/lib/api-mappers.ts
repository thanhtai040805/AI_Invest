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

export function mapIndicesResponse(data: unknown): MarketIndex[] {
  if (!data || typeof data !== 'object') return [];
  const d = data as { indices?: MarketIndex[] };
  const list = Array.isArray(d) ? d : d?.indices ?? [];
  return list.map((idx: MarketIndex) => ({
    ...idx,
    trend: idx.trend ?? trendFromChange(idx.changePercent ?? 0),
  }));
}

export function mapBreadthResponse(data: unknown): MarketBreadth {
  if (!data || typeof data !== 'object') {
    return { advancers: 0, decliners: 0, unchanged: 0, lastUpdate: new Date().toISOString() };
  }
  const d = data as Partial<MarketBreadth>;
  return {
    advancers: d.advancers ?? 0,
    decliners: d.decliners ?? 0,
    unchanged: d.unchanged ?? 0,
    lastUpdate: d.lastUpdate ?? new Date().toISOString(),
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

export function mapSnapshotToQuotes(data: unknown): StockQuote[] {
  if (!data || typeof data !== 'object') return [];
  const d = data as { stocks?: Record<string, unknown>[] };
  return (d.stocks ?? []).map(mapRowToQuote).filter((q) => q.symbol);
}

export function mapRowToQuote(row: unknown): StockQuote {
  if (!row || typeof row !== 'object') {
    return { symbol: '', name: '', price: 0, change: 0, changePercent: 0, volume: 0, tradingValue: 0, open: 0, high: 0, low: 0, prevClose: 0, ceiling: 0, floor: 0, avgVolume: 0, marketCap: 0, foreignNetBuy: 0, industry: '', exchange: '', sector: '', signal: 'THEO DÕI', trend: 'steady', lastUpdate: '' };
  }
  const r = row as Record<string, unknown>;
  const price = Number(r.price ?? 0);
  const change = Number(r.change ?? 0);
  const changePercent = Number(r.changePercent ?? 0);
  const prevClose = Number(r.prevClose ?? price - change);
  const volume = Number(r.volume ?? 0);

  return {
    symbol: String(r.symbol ?? ''),
    name: String(r.name ?? r.symbol ?? ''),
    price,
    change,
    changePercent,
    volume,
    tradingValue: Number(r.tradingValue ?? price * volume),
    open: Number(r.open ?? price),
    high: Number(r.high ?? price),
    low: Number(r.low ?? price),
    prevClose,
    ceiling: Number(r.ceiling ?? 0),
    floor: Number(r.floor ?? 0),
    avgVolume: Number(r.avgVolume ?? volume),
    marketCap: Number(r.marketCap ?? 0),
    foreignNetBuy: Number(r.foreignNetBuy ?? 0),
    industry: String(r.industry ?? r.sector ?? ''),
    exchange: String(r.exchange ?? ''),
    sector: String(r.sector ?? r.industry ?? ''),
    signal: signalFromChange(changePercent),
    trend: trendFromChange(changePercent),
    lastUpdate: String(r.lastUpdate ?? new Date().toISOString()),
  };
}

export function mapQuoteResponse(row: unknown): StockQuote {
  return mapRowToQuote(row);
}
