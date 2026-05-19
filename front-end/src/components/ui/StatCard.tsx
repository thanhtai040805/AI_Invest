"use client";

import { cn } from "@/lib/utils";
import { GlassCard } from "./GlassCard";
import { motion } from "framer-motion";

interface StatCardProps {
  label: string;
  value: string;
  unit?: string;
  trend?: string;
  trendType?: 'positive' | 'negative' | 'neutral';
  description?: string;
  className?: string;
}

export function StatCard({ 
  label, 
  value, 
  unit, 
  trend, 
  trendType = 'neutral', 
  description,
  className 
}: StatCardProps) {
  const isPositive = trendType === 'positive';
  const isNegative = trendType === 'negative';

  return (
    <GlassCard className={cn("flex flex-col gap-base group relative border-white/5", className)} interactive={true}>
      {/* Label with subtle animation */}
      <span className="font-label-caps text-[10px] font-black opacity-45 uppercase tracking-widest group-hover:text-[#e8a940] transition-colors duration-300">
        {label}
      </span>
      
      {/* Value with emphasis */}
      <div className="flex items-baseline gap-xs">
        <span className="text-3xl font-black font-data-mono text-on-surface group-hover:scale-[1.02] transition-transform duration-500 origin-left">
          {value}
        </span>
        {unit && (
          <span className="text-[10px] font-data-mono font-bold text-on-surface-variant uppercase ml-1 opacity-60">
            {unit}
          </span>
        )}
      </div>

      {/* Trend Badge */}
      {trend && (
        <motion.div 
          initial={{ opacity: 0, x: -5 }}
          animate={{ opacity: 1, x: 0 }}
          className={cn(
            "mt-xs px-2.5 py-1 rounded-lg w-fit font-data-mono text-[10px] font-bold flex items-center gap-1 border",
            isPositive ? 'text-[#2dbd7e] bg-[#2dbd7e]/10 border-[#2dbd7e]/20' : 
            isNegative ? 'text-[#f87171] bg-[#f87171]/10 border-[#f87171]/20' : 
            'text-on-surface-variant bg-white/5 border-white/10'
          )}
        >
          <span className="material-symbols-outlined text-[14px]">
            {isPositive ? 'trending_up' : isNegative ? 'trending_down' : 'remove'}
          </span>
          {trend}
        </motion.div>
      )}

      {/* Description */}
      {description && (
        <p className="mt-xs text-on-surface-variant text-[11px] opacity-60 leading-relaxed italic group-hover:opacity-80 transition-opacity">
          {description}
        </p>
      )}

      {/* Decorative accent icon on hover */}
      <div className="absolute top-lg right-lg opacity-0 group-hover:opacity-25 transition-all duration-500 translate-x-2 group-hover:translate-x-0 pointer-events-none">
        <span className="material-symbols-outlined text-[#e8a940] text-[24px]">
          {isPositive ? 'query_stats' : 'analytics'}
        </span>
      </div>
    </GlassCard>
  );
}
