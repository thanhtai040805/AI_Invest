"use client";

import { GlassCard } from "@/components/ui/GlassCard";
import { useMarketStore } from "@/stores/useMarketStore";

export function LiquidityComparison() {
  const liquidity = useMarketStore((state) => state.liquidity);
  const maxVal = Math.max(...liquidity.map(d => Math.max(d.today, d.yesterday)));

  return (
    <GlassCard className="p-xl border-white/5">
      <h3 className="font-label-caps text-[10px] tracking-widest opacity-60 mb-xl uppercase">Thanh khoản (Tỷ đồng)</h3>
      
      <div className="flex items-end justify-between h-32 gap-1.5 px-1">
        {liquidity.map((data, i) => (
          <div key={i} className="flex-1 flex flex-col justify-end gap-0.5 h-full group relative">
            <div 
              className="w-full bg-white/10 rounded-t-sm transition-all duration-500 group-hover:bg-white/20" 
              style={{ height: `${(data.yesterday / maxVal) * 100}%` }}
            />
            <div 
              className="w-full bg-primary/40 rounded-t-sm transition-all duration-700 delay-100 group-hover:bg-primary/60 border-t border-primary/50 shadow-[0_0_15px_rgba(var(--primary-rgb),0.1)]" 
              style={{ height: `${(data.today / maxVal) * 100}%` }}
            />
            
            {/* Tooltip on hover */}
            <div className="absolute bottom-full mb-2 left-1/2 -translate-x-1/2 opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none z-30">
               <div className="bg-[#151515] border border-white/10 rounded-lg p-2 shadow-2xl text-[9px] whitespace-nowrap min-w-[80px]">
                  <div className="flex justify-between gap-md mb-1">
                     <span className="opacity-40">Hôm nay:</span>
                     <span className="font-bold text-primary">{data.today.toLocaleString()}B</span>
                  </div>
                  <div className="flex justify-between gap-md">
                     <span className="opacity-40">Hôm qua:</span>
                     <span className="font-bold text-on-surface">{data.yesterday.toLocaleString()}B</span>
                  </div>
               </div>
            </div>
          </div>
        ))}
      </div>
      
      <div className="flex justify-between mt-md px-1 opacity-20 text-[8px] font-bold tracking-widest">
        <span>{liquidity[0].time}</span>
        <span>{liquidity[Math.floor(liquidity.length / 2)].time}</span>
        <span>{liquidity[liquidity.length - 1].time}</span>
      </div>

      <div className="flex gap-lg mt-xl pt-lg border-t border-white/5">
        <div className="flex items-center gap-2">
           <div className="w-2 h-2 rounded-full bg-primary/40" />
           <span className="text-[9px] opacity-40 font-bold uppercase">Hôm nay</span>
        </div>
        <div className="flex items-center gap-2">
           <div className="w-2 h-2 rounded-full bg-white/10" />
           <span className="text-[9px] opacity-40 font-bold uppercase">Hôm qua</span>
        </div>
      </div>
    </GlassCard>
  );
}
