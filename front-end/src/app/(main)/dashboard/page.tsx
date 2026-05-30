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

/* Stagger variants for bento grid entrance */
const containerVariants = {
  hidden: {},
  show: {
    transition: { staggerChildren: 0.06, delayChildren: 0.05 },
  },
};

const itemVariants = {
  hidden: { opacity: 0, y: 18 },
  show:   { opacity: 1, y: 0, transition: { type: "spring" as const, stiffness: 260, damping: 26 } },
};

export default function Page() {
  const indices    = useMarketStore((s) => s.indices);
  const breadth    = useMarketStore((s) => s.breadth);
  const searchQuery    = useStockStore((s) => s.searchQuery);
  const setSearchQuery = useStockStore((s) => s.setSearchQuery);

  const total   = breadth.advancers + breadth.decliners + breadth.unchanged || 1;
  const advPct  = Math.round((breadth.advancers / total) * 100);
  const decPct  = Math.round((breadth.decliners / total) * 100);
  const unchPct = 100 - advPct - decPct;

  return (
    <DashboardDataLoader>
      <ErrorBoundary>
        <motion.div
          variants={containerVariants}
          initial="hidden"
          animate="show"
          className="min-h-full pb-10"
        >
          {/* ── Page header ── */}
          <motion.div
            variants={itemVariants}
            className="sticky top-0 z-20 flex items-center justify-between px-6 py-3.5 bg-[#08080a]/90 backdrop-blur-xl border-b border-white/[0.04]"
          >
            <div className="flex items-center gap-3">
              <div className="w-8 h-8 rounded-lg bg-[#e8a940]/8 border border-[#e8a940]/15 flex items-center justify-center">
                <span className="material-symbols-outlined text-[#e8a940] text-[17px]" style={{ fontVariationSettings: "'FILL' 1" }}>
                  space_dashboard
                </span>
              </div>
              <div>
                <h1 className="text-[15px] font-bold text-white/90 leading-none tracking-tight">Bảng điện trực tuyến</h1>
                <p className="text-[11px] text-white/35 mt-0.5">Dữ liệu cập nhật theo thời gian thực</p>
              </div>
            </div>

            <div className="flex items-center gap-3">
              {/* Inline search */}
              <div className="relative hidden md:block">
                <span className="material-symbols-outlined absolute left-2.5 top-1/2 -translate-y-1/2 text-[15px] text-white/30 pointer-events-none">
                  search
                </span>
                <input
                  type="text"
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  placeholder="Tìm mã cổ phiếu..."
                  className="bg-white/[0.04] border border-white/[0.07] rounded-lg pl-8 pr-3 py-1.5 text-[12px] focus:outline-none focus:border-[#e8a940]/40 w-44 transition-all duration-200 font-sans text-white/80 placeholder:text-white/25"
                />
              </div>
              <Badge variant="secondary" dot className="text-[9px] font-bold uppercase tracking-wider">
                Trực tiếp
              </Badge>
            </div>
          </motion.div>

          {/* ── Main content: asymmetric bento ── */}
          <div className="p-5 grid grid-cols-1 lg:grid-cols-12 gap-4">

            {/* ── Left: Market table 9 cols ── */}
            <motion.div variants={itemVariants} className="lg:col-span-9 flex flex-col gap-4">

              {/* Market breadth mini strip */}
              <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                {[
                  {
                    label: "Tăng",
                    value: breadth.advancers || "—",
                    sub: `${advPct}% mã`,
                    color: "#2dbd7e",
                    bg: "#2dbd7e",
                    icon: "trending_up",
                  },
                  {
                    label: "Giảm",
                    value: breadth.decliners || "—",
                    sub: `${decPct}% mã`,
                    color: "#f87171",
                    bg: "#f87171",
                    icon: "trending_down",
                  },
                  {
                    label: "Không đổi",
                    value: breadth.unchanged || "—",
                    sub: `${unchPct}% mã`,
                    color: "#95949c",
                    bg: "#95949c",
                    icon: "remove",
                  },
                  {
                    label: "Tổng số mã",
                    value: total === 1 ? "—" : total,
                    sub: "đang giao dịch",
                    color: "#e8a940",
                    bg: "#e8a940",
                    icon: "format_list_numbered",
                  },
                ].map((s) => (
                  <div
                    key={s.label}
                    className="glass-card rounded-xl px-4 py-3 flex items-center gap-3 border-white/[0.05] group hover:border-white/[0.08] transition-all duration-200"
                  >
                    <div
                      className="w-8 h-8 rounded-lg flex items-center justify-center shrink-0"
                      style={{ background: `${s.bg}14`, border: `1px solid ${s.bg}22` }}
                    >
                      <span
                        className="material-symbols-outlined text-[17px]"
                        style={{ color: s.color, fontVariationSettings: "'FILL' 1" }}
                      >
                        {s.icon}
                      </span>
                    </div>
                    <div className="min-w-0">
                      <p className="font-data-mono font-bold text-[18px] text-white/90 leading-none">
                        {s.value}
                      </p>
                      <p className="font-label-caps text-[9px] text-white/30 mt-0.5 tracking-[0.1em]">
                        {s.label}
                      </p>
                    </div>
                  </div>
                ))}
              </div>

              {/* Main stock table */}
              <GlassCard className="flex-1 overflow-hidden p-0 border-white/[0.05]">
                <div className="flex justify-between items-center px-5 py-3.5 border-b border-white/[0.05] bg-white/[0.01]">
                  <div className="flex items-center gap-2.5">
                    <div className="w-7 h-7 rounded-md bg-[#e8a940]/8 border border-[#e8a940]/15 flex items-center justify-center">
                      <span className="material-symbols-outlined text-[#e8a940] text-[15px]" style={{ fontVariationSettings: "'FILL' 1" }}>
                        list_alt
                      </span>
                    </div>
                    <span className="font-label-caps text-[10px] tracking-widest text-white/40 uppercase">
                      Danh mục theo dõi
                    </span>
                  </div>
                  <div className="flex items-center gap-2">
                    <span className="font-label-caps text-[8px] tracking-[0.14em] text-white/18 uppercase hidden md:block">
                      Nguồn: VNSTOCK / AIInvest
                    </span>
                    <div className="w-px h-3 bg-white/[0.06] hidden md:block" />
                    <span className="flex items-center gap-1">
                      <span className="w-1 h-1 rounded-full bg-[#2dbd7e] animate-pulse-dot" />
                      <span className="font-label-caps text-[8px] text-[#2dbd7e] tracking-[0.14em]">LIVE</span>
                    </span>
                  </div>
                </div>
                <div className="max-h-[780px] overflow-y-auto no-scrollbar">
                  <MarketTable />
                </div>
              </GlassCard>
            </motion.div>

            {/* ── Right column: 3 cols ── */}
            <motion.div variants={itemVariants} className="lg:col-span-3 flex flex-col gap-4">

              <PortfolioSummary />

              {/* Market breadth bar */}
              <GlassCard className="p-5 border-white/[0.05]">
                <p className="font-label-caps text-[9px] tracking-[0.16em] text-white/35 uppercase mb-4">
                  Độ rộng thị trường
                </p>

                {/* Segmented progress bar */}
                <div className="flex h-2 w-full rounded-full overflow-hidden gap-px bg-white/[0.04] mb-3">
                  <div
                    className="h-full rounded-l-full transition-all duration-700"
                    style={{ width: `${advPct}%`, background: "linear-gradient(90deg, #1ea866, #2dbd7e)" }}
                  />
                  <div
                    className="h-full transition-all duration-700"
                    style={{ width: `${unchPct}%`, background: "rgba(255,255,255,0.06)" }}
                  />
                  <div
                    className="h-full rounded-r-full transition-all duration-700"
                    style={{ width: `${decPct}%`, background: "linear-gradient(90deg, #f87171, #e05252)" }}
                  />
                </div>

                <div className="grid grid-cols-3 gap-1 text-center">
                  {[
                    { label: "Tăng",   val: breadth.advancers, pct: advPct,  color: "#2dbd7e" },
                    { label: "Đứng",   val: breadth.unchanged, pct: unchPct, color: "#95949c" },
                    { label: "Giảm",   val: breadth.decliners, pct: decPct,  color: "#f87171" },
                  ].map((item) => (
                    <div key={item.label}>
                      <p className="font-data-mono text-[15px] font-bold" style={{ color: item.color }}>
                        {item.val || "—"}
                      </p>
                      <p className="font-label-caps text-[8px] text-white/30 tracking-[0.1em]">
                        {item.label} · {item.pct}%
                      </p>
                    </div>
                  ))}
                </div>
              </GlassCard>

              <LiquidityComparison />
              <MarketHeatmap />
            </motion.div>
          </div>
        </motion.div>
      </ErrorBoundary>
    </DashboardDataLoader>
  );
}
