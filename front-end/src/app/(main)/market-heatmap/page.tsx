"use client";

import { useState } from "react";
import { motion } from "framer-motion";
import { PageHeader } from "@/components/layout/PageHeader";
import { GlassCard } from "@/components/ui/GlassCard";
import { Badge } from "@/components/ui/Badge";
import { MarketTreemap, SectorFlowMatrix, LiquidityChart, MarketBubbleMap } from "@/components/feature/stock/HeatmapExploration";
import { cn } from "@/lib/utils";

const SECTORS = ['All', 'Banking', 'Real Estate', 'Steel & Resources', 'Consumer & Retail', 'Energy & Chemicals', 'Technology'];

export default function Page() {
   const [viewMode, setViewMode] = useState<'market' | 'bubble' | 'sector' | 'foreign'>('market');
   const [selectedSector, setSelectedSector] = useState('All');

   return (
      <motion.div
         initial={{ opacity: 0, y: 15 }}
         animate={{ opacity: 1, y: 0 }}
         transition={{ duration: 0.4, ease: "easeOut" }}
         className="space-y-lg pb-xl"
      >
         <PageHeader
            title="Bản Đồ Dòng Tiền"
            subtitle="Phân tích tương quan vốn hóa và biến động dòng tiền toàn thị trường."
            extra={
               <div className="flex bg-white/5 p-1 rounded-xl border border-white/10">
                  {[
                     { id: 'market', label: 'Treemap', icon: 'grid_view' },
                     { id: 'bubble', label: 'Bubble', icon: 'bubble_chart' },
                     { id: 'sector', label: 'Flow', icon: 'analytics' },
                     { id: 'foreign', label: 'Foreign', icon: 'public' },
                  ].map((mode) => (
                     <button
                        key={mode.id}
                        onClick={() => setViewMode(mode.id as any)}
                        className={cn(
                           "flex items-center gap-xs px-md py-1.5 rounded-lg text-[10px] font-bold tracking-widest transition-all",
                           viewMode === mode.id
                              ? "bg-primary text-on-primary shadow-lg"
                              : "text-on-surface-variant hover:text-on-surface"
                        )}
                     >
                        <span className="material-symbols-outlined text-[16px]">{mode.icon}</span>
                        <span className="hidden md:inline">{mode.label}</span>
                     </button>
                  ))}
               </div>
            }
         />

         {/* ... (Market Vital Bar remains same) ... */}

         <div className="grid grid-cols-1 lg:grid-cols-12 gap-lg">
            {/* Main View Area */}
            <div className="lg:col-span-9 flex flex-col gap-lg">
               {/* Color Legend & Sector Filter Bar */}
               <div className="flex flex-wrap items-center justify-between gap-md p-sm px-md bg-white/[0.02] border border-white/5 rounded-xl">
                  <div className="flex items-center gap-lg">
                     <div className="flex items-center gap-md pr-lg border-r border-white/10">
                        <span className="text-[9px] font-bold opacity-40 uppercase">Biến động:</span>
                        <div className="flex items-center gap-xs"><div className="w-2.5 h-2.5 rounded bg-[#991b1b]" /><span className="text-[9px] opacity-60">{"< -2%"}</span></div>
                        <div className="flex items-center gap-xs"><div className="w-2.5 h-2.5 rounded bg-[#6b7280]" /><span className="text-[9px] opacity-60">0%</span></div>
                        <div className="flex items-center gap-xs"><div className="w-2.5 h-2.5 rounded bg-[#10b981]" /><span className="text-[9px] opacity-60">{"> +2%"}</span></div>
                     </div>

                     <div className="flex items-center gap-sm">
                        <span className="text-[9px] font-bold opacity-40 uppercase">Lọc ngành:</span>
                        <select
                           value={selectedSector}
                           onChange={(e) => setSelectedSector(e.target.value)}
                           className="bg-transparent text-[10px] font-bold border-none focus:ring-0 cursor-pointer text-primary"
                        >
                           {SECTORS.map(s => <option key={s} value={s} className="bg-surface">{s}</option>)}
                        </select>
                     </div>
                  </div>
                  <Badge variant="outline">HOSE | LIVE DATA</Badge>
               </div>

               <GlassCard className="min-h-[600px] flex flex-col p-0 overflow-hidden">
                  <div className="flex justify-between items-center p-lg border-b border-white/5 bg-white/[0.01]">
                     <div className="flex items-center gap-sm">
                        <div className="w-1.5 h-6 bg-primary rounded-full shadow-[0_0_10px_rgba(173,198,255,0.4)]" />
                        <h3 className="font-title-md tracking-tight uppercase">
                           {viewMode === 'market' && `HOSE MARKET CAP HEATMAP - ${selectedSector}`}
                           {viewMode === 'bubble' && `VOLATILITY BUBBLE MAP - ${selectedSector}`}
                           {viewMode === 'sector' && "NET CAPITAL FLOW BY SECTOR"}
                           {viewMode === 'foreign' && "FOREIGN TRADING HEATMAP"}
                        </h3>
                     </div>
                  </div>

                  <div className="flex-1 p-lg">
                     {viewMode === 'market' && <MarketTreemap sector={selectedSector} />}
                     {viewMode === 'bubble' && <MarketBubbleMap sector={selectedSector} />}
                     {viewMode === 'sector' && <SectorFlowMatrix />}
                     {viewMode === 'foreign' && (
                        <div className="h-full flex flex-col items-center justify-center text-center p-xl opacity-40">
                           <span className="material-symbols-outlined text-[64px] mb-lg">public</span>
                           <p className="font-title-md">Foreign Flow Data...</p>
                           <p className="text-sm italic">Hệ thống đang đồng bộ dữ liệu giao dịch từ 24hMoney.</p>
                        </div>
                     )}
                  </div>
               </GlassCard>
            </div>

            {/* Side Analysis: Market Health */}
            <div className="lg:col-span-3 space-y-lg">
               <GlassCard className="flex flex-col gap-md">
                  <h3 className="font-label-caps text-[10px] tracking-widest opacity-60 uppercase">Market Liquidity</h3>
                  <div className="flex items-baseline gap-xs">
                     <span className="text-2xl font-bold font-data-mono">21.4T</span>
                     <span className="text-[10px] text-secondary font-bold">▲ 15.2%</span>
                  </div>
                  <p className="text-[9px] text-on-surface-variant opacity-60">Thanh khoản cao hơn trung bình 20 phiên.</p>
                  <LiquidityChart />
               </GlassCard>

               <GlassCard className="flex flex-col gap-lg">
                  <h3 className="font-label-caps text-[10px] tracking-widest opacity-60 uppercase">Top Capital Inflow</h3>
                  <div className="space-y-sm">
                     {[
                        { s: 'FPT', flow: '+124B', color: 'text-secondary' },
                        { s: 'TCB', flow: '+85B', color: 'text-secondary' },
                        { s: 'HPG', flow: '+42B', color: 'text-secondary' },
                        { s: 'VNM', flow: '-15B', color: 'text-error' },
                     ].map((item, i) => (
                        <div key={i} className="flex justify-between items-center p-sm rounded-lg bg-white/[0.02] border border-white/5">
                           <span className="font-bold text-xs">{item.s}</span>
                           <span className={cn("font-data-mono text-[11px] font-bold", item.color)}>{item.flow}</span>
                        </div>
                     ))}
                  </div>
               </GlassCard>
            </div>
         </div>
      </motion.div>
   );
}
