import { create } from 'zustand';
import { SectorPerformance } from '@/types/market';

interface HeatmapState {
  sectors: SectorPerformance[];
  setHeatmap: (sectors: SectorPerformance[]) => void;
  clear: () => void;
}

export const useHeatmapStore = create<HeatmapState>((set) => ({
  sectors: [],

  setHeatmap: (sectors) => set({ sectors }),

  clear: () => set({ sectors: [] }),
}));
