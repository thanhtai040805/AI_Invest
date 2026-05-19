"use client";

import { motion } from "framer-motion";
import { GlassCard } from "@/components/ui/GlassCard";
import { Badge } from "@/components/ui/Badge";
import { MarketTable } from "@/components/feature/stock/MarketTable";
import { cn } from "@/lib/utils";
import { useMarketStore } from "@/stores/useMarketStore";
import { useStockStore } from "@/stores/useStockStore";
import { DashboardDataLoader } from "@/components/providers/DashboardDataLoader";
import { MarketHeatmap } from "@/components/feature/market/MarketHeatmap";
import { LiquidityComparison } from "@/components/feature/market/LiquidityComparison";
import { PortfolioSummary } from "@/components/feature/portfolio/PortfolioSummary";
import { ErrorBoundary } from "@/components/ui/ErrorBoundary";

export default function Page() {
  const indices = useMarketStore((state) => state.indices);
  const breadth = useMarketStore((state) => state.breadth);
  const searchQuery = useStockStore((state) => state.searchQuery);
  const setSearchQuery = useStockStore((state) => state.setSearchQuery);

  const totalBreadth = breadth.advancers + breadth.decliners + breadth.unchanged;
  const advPct = totalBreadth > 0 ? Math.round((breadth.advancers / totalBreadth) * 100) : 0;
  const decPct = totalBreadth > 0 ? Math.round((breadth.decliners / totalBreadth) * 100) : 0;
  const unchPct = totalBreadth > 0 ? 100 - advPct - decPct : 0;

  const displayIndices = indices.length
    ? indices
    : [{ name: "...", value: 0, change: 0, changePercent: 0, trend: "steady" as const }];

  return (
    <DashboardDataLoader>
      <ErrorBoundary>
        <motion.div
          initial={{ opacity: 0, y: 15 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.4, ease: "easeOut" }}
          className="pb-xl space-y-lg px-xl pt-lg"
        >
          {/* Header Area */}
          <div className="flex flex-col md:flex-row justify-between items-start md:items-center gap-md border-b border-white/5 pb-lg">
            <div className="flex flex-col md:flex-row items-start md:items-center gap-lg">
              <div className="flex items-center gap-md">
                <div className="w-10 h-10 rounded-xl bg-[#e8a940]/10 flex items-center justify-center text-[#e8a940] border border-[#e8a940]/20">
                  <span className="material-symbols-outlined text-[20px]">analytics</span>
                </div>
                <div>
                  <h1 className="text-2xl font-black text-[#e8a940] tracking-tighter uppercase leading-none">Bảng Điện Trực Tuyến</h1>
                  <p className="text-xs text-on-surface-variant mt-1">Dữ liệu giao dịch thời gian thực. Theo dõi biến động thị trường & quản lý danh mục chuyên nghiệp.</p>
                </div>
              </div>

              <div className="hidden lg:flex items-center gap-lg px-4 py-2 bg-white/4 border border-white/5 rounded-xl font-data-mono">
                {displayIndices.map((idx, i) => (
                  <div
                    key={i}
                    className="flex items-center gap-sm border-r last:border-0 border-white/10 pr-lg last:pr-0"
                  >
                    <span className="text-[9px] font-black opacity-45 uppercase">{idx.name}</span>
                    <span className="text-xs font-bold text-on-surface">
                      {idx.value.toLocaleString("vi-VN")}
                    </span>
                    <span
                      className={cn(
                        "text-[10px] font-black",
                        idx.trend === "up" ? "text-secondary" : "text-error",
                      )}
                    >
                      {idx.trend === "up" ? "▲" : "▼"} {idx.changePercent}%
                    </span>
                  </div>
                ))}
              </div>
            </div>
            
            <div className="flex items-center gap-md">
              <Badge variant="secondary" dot className="text-[9px] font-black uppercase tracking-wider">
                PHÁT TRỰC TIẾP
              </Badge>
            </div>
          </div>

          {/* Main Content Grid */}
          <div className="grid grid-cols-1 lg:grid-cols-12 gap-lg">
            <div className="lg:col-span-9 flex flex-col gap-lg">
              <GlassCard className="flex-1 overflow-hidden p-0 border-white/5 shadow-2xl">
                <div className="flex justify-between items-center p-xl border-b border-white/5 bg-white/[0.01]">
                  <div className="flex items-center gap-md">
                    <div className="w-8 h-8 rounded-lg bg-[#e8a940]/10 flex items-center justify-center text-[#e8a940] border border-[#e8a940]/20">
                      <span className="material-symbols-outlined text-[16px]">list_alt</span>
                    </div>
                    <h3 className="text-[10px] font-black opacity-45 uppercase tracking-widest">Danh mục Theo dõi</h3>
                  </div>
                  <div className="flex gap-md">
                    <div className="relative">
                      <span className="material-symbols-outlined absolute left-3 top-1/2 -translate-y-1/2 text-[16px] opacity-40">
                        search
                      </span>
                      <input
                        type="text"
                        value={searchQuery}
                        onChange={(e) => setSearchQuery(e.target.value)}
                        placeholder="Tìm mã cổ phiếu..."
                        className="bg-white/5 border border-white/10 rounded-xl pl-9 pr-md py-2 text-xs focus:outline-none focus:border-[#e8a940]/50 w-48 transition-all font-sans"
                      />
                    </div>
                  </div>
                </div>
                <div className="max-h-[850px] overflow-y-auto no-scrollbar">
                  <MarketTable />
                </div>
                <div className="p-xl bg-white/[0.01] border-t border-white/5 flex justify-between items-center text-[8px] opacity-40 font-data-mono font-bold tracking-widest">
                  <span>NGUỒN DỮ LIỆU: VNSTOCK / AIINVEST</span>
                  <span>LIVE CONNECTION</span>
                </div>
              </GlassCard>
            </div>

            <div className="lg:col-span-3 flex flex-col gap-lg">
              <PortfolioSummary />
              
              <GlassCard className="p-xl border-white/5">
                <h3 className="font-label-caps text-[10px] tracking-widest opacity-60 mb-xl uppercase">
                  Độ rộng thị trường
                </h3>
                <div className="space-y-md">
                  <div className="flex justify-between items-end mb-1">
                    <span className="text-[10px] text-secondary font-black font-data-mono">TĂNG: {breadth.advancers}</span>
                    <span className="text-[10px] text-on-surface-variant opacity-40 font-data-mono font-bold">{advPct}%</span>
                  </div>
                  <div className="flex h-2.5 w-full rounded-full overflow-hidden bg-white/5">
                    <div className="h-full bg-secondary" style={{ width: `${advPct}%` }} />
                    <div className="h-full bg-white/10" style={{ width: `${unchPct}%` }} />
                    <div className="h-full bg-error" style={{ width: `${decPct}%` }} />
                  </div>
                  <div className="flex justify-between items-start mt-1">
                    <span className="text-[10px] text-error font-black font-data-mono">GIẢM: {breadth.decliners}</span>
                    <span className="text-[10px] text-on-surface-variant opacity-45 font-data-mono font-bold">
                      {breadth.unchanged} không đổi
                    </span>
                  </div>
                </div>
              </GlassCard>
              
              <LiquidityComparison />
              <MarketHeatmap />
            </div>
          </div>
        </motion.div>
      </ErrorBoundary>
    </DashboardDataLoader>
  );
}
