"use client";

import { motion } from 'framer-motion';
import { PageHeader } from '@/components/layout/PageHeader';
import { GlassCard } from '@/components/ui/GlassCard';
import { Badge } from '@/components/ui/Badge';
import { AutoPilotStats, RiskScore, BacktestWidget, BacktestHistory } from '@/components/feature/stock/AutoPilotModules';

export default function Page() {
  return (
    <motion.div 
      initial={{ opacity: 0, y: 15 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.4, ease: "easeOut" }}
      className="space-y-xl pb-xl"
    >
      <PageHeader 
        title="Auto-Pilot" 
        subtitle="Hệ thống giao dịch tự động vận hành bởi Aura AI."
        extra={
          <div className="flex items-center gap-md">
             <Badge variant="secondary" dot>Active</Badge>
          </div>
        }
      />

      <section className="grid grid-cols-1 lg:grid-cols-12 gap-lg">
        <div className="lg:col-span-8 flex flex-col gap-lg">
          <GlassCard className="relative overflow-hidden flex flex-col justify-between min-h-[280px]">
             <div className="absolute -top-24 -right-24 w-64 h-64 bg-primary/10 blur-[100px] rounded-full" />
             <div className="relative z-10">
                <div className="flex items-center gap-sm mb-lg">
                   <span className="material-symbols-outlined text-primary">shield_with_heart</span>
                   <h2 className="font-label-caps text-label-caps text-on-surface-variant tracking-[0.2em]">PORTFOLIO OVERVIEW</h2>
                </div>
                <div className="flex flex-col md:flex-row md:items-end gap-xl">
                   <div>
                      <p className="font-label-caps text-[10px] text-on-surface-variant opacity-60 mb-2">TOTAL ASSETS MANAGED</p>
                      <p className="font-display-md text-display-md text-on-surface tracking-tight">2.485.000.000 <span className="text-title-md opacity-40">VND</span></p>
                   </div>
                   <div className="pb-2">
                      <div className="flex items-center gap-1 text-secondary font-bold">
                         <span className="material-symbols-outlined text-[18px]">trending_up</span>
                         <span>+14.2%</span>
                      </div>
                      <p className="text-[10px] text-on-surface-variant opacity-60 uppercase font-data-mono">Profit/Loss (30D)</p>
                   </div>
                </div>
             </div>
             
             <div className="mt-xl pt-xl border-t border-white/5">
                <AutoPilotStats />
             </div>
          </GlassCard>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-lg">
             <GlassCard className="flex flex-col gap-lg">
                <div className="flex justify-between items-center">
                   <h3 className="font-title-md">Strategy Insights</h3>
                   <span className="material-symbols-outlined text-primary">lightbulb</span>
                </div>
                <p className="text-body-sm text-on-surface-variant leading-relaxed italic">
                  "Chỉ số tâm lý thị trường từ 24h Money & CafeF cho thấy dòng tiền đang tập trung mạnh vào nhóm cổ phiếu Ngân hàng và Bất động sản KCN."
                </p>
                <div className="h-2 w-full bg-white/5 rounded-full overflow-hidden">
                   <motion.div initial={{ width: 0 }} animate={{ width: '92%' }} className="h-full bg-primary shadow-[0_0_10px_rgba(173,198,255,0.4)]" />
                </div>
                <div className="flex justify-between font-label-caps text-[10px] opacity-60">
                   <span>AI CONFIDENCE</span>
                   <span>92%</span>
                </div>
             </GlassCard>

             <GlassCard className="flex flex-col gap-lg">
                <h3 className="font-title-md">Active Strategies</h3>
                <div className="space-y-sm">
                   {['Banking Sector Focus', 'Blue Chip Accumulation'].map((s, i) => (
                     <div key={i} className="flex items-center justify-between p-md rounded-xl bg-white/5 border border-white/5">
                        <span className="text-sm font-medium">{s}</span>
                        <span className="material-symbols-outlined text-secondary text-sm">check_circle</span>
                     </div>
                   ))}
                </div>
             </GlassCard>
          </div>
        </div>

        <div className="lg:col-span-4 flex flex-col gap-lg">
          <RiskScore score={8.5} />
          
          <BacktestWidget />
          <BacktestHistory />

          <GlassCard className="flex-1 flex flex-col gap-lg overflow-hidden">
             <div className="flex justify-between items-center">
                <h3 className="font-title-md">Auto-Pilot Log</h3>
                <Badge variant="outline">LIVE</Badge>
             </div>
             <div className="space-y-lg overflow-y-auto pr-2 no-scrollbar">
                {[
                  { symbol: 'VNM', action: 'BUY', price: '68,200', time: '14:22' },
                  { symbol: 'HPG', action: 'HOLD', price: 'Volume Flow', time: '11:05' },
                  { symbol: 'TCB', action: 'SELL', price: '34,150', time: 'Yesterday' }
                ].map((log, i) => (
                  <div key={i} className="flex justify-between items-center group cursor-pointer">
                     <div className="flex items-center gap-md">
                        <div className="w-10 h-10 rounded-xl bg-white/5 border border-white/10 flex items-center justify-center font-data-mono font-bold text-primary group-hover:bg-primary group-hover:text-on-primary transition-all">
                           {log.symbol}
                        </div>
                        <div>
                           <p className="text-sm font-bold">{log.action} {log.symbol}</p>
                           <p className="text-[10px] text-on-surface-variant">{log.price}</p>
                        </div>
                     </div>
                     <span className="text-[10px] text-on-surface-variant font-data-mono">{log.time}</span>
                  </div>
                ))}
             </div>
             <button className="w-full py-md bg-white/5 hover:bg-white/10 border border-white/5 rounded-xl text-[11px] font-label-caps tracking-widest transition-all">
                VIEW ALL ACTIVITY
             </button>
          </GlassCard>
        </div>
      </section>
    </motion.div>
  );
}
