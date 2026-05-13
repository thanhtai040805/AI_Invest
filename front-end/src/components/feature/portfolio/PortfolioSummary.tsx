"use client";
import { motion } from "framer-motion";
import { GlassCard } from "@/components/ui/GlassCard";
import { formatCurrency } from "@/lib/market-utils";
import { usePortfolioStore } from "@/stores/usePortfolioStore";
import { cn } from "@/lib/utils";

export function PortfolioSummary() {
  const stats = usePortfolioStore((state) => state.summary);

  return (
    <GlassCard className="p-xl border-primary/20 bg-primary/5 shadow-lg shadow-primary/5">
      <div className="flex justify-between items-start mb-xl">
        <h3 className="font-label-caps text-[10px] tracking-widest opacity-60 uppercase">Tài sản ròng (NAV)</h3>
        <div className="w-8 h-8 rounded-full bg-primary/10 flex items-center justify-center text-primary">
          <span className="material-symbols-outlined text-[18px]">account_balance_wallet</span>
        </div>
      </div>

      <div className="space-y-sm">
        <motion.div
          initial={{ scale: 0.95, opacity: 0 }}
          animate={{ scale: 1, opacity: 1 }}
          className="text-2xl font-bold font-title-md"
        >
          {formatCurrency(stats.totalEquity)}
        </motion.div>

        <div className="flex items-center gap-md">
          <div className={cn("flex items-center text-xs font-bold px-2 py-0.5 rounded-full bg-opacity-10",
            stats.dailyPnL >= 0 ? "text-secondary bg-secondary" : "text-error bg-error"
          )}>
            <span className="material-symbols-outlined text-[14px]">
              {stats.dailyPnL >= 0 ? 'trending_up' : 'trending_down'}
            </span>
            {stats.dailyPnL >= 0 ? '+' : ''}{stats.dailyPnLPercent}%
          </div>
          <span className="text-[10px] opacity-40 font-medium">Hôm nay: {formatCurrency(stats.dailyPnL)}</span>
        </div>
      </div>

      <div className="mt-xl pt-lg border-t border-white/5 grid grid-cols-2 gap-md">
        <div>
          <p className="text-[9px] uppercase tracking-tighter opacity-40 mb-1">Sức mua</p>
          <p className="text-sm font-bold font-data-mono">{formatCurrency(stats.buyingPower, 'B')}</p>
        </div>
        <div>
          <p className="text-[9px] uppercase tracking-tighter opacity-40 mb-1">Cổ phiếu</p>
          <div className="flex -space-x-2">
            {stats.holdings.slice(0, 3).map((s, i) => (
              <div key={i} className="w-6 h-6 rounded-full border-2 border-surface bg-primary/20 flex items-center justify-center text-[8px] font-bold">
                {s.substring(0, 1)}
              </div>
            ))}
            {stats.holdings.length > 3 && (
              <div className="w-6 h-6 rounded-full border-2 border-surface bg-white/5 flex items-center justify-center text-[8px] font-bold">
                +{stats.holdings.length - 3}
              </div>
            )}
          </div>
        </div>
      </div>
    </GlassCard>
  );
}
