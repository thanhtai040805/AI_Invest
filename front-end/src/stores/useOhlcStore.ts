import { create } from 'zustand';

export interface LiveOhlcBar {
  timestamp: number | null;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  resolution: string;
}

interface OhlcState {
  bars: Record<string, LiveOhlcBar[]>;
  setBar: (symbol: string, bar: LiveOhlcBar) => void;
  closeBar: (symbol: string, bar: LiveOhlcBar) => void;
  getBars: (symbol: string) => LiveOhlcBar[];
  clear: (symbol: string) => void;
}

const MAX_BARS = 100;

export const useOhlcStore = create<OhlcState>((set, get) => ({
  bars: {},
  setBar: (symbol, bar) => {
    const sym = symbol.toUpperCase();
    set((state) => {
      const existing = state.bars[sym] ?? [];
      const last = existing[existing.length - 1];
      const updated =
        last && last.timestamp === bar.timestamp
          ? [...existing.slice(0, -1), bar]
          : [...existing, bar];
      return { bars: { ...state.bars, [sym]: updated.slice(-MAX_BARS) } };
    });
  },
  closeBar: (symbol, bar) => {
    const sym = symbol.toUpperCase();
    set((state) => {
      const existing = state.bars[sym] ?? [];
      const updated = [...existing, { ...bar, resolution: `${bar.resolution}_closed` }];
      return { bars: { ...state.bars, [sym]: updated.slice(-MAX_BARS) } };
    });
  },
  getBars: (symbol) => get().bars[symbol.toUpperCase()] ?? [],
  clear: (symbol) => {
    const sym = symbol.toUpperCase();
    set((state) => {
      const next = { ...state.bars };
      delete next[sym];
      return { bars: next };
    });
  },
}));
