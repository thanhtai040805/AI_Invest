"use client";

import { motion } from "framer-motion";
import { PageHeader } from "@/components/layout/PageHeader";
import { GlassCard } from "@/components/ui/GlassCard";
import { Badge } from "@/components/ui/Badge";
import { cn } from "@/lib/utils";
import { 
  PerformanceChart, 
  AllocationPie, 
  RiskRadar, 
  HoldingsTable 
} from "@/components/feature/stock/SimulatorAnalysis";

export default function Page() {
  return (
    <motion.div 
      initial={{ opacity: 0, y: 15 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.4, ease: "easeOut" }}
      className="space-y-xl pb-xl"
    >
      <PageHeader 
        title="Portfolio Analytics" 
        subtitle="Phân tích sâu hiệu suất danh mục giả lập và quản trị rủi ro AI."
        extra={
          <div className="flex items-center gap-md">
             <Badge variant="primary" dot>Real-time Analysis</Badge>
             <button className="p-sm bg-white/5 hover:bg-white/10 border border-white/5 rounded-lg transition-all">
                <span className="material-symbols-outlined text-[20px]">file_download</span>
             </button>
          </div>
        }
      />

      {/* Top Layer: Critical Stats */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-lg">
        {[
          { label: 'NET WORTH', value: '1.452.000.000', sub: 'VND', trend: '+12.4%', color: 'text-primary' },
          { label: 'TOTAL PROFIT', value: '+142.500.000', sub: 'VND', trend: '+8.2%', color: 'text-secondary' },
          { label: 'DAILY CHANGE', value: '+24.150.000', sub: 'VND', trend: '+1.8%', color: 'text-secondary' },
          { label: 'BUYING POWER', value: '450.000.000', sub: 'VND', trend: 'Cash', color: 'text-on-surface' },
        ].map((stat, i) => (
          <GlassCard key={i} className="group relative overflow-hidden">
            <div className="absolute top-0 right-0 p-lg opacity-10 group-hover:opacity-20 transition-opacity">
               <span className="material-symbols-outlined text-[48px]">monitoring</span>
            </div>
            <p className="font-label-caps text-[10px] text-on-surface-variant tracking-[0.2em] mb-sm">{stat.label}</p>
            <div className="flex items-baseline gap-xs">
               <span className={cn("font-display-sm text-headline-md tracking-tight", stat.color)}>{stat.value}</span>
               <span className="text-[10px] opacity-40 font-bold">{stat.sub}</span>
            </div>
            <div className="mt-md flex items-center gap-sm">
               <span className={cn("text-[10px] font-bold px-2 py-0.5 rounded-full", stat.trend.startsWith('+') ? "bg-secondary/10 text-secondary" : "bg-on-surface/5 text-on-surface-variant")}>
                 {stat.trend}
               </span>
               <span className="text-[9px] opacity-40 uppercase font-data-mono">vs Prev Close</span>
            </div>
          </GlassCard>
        ))}
      </div>

      {/* Middle Layer: Charts & Analytics */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-lg">
        <GlassCard className="lg:col-span-8">
           <div className="flex items-center justify-between mb-lg">
              <div>
                <h3 className="font-title-lg">Growth Performance</h3>
                <p className="text-[11px] text-on-surface-variant">So sánh tăng trưởng tài sản với VN-Index</p>
              </div>
              <div className="flex gap-md">
                 <div className="flex items-center gap-xs font-label-caps text-[9px] text-primary">
                    <div className="w-2 h-2 rounded-full bg-primary" /> PORTFOLIO
                 </div>
                 <div className="flex items-center gap-xs font-label-caps text-[9px] opacity-40">
                    <div className="w-2 h-2 rounded-full bg-white/40" /> VN-INDEX
                 </div>
              </div>
           </div>
           <PerformanceChart />
        </GlassCard>

        <div className="lg:col-span-4 flex flex-col gap-lg">
           <GlassCard className="flex-1">
              <h3 className="font-title-md mb-md">Risk Radar</h3>
              <p className="text-[11px] text-on-surface-variant mb-lg">Đánh giá kỹ năng đầu tư từ AI</p>
              <RiskRadar />
           </GlassCard>
        </div>
      </div>

      {/* Bottom Layer: Allocation & Holdings */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-lg">
         <GlassCard className="lg:col-span-4">
            <h3 className="font-title-md mb-lg">Asset Allocation</h3>
            <AllocationPie />
            <div className="mt-lg space-y-sm">
               {['FPT', 'VNM', 'HPG', 'TCB'].map((s, i) => (
                  <div key={i} className="flex justify-between items-center text-[11px]">
                     <span className="opacity-60">{s}</span>
                     <span className="font-data-mono font-bold">{(40 - i * 5)}%</span>
                  </div>
               ))}
            </div>
         </GlassCard>

         <GlassCard className="lg:col-span-8 overflow-hidden">
            <div className="flex justify-between items-center mb-xl">
               <div>
                  <h3 className="font-title-md">Holdings Details</h3>
                  <p className="text-[11px] text-on-surface-variant">Chi tiết lãi lỗ từng vị thế đang nắm giữ</p>
               </div>
               <Badge variant="outline">3 ACTIVE POSITIONS</Badge>
            </div>
            <HoldingsTable />
         </GlassCard>
      </div>
    </motion.div>
  );
}
