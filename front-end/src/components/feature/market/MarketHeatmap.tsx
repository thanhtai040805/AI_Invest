"use client";

import { GlassCard } from "@/components/ui/GlassCard";
import { cn } from "@/lib/utils";
import { useMarketStore } from "@/stores/useMarketStore";

export function MarketHeatmap() {
  const sectors = useMarketStore((state) => state.sectors);

  return (
    <GlassCard className="p-xl border-white/5">
      <div className="flex justify-between items-center mb-lg">
         <h3 className="font-label-caps text-[10px] tracking-widest opacity-60 uppercase">Biến động Nhóm ngành</h3>
         <span className="material-symbols-outlined text-xs opacity-40">grid_view</span>
      </div>
      
      <div className="grid grid-cols-2 gap-2">
        {sectors.map((sector, i) => (
          <div 
            key={i}
            className={cn(
              "p-3 rounded-xl flex flex-col justify-between min-h-[70px] transition-all cursor-pointer hover:brightness-110 active:scale-95",
              sector.color,
              sector.color.includes('bg-error') ? "bg-opacity-20 border border-error/30" : 
              sector.color.includes('bg-secondary') ? "bg-opacity-20 border border-secondary/30" :
              "bg-opacity-10 border border-white/10"
            )}
          >
            <span className="text-[10px] font-bold truncate opacity-80 uppercase leading-none">{sector.name}</span>
            <span className={cn(
              "text-sm font-bold font-data-mono",
              sector.change > 0 ? "text-secondary" : sector.change < 0 ? "text-error" : "text-on-surface"
            )}>
              {sector.change > 0 ? '+' : ''}{sector.change}%
            </span>
          </div>
        ))}
      </div>
    </GlassCard>
  );
}
