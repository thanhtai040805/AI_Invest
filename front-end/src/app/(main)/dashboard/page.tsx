"use client";

import { motion } from "framer-motion";
import { PageHeader } from "@/components/layout/PageHeader";
import { GlassCard } from "@/components/ui/GlassCard";
import { Badge } from "@/components/ui/Badge";
import { MarketTable } from "@/components/feature/stock/MarketTable";
import { cn } from "@/lib/utils";
import { useMarketStore } from "@/stores/useMarketStore";
import { useStockStore } from "@/stores/useStockStore";
import { useUIStore } from "@/stores/useUIStore";
import { MarketHeatmap } from "@/components/feature/market/MarketHeatmap";
import { LiquidityComparison } from "@/components/feature/market/LiquidityComparison";
import { PortfolioSummary } from "@/components/feature/portfolio/PortfolioSummary";
import { Skeleton } from "boneyard-js/react";
import { ErrorBoundary } from "@/components/ui/ErrorBoundary";

export default function Page() {
   const indices = useMarketStore((state) => state.indices);
   const searchQuery = useStockStore((state) => state.searchQuery);
   const setSearchQuery = useStockStore((state) => state.setSearchQuery);
   const isLoading = useUIStore((state) => state.isLoading);

   return (
      <ErrorBoundary>
         <motion.div
            initial={{ opacity: 0, y: 15 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.4, ease: "easeOut" }}
            className="space-y-lg pb-xl"
         >
            <PageHeader
               title="Bảng Điện Trực Tuyến"
               subtitle="Dữ liệu giao dịch thời gian thực. Theo dõi biến động thị trường & quản lý danh mục chuyên nghiệp."
               extra={
                  <div className="flex items-center gap-md">
                     <Skeleton name="dashboard-indices" loading={isLoading}>
                        <div className="hidden lg:flex items-center gap-lg px-md py-sm bg-white/5 border border-white/10 rounded-xl">
                           {indices.map((idx, i) => (
                              <div key={i} className="flex items-center gap-sm border-r last:border-0 border-white/10 pr-lg last:pr-0">
                                 <span className="text-[10px] font-bold opacity-60 uppercase">{idx.name}</span>
                                 <span className="font-data-mono text-sm font-bold">
                                    {idx.value.toLocaleString("vi-VN")}
                                 </span>                         <span className={cn("text-[10px] font-bold", idx.trend === 'up' ? "text-secondary" : "text-error")}>
                                    {idx.trend === 'up' ? '▲' : '▼'} {idx.changePercent}%
                                 </span>
                              </div>
                           ))}
                        </div>
                     </Skeleton>
                     <Badge variant="secondary" dot>Phát trực tiếp</Badge>
                  </div>
               }
            />

            <div className="grid grid-cols-1 lg:grid-cols-12 gap-lg">

               {/* Main Content Area: Market Table (The "Bảng Điện") */}
               <div className="lg:col-span-9 flex flex-col gap-lg">
                  <GlassCard className="flex-1 overflow-hidden p-0 border-primary/10 shadow-2xl">
                     <div className="flex justify-between items-center p-lg border-b border-white/5 bg-white/[0.01]">
                        <div className="flex items-center gap-md">
                           <div className="w-8 h-8 rounded-lg bg-primary/20 flex items-center justify-center text-primary">
                              <span className="material-symbols-outlined text-[18px]">list_alt</span>
                           </div>
                           <h3 className="font-title-md uppercase tracking-wider">Danh mục Theo dõi</h3>
                        </div>
                        <div className="flex gap-md">
                           <div className="relative">
                              <span className="material-symbols-outlined absolute left-3 top-1/2 -translate-y-1/2 text-[16px] opacity-40">search</span>
                              <input
                                 type="text"
                                 value={searchQuery}
                                 onChange={(e) => setSearchQuery(e.target.value)}
                                 placeholder="Tìm mã cổ phiếu..."
                                 className="bg-white/5 border border-white/10 rounded-lg pl-9 pr-md py-1.5 text-xs focus:outline-none focus:border-primary/50 w-48 transition-all"
                              />
                           </div>
                           <div className="flex items-center gap-xs bg-white/5 rounded-lg p-0.5 border border-white/10">
                              {['HSX', 'HNX', 'UPC'].map(s => (
                                 <button key={s} className="px-sm py-1 text-[9px] font-bold hover:bg-white/10 rounded transition-all uppercase">{s}</button>
                              ))}
                           </div>
                        </div>
                     </div>
                     <div className="max-h-[850px] overflow-y-auto no-scrollbar">
                        <MarketTable />
                     </div>
                     <div className="p-md bg-white/[0.02] border-t border-white/5 flex justify-between items-center text-[10px] opacity-40 font-data-mono">
                        <span>NGUỒN DỮ LIỆU: FIREANT, CAFEF, VIETSTOCK</span>
                        <div className="flex gap-lg">
                           <span>VN-INDEX VOL: 842.1M</span>
                           <span>GIÁ TRỊ: 21,450.2B</span>
                        </div>
                     </div>
                  </GlassCard>
               </div>

               {/* Sidebar: Analytics & Portfolio */}
               <div className="lg:col-span-3 flex flex-col gap-lg">
                  <Skeleton name="dashboard-sidebar" loading={isLoading}>
                     <PortfolioSummary />
                     <GlassCard className="p-xl border-white/5">
                        <h3 className="font-label-caps text-[10px] tracking-widest opacity-60 mb-lg uppercase">Độ rộng thị trường</h3>
                        <div className="space-y-md">
                           <div className="flex justify-between items-end mb-1">
                              <span className="text-[10px] text-secondary font-bold">TĂNG: 245</span>
                              <span className="text-[10px] text-on-surface-variant opacity-40">62%</span>
                           </div>
                           <div className="flex h-2.5 w-full rounded-full overflow-hidden bg-white/5">
                              <div className="h-full bg-secondary shadow-[0_0_10px_rgba(var(--secondary-rgb),0.3)]" style={{ width: '62%' }} />
                              <div className="h-full bg-white/10" style={{ width: '15%' }} />
                              <div className="h-full bg-error shadow-[0_0_10px_rgba(var(--error-rgb),0.3)]" style={{ width: '23%' }} />
                           </div>
                           <div className="flex justify-between items-start mt-1">
                              <span className="text-[10px] text-error font-bold">GIẢM: 92</span>
                              <span className="text-[10px] text-on-surface-variant opacity-40 italic">Đang cập nhật...</span>
                           </div>
                        </div>
                     </GlassCard>
                     <LiquidityComparison />
                     <MarketHeatmap />
                  </Skeleton>
               </div>

            </div>
         </motion.div>
      </ErrorBoundary>
   );
}
