"use client";

import { GlassCard } from "@/components/ui/GlassCard";
import { useMarketStore } from "@/stores/useMarketStore";

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
      <h3 className="font-label-caps text-[10px] tracking-widest opacity-60 mb-xl uppercase">
        Thanh khoản (Tỷ đồng)
      </h3>

      <div className="flex items-end justify-between h-32 gap-1.5 px-1">
        {liquidity.map((data, i) => (
          <div key={i} className="flex-1 flex flex-col justify-end gap-0.5 h-full group relative">
            <div
              className="w-full bg-white/10 rounded-t-sm"
              style={{ height: `${(data.yesterday / maxVal) * 100}%` }}
            />
            <div
              className="w-full bg-primary/40 rounded-t-sm border-t border-primary/50"
              style={{ height: `${(data.today / maxVal) * 100}%` }}
            />
          </div>
        ))}
      </div>

      <div className="flex justify-between mt-md px-1 opacity-20 text-[8px] font-bold tracking-widest">
        <span>{liquidity[0]?.time}</span>
        <span>{liquidity[Math.floor(liquidity.length / 2)]?.time}</span>
        <span>{liquidity[liquidity.length - 1]?.time}</span>
      </div>
    </GlassCard>
  );
}
