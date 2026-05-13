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
    <GlassCard className={cn("flex flex-col gap-base group relative", className)} interactive={true}>
      {/* Label with subtle animation */}
      <span className="font-label-caps text-label-caps text-on-surface-variant group-hover:text-primary transition-colors duration-300">
        {label}
      </span>
      
      {/* Value with emphasis */}
      <div className="flex items-baseline gap-xs">
        <span className="font-headline-lg text-headline-lg text-on-surface group-hover:scale-[1.02] transition-transform duration-500 origin-left">
          {value}
        </span>
        {unit && (
          <span className="text-body-sm font-data-mono text-on-surface-variant uppercase">
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
            "mt-xs px-sm py-0.5 rounded-full w-fit font-data-mono text-[11px] flex items-center gap-1 border",
            isPositive ? 'text-secondary bg-secondary/10 border-secondary/20 shadow-[0_0_10px_rgba(52,211,153,0.1)]' : 
            isNegative ? 'text-error bg-error/10 border-error/20 shadow-[0_0_10px_rgba(248,113,113,0.1)]' : 
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
        <p className="mt-xs text-on-surface-variant font-body-sm text-[11px] opacity-60 leading-relaxed italic group-hover:opacity-80 transition-opacity">
          {description}
        </p>
      )}

      {/* Decorative accent icon on hover */}
      <div className="absolute top-lg right-lg opacity-0 group-hover:opacity-20 transition-all duration-500 translate-x-2 group-hover:translate-x-0">
         <span className="material-symbols-outlined text-primary text-[24px]">
           {isPositive ? 'query_stats' : 'analytics'}
         </span>
      </div>
    </GlassCard>
  );
}
