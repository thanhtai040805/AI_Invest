import { create } from 'zustand';

export interface LiquidityData {
  totalValueBillion: number;
  stockCount: number;
  topByVolume: Array<{
    symbol: string;
    volume: number;
    tradingValue: number;
    price: number;
  }>;
  lastUpdate: string;
}

interface LiquidityState {
  data: LiquidityData | null;
  setLiquidity: (data: LiquidityData) => void;
  clear: () => void;
}

export const useLiquidityStore = create<LiquidityState>((set) => ({
  data: null,

  setLiquidity: (data) => set({ data }),

  clear: () => set({ data: null }),
}));
