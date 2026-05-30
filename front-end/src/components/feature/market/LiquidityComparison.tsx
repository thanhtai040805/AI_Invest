"use client";

import { GlassCard } from "@/components/ui/GlassCard";
import { useLiquidityStore } from "@/stores/useLiquidityStore";
import { cn } from "@/lib/utils";
import { formatVolume } from "@/lib/market-utils";
import { motion } from "framer-motion";

export function LiquidityComparison() {
  const liquidity = useLiquidityStore((state) => state.data);

  if (!liquidity) {
    return (
      <GlassCard className="p-lg border-white/5 bg-surface-container-lowest/30 backdrop-blur-xl">
        <h3 className="font-outfit text-[10px] font-extrabold tracking-widest text-on-surface-variant uppercase opacity-50 mb-lg">
          Thanh khoản thị trường
        </h3>
        <div className="flex items-center gap-xs text-xs text-on-surface-variant/60 font-outfit">
          <span className="w-4 h-4 border-2 border-primary border-t-transparent rounded-full animate-spin" />
          Đang tổng hợp thanh khoản...
        </div>
      </GlassCard>
    );
  }

  const topStocks = liquidity.topByVolume?.slice(0, 8) ?? [];

  return (
    <GlassCard className="p-lg border-white/5 bg-surface-container-lowest/30 backdrop-blur-xl">
      <div className="flex justify-between items-start mb-lg">
        <div>
          <h3 className="font-outfit text-[10px] font-extrabold tracking-widest text-on-surface-variant uppercase opacity-50">
            Thanh khoản toàn sàn
          </h3>
          <div className="flex items-baseline gap-xs mt-1">
            <span className="text-2xl font-black font-data-mono text-on-surface tracking-tight">
              {liquidity.totalValueBillion.toLocaleString("vi-VN")}
            </span>
            <span className="text-[10px] font-bold text-primary uppercase font-outfit tracking-wide">Tỷ VNĐ</span>
          </div>
        </div>
        <div className="text-right flex flex-col items-end gap-1">
          <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-md text-[9px] font-bold border border-white/10 bg-white/5 text-on-surface font-outfit uppercase tracking-widest">
            <span className="w-1.5 h-1.5 rounded-full bg-secondary animate-pulse" />
            Live
          </span>
          <span className="text-[9px] font-data-mono text-on-surface-variant opacity-40">
            {liquidity.lastUpdate ? new Date(liquidity.lastUpdate).toLocaleTimeString() : '--:--:--'}
          </span>
        </div>
      </div>

      {topStocks.length > 0 && (
        <div className="space-y-sm mt-lg">
          <div className="flex justify-between items-center pb-xs border-b border-white/5">
            <span className="text-[9px] font-extrabold text-on-surface-variant uppercase tracking-widest opacity-40">
              Top KL Giao dịch
            </span>
            <span className="text-[9px] font-extrabold text-on-surface-variant uppercase tracking-widest opacity-40">
              Giá trị
            </span>
          </div>
          <div className="space-y-1.5">
            {topStocks.map((stock, i) => (
              <motion.div 
                key={stock.symbol}
                initial={{ opacity: 0, x: -5 }}
                animate={{ opacity: 1, x: 0 }}
                transition={{ type: "spring", stiffness: 100, damping: 20, delay: i * 0.03 }}
                className="flex items-center justify-between py-1.5 px-md rounded-xl hover:bg-white/[0.02] border border-transparent hover:border-white/5 transition-all duration-300 group"
              >
                <div className="flex items-center gap-2.5">
                  <span className="text-[10px] font-bold font-data-mono text-on-surface-variant/40 w-4 block">
                    {(i + 1).toString().padStart(2, '0')}
                  </span>
                  <div className="flex flex-col">
                    <span className="text-xs font-black font-outfit text-on-surface group-hover:text-primary transition-colors">
                      {stock.symbol}
                    </span>
                    <span className="text-[9px] text-on-surface-variant/60 font-sans mt-0.5">
                      KL: <span className="font-bold font-data-mono text-on-surface/80">{formatVolume(stock.volume)}</span>
                    </span>
                  </div>
                </div>
                <div className="text-right">
                  <span className="text-xs font-bold font-data-mono text-on-surface">
                    {(stock.tradingValue / 1e9).toFixed(2)}
                  </span>
                  <span className="text-[9px] text-on-surface-variant/40 font-medium ml-1 font-outfit uppercase">Tỷ</span>
                </div>
              </motion.div>
            ))}
          </div>
        </div>
      )}
    </GlassCard>
  );
}
