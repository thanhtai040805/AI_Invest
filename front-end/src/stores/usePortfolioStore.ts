import { create } from 'zustand';
import { PortfolioStats, AssetPosition } from '@/types/portfolio';

interface PortfolioState {
  summary: PortfolioStats;
  assets: AssetPosition[];
  setSummary: (summary: PortfolioStats) => void;
  setAssets: (assets: AssetPosition[]) => void;
}

const emptySummary: PortfolioStats = {
  totalEquity: 0,
  totalProfit: 0,
  totalProfitPercent: 0,
  dailyPnL: 0,
  dailyPnLPercent: 0,
  buyingPower: 0,
  assetsCount: 0,
  holdings: [],
};

export const usePortfolioStore = create<PortfolioState>((set) => ({
  summary: emptySummary,
  assets: [],
  setSummary: (summary) => set({ summary }),
  setAssets: (assets) => set({ assets }),
}));
