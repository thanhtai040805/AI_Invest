import { create } from 'zustand';

interface OrderBookLevel {
  price: number;
  volume: number;
  cumulativeVolume?: number;
}

interface OrderBookEntry {
  bids: OrderBookLevel[];
  asks: OrderBookLevel[];
  lastUpdate: string;
}

interface OrderBookState {
  books: Record<string, OrderBookEntry>;
  setOrderBook: (symbol: string, data: { bids: OrderBookLevel[]; asks: OrderBookLevel[]; lastUpdate?: string }) => void;
  getOrderBook: (symbol: string) => OrderBookEntry | undefined;
  clearOrderBook: (symbol: string) => void;
  clearAll: () => void;
}

export const useOrderBookStore = create<OrderBookState>((set, get) => ({
  books: {},

  setOrderBook: (symbol, data) => {
    const sym = symbol.toUpperCase();
    set((state) => ({
      books: {
        ...state.books,
        [sym]: {
          bids: data.bids ?? [],
          asks: data.asks ?? [],
          lastUpdate: data.lastUpdate ?? new Date().toISOString(),
        },
      },
    }));
  },

  getOrderBook: (symbol) => {
    return get().books[symbol.toUpperCase()];
  },

  clearOrderBook: (symbol) => {
    const sym = symbol.toUpperCase();
    set((state) => {
      const next = { ...state.books };
      delete next[sym];
      return { books: next };
    });
  },

  clearAll: () => set({ books: {} }),
}));
