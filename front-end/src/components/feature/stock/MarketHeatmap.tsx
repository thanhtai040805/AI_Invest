"use client";

import { GlassCard } from "@/components/ui/GlassCard";
import { Badge } from "@/components/ui/Badge";
import { motion } from "framer-motion";
import { cn } from "@/lib/utils";
import { useMarketSnapshot } from "@/hooks/useMarketData";
import { useStockStore } from "@/stores/useStockStore";

export function MarketHeatmap() {
  const { data: stocks } = useMarketSnapshot();
  const stockStore = useStockStore((s) => s.stocks);

  const displayStocks = stocks && stocks.length > 0 ? stocks : stockStore;

  const sectorMap = displayStocks.reduce((acc, stock) => {
    const sector = stock.industry || 'Other';
    if (!acc[sector]) {
      acc[sector] = { totalChange: 0, count: 0, stocks: [] };
    }
    acc[sector].totalChange += stock.changePercent || 0;
    acc[sector].count += 1;
    acc[sector].stocks.push(stock);
    return acc;
  }, {} as Record<string, { totalChange: number; count: number; stocks: typeof displayStocks }>);

  const sectors = Object.entries(sectorMap)
    .map(([name, data]) => ({
      name,
      value: data.count > 0 ? (data.totalChange / data.count) : 0,
      trend: data.totalChange > 0 ? 'up' : data.totalChange < 0 ? 'down' : 'neutral' as 'up' | 'down' | 'neutral',
      span: 'col-span-6 md:col-span-4',
    }))
    .sort((a, b) => Math.abs(b.value) - Math.abs(a.value))
    .slice(0, 6);

  return (
    <GlassCard className="p-xl border-white/5 shadow-2xl overflow-hidden relative" interactive={false}>
      <div className="flex justify-between items-center mb-xl">
        <div className="flex items-center gap-sm">
           <div className="w-1.5 h-6 bg-primary rounded-full shadow-[0_0_10px_rgba(173,198,255,0.6)]" />
           <h3 className="font-headline-sm text-[20px] text-on-surface">Sector Heatmap</h3>
        </div>
        <div className="flex gap-md">
           <div className="flex items-center gap-1 text-[10px] text-on-surface-variant font-label-caps">
              <span className="w-2 h-2 rounded-full bg-secondary"></span>
              BULLISH
           </div>
           <div className="flex items-center gap-1 text-[10px] text-on-surface-variant font-label-caps">
              <span className="w-2 h-2 rounded-full bg-error"></span>
              BEARISH
           </div>
        </div>
      </div>

      <div className="grid grid-cols-12 auto-rows-[100px] gap-md">
        {sectors.map((sector, i) => (
          <motion.div 
            key={sector.name}
            initial={{ opacity: 0, scale: 0.9 }}
            animate={{ opacity: 1, scale: 1 }}
            transition={{ delay: i * 0.05 }}
            whileHover={{ y: -4, boxShadow: "0 20px 40px rgba(0,0,0,0.4)" }}
            className={cn(
              sector.span,
              "rounded-2xl p-xl flex flex-col justify-between transition-all cursor-pointer border relative group overflow-hidden",
              sector.trend === 'up' ? "bg-secondary/10 border-secondary/20 hover:bg-secondary/20" :
              sector.trend === 'down' ? "bg-error/10 border-error/20 hover:bg-error/20" :
              "bg-white/5 border-white/10 hover:bg-white/10"
            )}
          >
            {/* Background Accent */}
            <div className={cn(
              "absolute -bottom-6 -right-6 w-16 h-16 blur-2xl opacity-20 transition-all duration-500 group-hover:scale-150 group-hover:opacity-40",
              sector.trend === 'up' ? "bg-secondary" : sector.trend === 'down' ? "bg-error" : "bg-white"
            )} />

            <div className="flex justify-between items-start relative z-10">
              <span className="font-label-caps text-[10px] tracking-[0.2em] text-on-surface-variant opacity-60 group-hover:opacity-100 group-hover:text-on-surface transition-all">
                {sector.name}
              </span>
              <span className="material-symbols-outlined text-[16px] opacity-20 group-hover:opacity-100 group-hover:text-on-surface transition-all">
                {sector.trend === 'up' ? 'trending_up' : sector.trend === 'down' ? 'trending_down' : 'horizontal_rule'}
              </span>
            </div>

            <div className="relative z-10 flex items-baseline gap-1">
              <span className={cn(
                "font-data-mono text-headline-lg",
                sector.trend === 'up' ? "text-secondary" : sector.trend === 'down' ? "text-error" : "text-on-surface"
              )}>
                {sector.value > 0 ? '+' : ''}{sector.value.toFixed(2)}%
              </span>
            </div>
          </motion.div>
        ))}
      </div>
    </GlassCard>
  );
}
