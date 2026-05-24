import { create } from 'zustand';
import { MarketIndex, SectorPerformance, MarketBreadth, LiquidityPoint } from '@/types/market';

interface MarketState {
  indices: MarketIndex[];
  sectors: SectorPerformance[];
  breadth: MarketBreadth;
  liquidity: LiquidityPoint[];
  
  setIndices: (indices: MarketIndex[]) => void;
  updateIndex: (name: string, data: Partial<MarketIndex>) => void;
  setSectors: (sectors: SectorPerformance[]) => void;
  setBreadth: (breadth: MarketBreadth) => void;
  setLiquidity: (liquidity: LiquidityPoint[]) => void;
}

export const useMarketStore = create<MarketState>((set) => ({
  indices: [],
  sectors: [],
  breadth: {
    advancers: 0,
    decliners: 0,
    unchanged: 0,
    lastUpdate: new Date().toISOString(),
  },
  liquidity: [],
  setIndices: (indices) => set({ indices }),
  updateIndex: (name, data) =>
    set((state) => ({
      indices: state.indices.map((idx) =>
        idx.name.toUpperCase() === name.toUpperCase() ? { ...idx, ...data } : idx
      ),
    })),
  setSectors: (sectors) => set({ sectors }),
  setBreadth: (breadth) => set({ breadth }),
  setLiquidity: (liquidity) => set({ liquidity }),
}));
