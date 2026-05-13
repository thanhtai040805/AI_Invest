"use client";

import { GlassCard } from "@/components/ui/GlassCard";
import { Badge } from "@/components/ui/Badge";
import { motion } from "framer-motion";
import { cn } from "@/lib/utils";

export function AutoPilotStats() {
  return (
    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-md">
      {[
        { label: 'DAILY ROI', value: '+1.25%', trend: 'up', color: 'text-secondary' },
        { label: 'MAX DRAWDOWN', value: '-4.8%', trend: 'down', color: 'text-error' },
        { label: 'WIN RATE', value: '78.5%', trend: 'up', color: 'text-on-surface' },
        { label: 'VOLATILITY', value: 'LOW', trend: 'neutral', color: 'text-primary' },
      ].map((stat, i) => (
        <GlassCard key={i} className="flex flex-col gap-xs p-xl">
          <span className="font-label-caps text-[10px] text-on-surface-variant tracking-[0.2em]">{stat.label}</span>
          <span className={cn("font-headline-sm text-headline-sm", stat.color)}>{stat.value}</span>
        </GlassCard>
      ))}
    </div>
  );
}

export function RiskScore({ score }: { score: number }) {
  const strokeDasharray = 364;
  const strokeDashoffset = strokeDasharray - (strokeDasharray * (score / 10));

  return (
    <GlassCard className="flex flex-col items-center justify-center text-center p-xl h-full">
      <div className="relative w-32 h-32 mb-lg flex items-center justify-center">
        <svg className="w-full h-full transform -rotate-90 overflow-visible">
          <circle className="text-white/5" cx="64" cy="64" fill="transparent" r="58" stroke="currentColor" strokeWidth="8"></circle>
          <motion.circle 
            initial={{ strokeDashoffset: strokeDasharray }}
            animate={{ strokeDashoffset }}
            transition={{ duration: 1.5, ease: "easeOut" }}
            className="text-primary" 
            cx="64" cy="64" fill="transparent" r="58" 
            stroke="currentColor" 
            strokeDasharray={strokeDasharray}
            strokeWidth="8"
            strokeLinecap="round"
          />
        </svg>
        <div className="absolute inset-0 flex flex-col items-center justify-center">
          <p className="text-headline-lg font-headline-lg text-on-surface">{score}</p>
          <p className="font-label-caps text-[9px] text-on-surface-variant uppercase tracking-widest">Risk Score</p>
        </div>
      </div>
      <h3 className="font-title-md text-on-surface mb-xs">Balanced Strategy</h3>
      <p className="text-[11px] text-on-surface-variant leading-relaxed opacity-70">
        AI đang ưu tiên tích lũy cổ phiếu cơ bản và phòng vệ trước biến động thị trường.
      </p>
    </GlassCard>
  );
}
