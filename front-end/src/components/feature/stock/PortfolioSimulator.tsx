"use client";

import { GlassCard } from "@/components/ui/GlassCard";
import { Badge } from "@/components/ui/Badge";
import { motion } from "framer-motion";
import { cn } from "@/lib/utils";

export function PortfolioSimulator() {
  return (
    <GlassCard className="flex flex-col gap-xl border-white/5 shadow-2xl relative overflow-hidden" interactive={false}>
      {/* Background Decorative Element */}
      <div className="absolute -top-24 -right-24 w-48 h-48 bg-primary/10 blur-[80px] rounded-full pointer-events-none" />

      <div>
        <div className="flex items-center justify-between mb-xl">
          <h3 className="font-headline-sm text-[20px] text-on-surface">Virtual Portfolio</h3>
          <Badge variant="primary" dot>Live Sync</Badge>
        </div>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-md">
          <div className="p-xl rounded-2xl bg-white/[0.02] border border-white/5 group hover:border-primary/20 transition-all duration-500">
            <div className="flex items-center gap-sm mb-md text-on-surface-variant opacity-60">
              <span className="material-symbols-outlined text-[18px]">account_balance_wallet</span>
              <span className="font-label-caps text-[10px] tracking-[0.2em]">TOTAL BALANCE</span>
            </div>
            <p className="font-data-mono text-display-sm text-on-surface group-hover:scale-[1.02] transition-transform origin-left duration-500">
              50,000<span className="text-body-sm opacity-40 ml-1">.00</span>
            </p>
            <div className="mt-md h-1 w-full bg-white/5 rounded-full overflow-hidden">
              <motion.div initial={{ width: 0 }} animate={{ width: '100%' }} className="h-full bg-primary" />
            </div>
          </div>

          <div className="p-xl rounded-2xl bg-white/[0.02] border border-white/5 group hover:border-secondary/20 transition-all duration-500">
            <div className="flex items-center gap-sm mb-md text-on-surface-variant opacity-60">
              <span className="material-symbols-outlined text-[18px]">trending_up</span>
              <span className="font-label-caps text-[10px] tracking-[0.2em]">UNREALIZED P/L</span>
            </div>
            <p className="font-data-mono text-display-sm text-secondary group-hover:scale-[1.02] transition-transform origin-left duration-500">
              +1,240<span className="text-body-sm opacity-40 ml-1">.50</span>
            </p>
            <div className="mt-md flex items-center gap-2">
              <span className="text-[10px] font-bold text-secondary bg-secondary/10 px-2 py-0.5 rounded-full">+2.48% TODAY</span>
            </div>
          </div>
        </div>
      </div>
    </GlassCard>
  );
}
