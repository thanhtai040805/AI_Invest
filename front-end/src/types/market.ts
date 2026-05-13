export type MarketTrend = 'up' | 'down' | 'steady';

export interface MarketIndex {
  name: string;
  value: number;
  change: number;
  changePercent: number;
  volume?: number;
  tradingValue?: number;
  trend: MarketTrend;
}

export interface SectorPerformance {
  name: string;
  change: number;
  weight: number;
  color: string;
}

export interface MarketBreadth {
  advancers: number;
  decliners: number;
  unchanged: number;
  lastUpdate: string;
}

export interface LiquidityPoint {
  time: string;
  today: number;
  yesterday: number;
}
