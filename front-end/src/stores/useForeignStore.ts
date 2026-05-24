import { create } from 'zustand';

export interface ForeignData {
  symbol: string;
  buyVolume: number;
  sellVolume: number;
  netVolume: number;
  buyValue: number;
  sellValue: number;
  netValue: number;
  lastUpdate?: string | number;
}

interface ForeignState {
  data: Record<string, ForeignData>;
  setForeign: (symbol: string, data: ForeignData) => void;
  getForeign: (symbol: string) => ForeignData | undefined;
  clear: (symbol: string) => void;
}

export const useForeignStore = create<ForeignState>((set, get) => ({
  data: {},
  setForeign: (symbol, data) => {
    const sym = symbol.toUpperCase();
    set((state) => ({ data: { ...state.data, [sym]: data } }));
  },
  getForeign: (symbol) => get().data[symbol.toUpperCase()],
  clear: (symbol) => {
    const sym = symbol.toUpperCase();
    set((state) => {
      const next = { ...state.data };
      delete next[sym];
      return { data: next };
    });
  },
}));
