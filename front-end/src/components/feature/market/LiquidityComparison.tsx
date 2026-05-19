"use client";

import { GlassCard } from "@/components/ui/GlassCard";
import { useMarketStore } from "@/stores/useMarketStore";
import { cn } from "@/lib/utils";

export function LiquidityComparison() {
  const liquidity = useMarketStore((state) => state.liquidity);

  if (!liquidity.length) {
    return (
      <GlassCard className="p-xl border-white/5">
        <h3 className="font-label-caps text-[10px] tracking-widest opacity-60 mb-xl uppercase">
          Thanh khoản (Tỷ đồng)
        </h3>
        <p className="text-xs opacity-40">Đang tải dữ liệu thanh khoản...</p>
      </GlassCard>
    );
  }

  const maxVal = Math.max(...liquidity.map((d) => Math.max(d.today, d.yesterday)), 1);

  return (
    <GlassCard className="p-xl border-white/5">
      <div className="flex justify-between items-center mb-xl">
        <h3 className="font-label-caps text-[10px] tracking-widest opacity-60 uppercase">
          Thanh khoản (Tỷ đồng)
        </h3>
        <div className="flex items-center gap-2 text-[9px] font-bold opacity-60 uppercase">
          <div className="flex items-center gap-1">
            <span className="w-1.5 h-1.5 rounded-full bg-white/30" />
            <span>Hôm qua</span>
          </div>
          <div className="flex items-center gap-1">
            <span className="w-1.5 h-1.5 rounded-full bg-[#e8a940]" />
            <span>Hôm nay</span>
          </div>
        </div>
      </div>

      {/* Side-by-side comparative column chart */}
      <div className="flex items-end justify-between h-36 gap-3 px-1">
        {liquidity.map((data, i) => (
          <div key={i} className="flex-1 flex items-end gap-[2px] h-full group relative">
            {/* Yesterday bar */}
            <div
              className="flex-1 bg-white/10 rounded-t-sm transition-all duration-300 group-hover:bg-white/20"
              style={{ height: `${(data.yesterday / maxVal) * 100}%` }}
              title={`Hôm qua: ${data.yesterday.toLocaleString()} tỷ`}
            />
            {/* Today bar */}
            <div
              className="flex-1 bg-[#e8a940]/80 rounded-t-sm transition-all duration-300 group-hover:bg-[#e8a940]"
              style={{ height: `${(data.today / maxVal) * 100}%` }}
              title={`Hôm nay: ${data.today.toLocaleString()} tỷ`}
            />
          </div>
        ))}
      </div>

      <div className="flex justify-between mt-md px-1 opacity-40 text-[8px] font-data-mono font-bold tracking-widest">
        <span>{liquidity[0]?.time}</span>
        <span>{liquidity[Math.floor(liquidity.length / 2)]?.time}</span>
        <span>{liquidity[liquidity.length - 1]?.time}</span>
      </div>
    </GlassCard>
  );
}
