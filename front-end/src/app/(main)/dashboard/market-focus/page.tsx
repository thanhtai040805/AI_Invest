"use client";

import { motion } from 'framer-motion';
import { StatCard } from '@/components/ui/StatCard';
import { MarketTable } from '@/components/feature/stock/MarketTable';

export default function Page() {
  return (
    <motion.div 
      initial={{ opacity: 0, y: 15 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.4, ease: "easeOut" }}
      className="pb-xl space-y-lg px-xl pt-lg"
    >
      {/* Header section */}
      <div className="flex flex-col md:flex-row justify-between items-start md:items-center gap-md border-b border-white/5 pb-lg">
        <div className="flex items-center gap-md">
          <div className="w-10 h-10 rounded-xl bg-[#e8a940]/10 flex items-center justify-center text-[#e8a940] border border-[#e8a940]/20">
            <span className="material-symbols-outlined text-[20px]">leaderboard</span>
          </div>
          <div>
            <h1 className="text-2xl font-black text-[#e8a940] tracking-tighter uppercase leading-none">Market Focus</h1>
            <p className="text-xs text-on-surface-variant mt-1">Phân tích chuyên sâu danh mục VN30 và các mã cổ phiếu tiềm năng.</p>
          </div>
        </div>
        <div className="flex items-center gap-md">
          <div className="bg-white/4 border border-white/5 flex items-center px-4 py-2 rounded-xl">
            <span className="w-1.5 h-1.5 rounded-full bg-[#2dbd7e] mr-2 animate-pulse" />
            <span className="font-data-mono text-xs font-bold text-[#2dbd7e]">VN30 1,295.45 (+0.85%)</span>
          </div>
        </div>
      </div>

      <div className="space-y-lg">
        {/* Asymmetrical Stats Grids */}
        <div className="grid grid-cols-1 md:grid-cols-3 gap-lg">
          <StatCard label="VN30 INDEX" value="1,295.45" trend="+0.85%" trendType="positive" description="Chỉ số 30 cổ phiếu hàng đầu HSX" />
          <StatCard label="HNX INDEX" value="242.15" trend="+0.42%" trendType="positive" description="Chỉ số sàn Hà Nội" />
          <StatCard label="UPCOM INDEX" value="91.20" trend="-0.12%" trendType="negative" description="Thị trường công ty đại chúng chưa niêm yết" />
        </div>

        {/* VNSTOCK live market data board */}
        <MarketTable />
      </div>
    </motion.div>
  );
}
