import { create } from 'zustand';

export interface TradeEntry {
  time: string;
  price: number;
  volume: number;
  side: 'buy' | 'sell';
}

interface TradesState {
  trades: Record<string, TradeEntry[]>;
  addTrade: (symbol: string, trade: TradeEntry) => void;
  setTrades: (symbol: string, trades: TradeEntry[]) => void;
  getTrades: (symbol: string) => TradeEntry[];
  clearTrades: (symbol: string) => void;
  clearAll: () => void;
}

const MAX_TRADES_PER_SYMBOL = 100;

export const useTradesStore = create<TradesState>((set, get) => ({
  trades: {},

  addTrade: (symbol, trade) => {
    const sym = symbol.toUpperCase();
    set((state) => {
      const existing = state.trades[sym] ?? [];
      const updated = [trade, ...existing].slice(0, MAX_TRADES_PER_SYMBOL);
      return {
        trades: {
          ...state.trades,
          [sym]: updated,
        },
      };
    });
  },

  setTrades: (symbol, trades) => {
    const sym = symbol.toUpperCase();
    set((state) => ({
      trades: {
        ...state.trades,
        [sym]: trades.slice(0, MAX_TRADES_PER_SYMBOL),
      },
    }));
  },

  getTrades: (symbol) => {
    return get().trades[symbol.toUpperCase()] ?? [];
  },

  clearTrades: (symbol) => {
    const sym = symbol.toUpperCase();
    set((state) => {
      const next = { ...state.trades };
      delete next[sym];
      return { trades: next };
    });
  },

  clearAll: () => set({ trades: {} }),
}));
