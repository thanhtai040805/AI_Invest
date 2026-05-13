import { create } from 'zustand';
import { MarketIndex, SectorPerformance, MarketBreadth, LiquidityPoint } from '@/types/market';

interface MarketState {
  indices: MarketIndex[];
  sectors: SectorPerformance[];
  breadth: MarketBreadth;
  liquidity: LiquidityPoint[];
  
  setIndices: (indices: MarketIndex[]) => void;
  setSectors: (sectors: SectorPerformance[]) => void;
  setBreadth: (breadth: MarketBreadth) => void;
  setLiquidity: (liquidity: LiquidityPoint[]) => void;
}

export const useMarketStore = create<MarketState>((set) => ({
  indices: [
    { name: 'VN-INDEX', value: 1284.5, change: 12.4, changePercent: 1.02, volume: 842100000, tradingValue: 21450200000000, trend: 'up' },
    { name: 'VN30', value: 1302.1, change: 15.2, changePercent: 1.18, volume: 245600000, tradingValue: 12560000000000, trend: 'up' },
    { name: 'HNX', value: 242.8, change: -0.4, changePercent: -0.16, volume: 98400000, tradingValue: 1840000000000, trend: 'down' },
  ],
  sectors: [
    { name: "Ngân hàng", change: 1.2, weight: 35, color: "bg-secondary" },
    { name: "Bất động sản", change: -0.8, weight: 20, color: "bg-error" },
    { name: "Chứng khoán", change: 2.5, weight: 15, color: "bg-secondary" },
    { name: "Thép", change: 0.2, weight: 10, color: "bg-yellow-500" },
    { name: "Bán lẻ", change: -1.5, weight: 8, color: "bg-error" },
    { name: "Dầu khí", change: 0.5, weight: 7, color: "bg-secondary" },
    { name: "Khác", change: 0.0, weight: 5, color: "bg-white/10" },
  ],
  breadth: {
    advancers: 245,
    decliners: 92,
    unchanged: 42,
    lastUpdate: new Date().toISOString(),
  },
  liquidity: [
    { time: "9:15", today: 1200, yesterday: 1000 },
    { time: "10:00", today: 3500, yesterday: 3100 },
    { time: "11:00", today: 8200, yesterday: 7500 },
    { time: "13:30", today: 11500, yesterday: 10500 },
    { time: "14:15", today: 18200, yesterday: 16800 },
    { time: "14:45", today: 21450, yesterday: 19500 },
  ],
  setIndices: (indices) => set({ indices }),
  setSectors: (sectors) => set({ sectors }),
  setBreadth: (breadth) => set({ breadth }),
  setLiquidity: (liquidity) => set({ liquidity }),
}));
