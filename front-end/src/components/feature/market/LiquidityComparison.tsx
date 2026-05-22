"use client";

import { GlassCard } from "@/components/ui/GlassCard";
import { useLiquidityStore } from "@/stores/useLiquidityStore";
import { cn } from "@/lib/utils";
import { formatVolume } from "@/lib/market-utils";

export function LiquidityComparison() {
  const liquidity = useLiquidityStore((state) => state.data);

  if (!liquidity) {
    return (
      <GlassCard className="p-xl border-white/5">
        <h3 className="font-label-caps text-[10px] tracking-widest opacity-60 mb-xl uppercase">
          Thanh khoản (Tỷ đồng)
        </h3>
        <p className="text-xs opacity-40">Đang tải dữ liệu thanh khoản...</p>
      </GlassCard>
    );
  }

  const topStocks = liquidity.topByVolume?.slice(0, 8) ?? [];

  return (
    <GlassCard className="p-xl border-white/5">
      <div className="flex justify-between items-center mb-xl">
        <div>
          <h3 className="font-label-caps text-[10px] tracking-widest opacity-60 uppercase">
            Thanh khoản
          </h3>
          <p className="text-lg font-bold font-data-mono mt-1">
            {liquidity.totalValueBillion.toLocaleString()} <span className="text-[10px] opacity-40">tỷ đồng</span>
          </p>
        </div>
        <div className="text-right">
          <p className="text-[9px] opacity-40 uppercase">{liquidity.stockCount} mã giao dịch</p>
          <p className="text-[9px] opacity-30 font-data-mono">{liquidity.lastUpdate ? new Date(liquidity.lastUpdate).toLocaleTimeString() : '--:--:--'}</p>
        </div>
      </div>

      {topStocks.length > 0 && (
        <div className="space-y-2">
          <p className="text-[9px] font-bold opacity-30 uppercase tracking-widest">Top KL giao dịch</p>
          {topStocks.map((stock, i) => (
            <div key={stock.symbol} className="flex items-center justify-between py-1 border-b border-white/5 last:border-0">
              <div className="flex items-center gap-2">
                <span className="text-[9px] opacity-20 font-data-mono w-4">{i + 1}</span>
                <span className="text-[11px] font-bold font-data-mono">{stock.symbol}</span>
              </div>
              <div className="flex items-center gap-4 text-[10px] font-data-mono">
                <span className="opacity-60">{formatVolume(stock.volume)}</span>
                <span className="text-on-surface-variant w-20 text-right">
                  {(stock.tradingValue / 1e9).toFixed(1)} tỷ
                </span>
              </div>
            </div>
          ))}
        </div>
      )}
    </GlassCard>
  );
}
