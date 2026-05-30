"use client";

import { useMarketStore } from "@/stores/useMarketStore";
import { cn } from "@/lib/utils";

/**
 * MarketTickerBar — slim top bar showing live indices.
 * Isolated Client Component so the server layout stays static.
 */
export default function MarketTickerBar() {
  const indices = useMarketStore((s) => s.indices);

  const displayIndices = indices.length
    ? indices
    : [
        { name: "VN-INDEX", value: 1287.43, changePercent: 0.97,  trend: "up"     as const },
        { name: "VN30",     value: 1301.18, changePercent: 0.76,  trend: "up"     as const },
        { name: "HNX",      value: 231.07,  changePercent: -0.53, trend: "down"   as const },
        { name: "UPCOM",    value: 95.82,   changePercent: 0.12,  trend: "steady" as const },
      ];

  return (
    <div className="h-8 border-b border-white/[0.045] bg-[#060608] flex items-center shrink-0 z-40 relative">
      {/* LIVE badge */}
      <div className="flex-shrink-0 h-full px-4 flex items-center gap-1.5 border-r border-white/[0.05] bg-[#060608]">
        <span className="w-1.5 h-1.5 rounded-full bg-[#2dbd7e] animate-pulse-dot" />
        <span className="font-label-caps text-[#2dbd7e] text-[9px] tracking-[0.18em]">LIVE</span>
      </div>

      {/* Indices row */}
      <div className="flex items-center h-full overflow-x-auto no-scrollbar">
        {displayIndices.map((idx, i) => (
          <div
            key={idx.name}
            className={cn(
              "h-full flex items-center gap-2.5 px-4 border-r border-white/[0.04] shrink-0",
              "hover:bg-white/[0.02] transition-colors duration-150 cursor-default"
            )}
          >
            <span className="font-label-caps text-[9px] text-white/35 tracking-[0.1em]">{idx.name}</span>
            <span className="font-data-mono text-[11px] font-bold text-white/80">
              {idx.value.toLocaleString("vi-VN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
            </span>
            <span
              className={cn(
                "font-data-mono text-[10px] font-bold",
                idx.trend === "up"     ? "text-[#2dbd7e]" :
                idx.trend === "down"   ? "text-[#f87171]" :
                "text-white/35"
              )}
            >
              {idx.trend === "up" ? "▲" : idx.trend === "down" ? "▼" : "◆"}{" "}
              {Math.abs(idx.changePercent).toFixed(2)}%
            </span>
          </div>
        ))}
      </div>

      {/* Right edge: market status */}
      <div className="ml-auto flex-shrink-0 px-4 flex items-center gap-2">
        <span className="font-label-caps text-[9px] text-white/25 tracking-[0.12em]">
          HOSE · HNX · UPCOM
        </span>
      </div>
    </div>
  );
}
