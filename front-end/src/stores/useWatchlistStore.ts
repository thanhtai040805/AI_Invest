import { create } from 'zustand';
import { persist, createJSONStorage } from 'zustand/middleware';

interface WatchlistState {
  symbols: string[];
  lastViewedSymbol: string | null;
  addSymbol: (symbol: string) => void;
  removeSymbol: (symbol: string) => void;
  setLastViewed: (symbol: string) => void;
  reorderSymbols: (symbols: string[]) => void;
  clear: () => void;
}

const MAX_WATCHLIST_SIZE = 50;

export const useWatchlistStore = create<WatchlistState>()(
  persist(
    (set, get) => ({
      symbols: ['VNM', 'FPT', 'VIC', 'SSI', 'HPG', 'VCB', 'TCB'],
      lastViewedSymbol: null,

      addSymbol: (symbol) => {
        const sym = symbol.toUpperCase();
        const current = get().symbols;
        if (current.includes(sym)) return;
        if (current.length >= MAX_WATCHLIST_SIZE) return;
        set({ symbols: [...current, sym] });
      },

      removeSymbol: (symbol) => {
        const sym = symbol.toUpperCase();
        set({ symbols: get().symbols.filter((s) => s !== sym) });
      },

      setLastViewed: (symbol) => {
        set({ lastViewedSymbol: symbol.toUpperCase() });
      },

      reorderSymbols: (symbols) => {
        set({ symbols: symbols.map((s) => s.toUpperCase()) });
      },

      clear: () => {
        set({ symbols: [], lastViewedSymbol: null });
      },
    }),
    {
      name: 'aiinvest-watchlist',
      storage: createJSONStorage(() => localStorage),
      partialize: (state) => ({
        symbols: state.symbols,
        lastViewedSymbol: state.lastViewedSymbol,
      }),
    }
  )
);
