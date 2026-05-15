import { create } from 'zustand';
import { StockQuote } from '@/types/stock';

interface StockState {
  stocks: StockQuote[];
  searchQuery: string;
  setStocks: (stocks: StockQuote[]) => void;
  updateStock: (symbol: string, updates: Partial<StockQuote>) => void;
  setSearchQuery: (query: string) => void;
}

export const useStockStore = create<StockState>((set) => ({
  stocks: [],
  searchQuery: '',
  setStocks: (stocks) => set({ stocks }),
  updateStock: (symbol, updates) => set((state) => ({
    stocks: state.stocks.map((s) => s.symbol === symbol ? { ...s, ...updates } : s)
  })),
  setSearchQuery: (searchQuery) => set({ searchQuery }),
}));
