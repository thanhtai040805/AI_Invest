"use client";

import { GlassCard } from "@/components/ui/GlassCard";
import { cn } from "@/lib/utils";
import { useRouter } from "next/navigation";
import { useHeatmapStore } from "@/stores/useHeatmapStore";
import { motion } from "framer-motion";

export function MarketHeatmap() {
  const sectors = useHeatmapStore((state) => state.sectors);
  const router = useRouter();

  if (!sectors.length) {
    return (
      <GlassCard className="p-lg border-white/5 bg-surface-container-lowest/30 backdrop-blur-xl">
        <h3 className="font-outfit text-[10px] font-extrabold tracking-widest text-on-surface-variant uppercase opacity-50">
          Biến động Nhóm ngành
        </h3>
        <div className="flex items-center gap-xs mt-md text-xs text-on-surface-variant/60 font-outfit">
          <span className="w-4 h-4 border-2 border-primary border-t-transparent rounded-full animate-spin" />
          Đang phân tích biến động ngành...
        </div>
      </GlassCard>
    );
  }

  return (
    <GlassCard className="p-lg border-white/5 bg-surface-container-lowest/30 backdrop-blur-xl">
      <div className="flex justify-between items-center mb-lg">
        <h3 className="font-outfit text-[10px] font-extrabold tracking-widest text-on-surface-variant uppercase opacity-50">
          Biến động Nhóm ngành
        </h3>
        <div className="w-6 h-6 rounded-lg bg-white/5 flex items-center justify-center border border-white/5">
          <span className="material-symbols-outlined text-[14px] text-on-surface-variant">grid_view</span>
        </div>
      </div>

      <div className="max-h-[340px] overflow-y-auto pr-1 -mr-1 space-y-2">
        <div className="grid grid-cols-2 gap-2">
        {sectors.map((sector, i) => {
          const isUp = sector.change > 0;
          const isDown = sector.change < 0;
          
          return (
            <motion.div
              key={i}
              initial={{ opacity: 0, scale: 0.98 }}
              animate={{ opacity: 1, scale: 1 }}
              transition={{ type: "spring", stiffness: 100, damping: 20, delay: i * 0.05 }}
              onClick={() => router.push(`/screener?sector=${encodeURIComponent(sector.name)}`)}
              className={cn(
                "p-md rounded-xl flex flex-col justify-between min-h-[78px] transition-all cursor-pointer border relative overflow-hidden active:scale-[0.98]",
                isUp 
                  ? "bg-secondary/5 border-secondary/15 hover:border-secondary/35 shadow-sm shadow-secondary/5" 
                  : isDown 
                    ? "bg-error/5 border-error/15 hover:border-error/35 shadow-sm shadow-error/5" 
                    : "bg-white/[0.02] border-white/10 hover:border-white/20"
              )}
            >
              {/* Left subtle colored status indicator */}
              <div className={cn(
                "absolute left-0 top-0 bottom-0 w-1",
                isUp ? "bg-secondary" : isDown ? "bg-error" : "bg-white/20"
              )} />

              <span className="text-[10px] font-bold text-on-surface-variant/80 truncate uppercase leading-none font-outfit tracking-wide pl-1.5">
                {sector.name}
              </span>
              <span className={cn(
                "text-[15px] font-extrabold font-data-mono mt-2 pl-1.5 flex items-center gap-0.5",
                isUp ? "text-secondary" : isDown ? "text-error" : "text-on-surface"
              )}>
                <span className="material-symbols-outlined text-[16px] leading-none">
                  {isUp ? 'arrow_drop_up' : isDown ? 'arrow_drop_down' : 'remove'}
                </span>
                {isUp ? '+' : ''}{sector.change.toFixed(2)}%
              </span>
            </motion.div>
          );
        })}
      </div>
      </div>
    </GlassCard>
  );
}
