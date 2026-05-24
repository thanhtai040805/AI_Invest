import { create } from 'zustand';

export interface ExpectedPriceData {
  symbol: string;
  expectedPrice: number;
  matchedVolume: number;
  receivedAt?: number | null;
  lastUpdate?: string | number;
}

interface ExpectedPriceState {
  data: Record<string, ExpectedPriceData>;
  setExpectedPrice: (symbol: string, data: ExpectedPriceData) => void;
  getExpectedPrice: (symbol: string) => ExpectedPriceData | undefined;
  clear: (symbol: string) => void;
}

export const useExpectedPriceStore = create<ExpectedPriceState>((set, get) => ({
  data: {},
  setExpectedPrice: (symbol, data) => {
    const sym = symbol.toUpperCase();
    set((state) => ({ data: { ...state.data, [sym]: data } }));
  },
  getExpectedPrice: (symbol) => get().data[symbol.toUpperCase()],
  clear: (symbol) => {
    const sym = symbol.toUpperCase();
    set((state) => {
      const next = { ...state.data };
      delete next[sym];
      return { data: next };
    });
  },
}));
