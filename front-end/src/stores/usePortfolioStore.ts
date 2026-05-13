import { create } from 'zustand';
import { PortfolioStats, AssetPosition } from '@/types/portfolio';

interface PortfolioState {
  summary: PortfolioStats;
  assets: AssetPosition[];
  setSummary: (summary: PortfolioStats) => void;
  setAssets: (assets: AssetPosition[]) => void;
}

export const usePortfolioStore = create<PortfolioState>((set) => ({
  summary: {
    totalEquity: 1245800000,
    totalProfit: 14500000,
    totalProfitPercent: 1.2,
    dailyPnL: 5200000,
    dailyPnLPercent: 0.42,
    buyingPower: 450200000,
    assetsCount: 3,
    holdings: ['FPT', 'VCB', 'HPG'],
  },
  assets: [
    {
      symbol: 'FPT',
      quantity: 1000,
      avgPrice: 110200,
      currentPrice: 114200,
      currentValue: 114200000,
      profit: 4000000,
      profitPercent: 3.63,
    },
    // More positions...
  ],
  setSummary: (summary) => set({ summary }),
  setAssets: (assets) => set({ assets }),
}));
