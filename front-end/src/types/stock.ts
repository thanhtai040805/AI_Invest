import { MarketTrend } from './market';

export type AISignal = 'MUA' | 'BÁN' | 'THEO DÕI' | 'NẮM GIỮ';

export interface StockQuote {
  symbol: string;
  name: string;
  price: number;
  change: number;
  changePercent: number;
  volume: number;
  tradingValue: number;
  open: number;
  high: number;
  low: number;
  prevClose: number;
  ceiling: number;
  floor: number;
  avgVolume?: number;
  marketCap?: number;
  foreignNetBuy?: number;
  industry?: string;
  exchange?: string;
  sector?: string;
  signal: AISignal;
  trend: MarketTrend;
  lastUpdate: string;
}

export interface OrderBookLevel {
  price: number;
  volume: number;
  percent: number;
  cumulativeVolume?: number;
  imbalance?: number; // % imbalance between bid/ask at this level
}

export interface StockOrderBook {
  symbol: string;
  bids: OrderBookLevel[];
  asks: OrderBookLevel[];
  totalBidVol: number;
  totalAskVol: number;
  spread: number;
  spreadPercent: number;
}

export interface KLineData {
  timestamp: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume?: number;
}
