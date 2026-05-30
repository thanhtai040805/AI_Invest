"use client";

import { motion } from "framer-motion";
import { GlassCard } from "@/components/ui/GlassCard";
import { formatCurrency } from "@/lib/market-utils";
import { usePortfolioStore } from "@/stores/usePortfolioStore";
import { usePortfolioSummary } from "@/hooks/usePortfolio";
import { cn } from "@/lib/utils";

function hasAuthToken() {
  return typeof window !== "undefined" && !!localStorage.getItem("aiinvest_access_token");
}

export function PortfolioSummary() {
  usePortfolioSummary(hasAuthToken());
  const stats = usePortfolioStore((state) => state.summary);
  const isPnLPositive = stats.dailyPnL >= 0;

  return (
    <GlassCard 
      className="p-lg relative overflow-hidden border-primary/20 bg-gradient-to-br from-surface to-surface-container-lowest shadow-2xl shadow-primary/5 hover:border-primary/40 transition-colors duration-500"
      interactive={true}
    >
      {/* Background ambient lighting */}
      <div className="absolute top-0 right-0 w-[150px] h-[150px] bg-primary/5 rounded-full blur-[60px] pointer-events-none" />

      <div className="flex justify-between items-start mb-lg relative z-10">
        <div>
          <h3 className="font-outfit text-[10px] font-extrabold tracking-widest text-on-surface-variant uppercase opacity-50">
            Tài sản ròng (NAV)
          </h3>
          <p className="text-[10px] text-primary font-bold uppercase tracking-wider mt-0.5">
            AI-Premium Account
          </p>
        </div>
        <div className="w-9 h-9 rounded-xl bg-primary/10 flex items-center justify-center text-primary border border-primary/20 shadow-inner">
          <span className="material-symbols-outlined text-[18px]">account_balance_wallet</span>
        </div>
      </div>

      <div className="space-y-md relative z-10">
        <motion.div
          initial={{ scale: 0.98, opacity: 0 }}
          animate={{ scale: 1, opacity: 1 }}
          transition={{ type: "spring", stiffness: 100, damping: 20 }}
          className="text-3xl font-extrabold font-data-mono tracking-tight text-on-surface"
        >
          {formatCurrency(stats.totalEquity)}
        </motion.div>

        <div className="flex items-center gap-md">
          <div className={cn(
            "flex items-center gap-1 text-[11px] font-extrabold px-3 py-1 rounded-lg border backdrop-blur-md transition-all duration-300 font-data-mono",
            isPnLPositive 
              ? "text-secondary bg-secondary/10 border-secondary/20 shadow-sm shadow-secondary/5" 
              : "text-error bg-error/10 border-error/20 shadow-sm shadow-error/5"
          )}>
            <span className="material-symbols-outlined text-[14px]">
              {isPnLPositive ? 'trending_up' : 'trending_down'}
            </span>
            {isPnLPositive ? '+' : ''}{stats.dailyPnLPercent.toFixed(2)}%
          </div>
          <span className="text-[10px] text-on-surface-variant font-semibold tracking-wide uppercase opacity-55">
            Hôm nay: <span className={cn("font-bold font-data-mono", isPnLPositive ? "text-secondary" : "text-error")}>
              {isPnLPositive ? '+' : ''}{formatCurrency(stats.dailyPnL)}
            </span>
          </span>
        </div>
      </div>

      <div className="mt-xl pt-lg border-t border-white/5 grid grid-cols-2 gap-lg relative z-10">
        <div>
          <p className="text-[9px] font-extrabold uppercase tracking-widest text-on-surface-variant opacity-40 mb-1.5">
            Sức mua khả dụng
          </p>
          <p className="text-base font-extrabold font-data-mono text-on-surface">
            {formatCurrency(stats.buyingPower, 'B')}
          </p>
        </div>
        <div>
          <p className="text-[9px] font-extrabold uppercase tracking-widest text-on-surface-variant opacity-40 mb-1.5">
            Danh mục nắm giữ
          </p>
          <div className="flex -space-x-1.5 select-none items-center">
            {stats.holdings.slice(0, 3).map((s, i) => (
              <div 
                key={i} 
                className="w-7 h-7 rounded-lg border border-white/10 bg-surface-container-high flex items-center justify-center text-[10px] font-black text-primary font-outfit shadow-md transition-transform hover:-translate-y-0.5"
                title={s}
              >
                {s.substring(0, 3)}
              </div>
            ))}
            {stats.holdings.length > 3 && (
              <div className="w-7 h-7 rounded-lg border border-white/10 bg-white/5 flex items-center justify-center text-[10px] font-black text-on-surface-variant font-outfit shadow-md">
                +{stats.holdings.length - 3}
              </div>
            )}
            {stats.holdings.length === 0 && (
              <span className="text-[10px] italic text-on-surface-variant/40">Trống</span>
            )}
          </div>
        </div>
      </div>
    </GlassCard>
  );
}
