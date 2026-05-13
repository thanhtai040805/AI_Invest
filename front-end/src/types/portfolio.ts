export interface PortfolioStats {
  totalEquity: number;
  totalProfit: number;
  totalProfitPercent: number;
  dailyPnL: number;
  dailyPnLPercent: number;
  buyingPower: number;
  assetsCount: number;
  holdings: string[];
}

export interface AssetPosition {
  symbol: string;
  quantity: number;
  avgPrice: number;
  currentPrice: number;
  currentValue: number;
  profit: number;
  profitPercent: number;
}
