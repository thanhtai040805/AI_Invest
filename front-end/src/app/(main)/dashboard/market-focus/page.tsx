"use client";

import { motion } from 'framer-motion';
import { PageHeader } from '@/components/layout/PageHeader';
import { StatCard } from '@/components/ui/StatCard';
import { MarketTable } from '@/components/feature/stock/MarketTable';

export default function Page() {
  return (
    <motion.div 
      initial={{ opacity: 0, y: 15 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.4, ease: "easeOut" }}
      className="font-body-lg text-body-lg selection:bg-primary/30 min-h-screen"
    >
      <PageHeader 
        title="Market Focus" 
        subtitle="Phân tích chuyên sâu danh mục VN30 và các mã tiềm năng."
        extra={
          <div className="flex items-center gap-md">
             <div className="glass-card flex items-center px-md py-xs rounded-full border border-white/10">
                <span className="w-2 h-2 rounded-full bg-secondary mr-2 animate-pulse"></span>
                <span className="font-data-mono text-data-mono text-secondary">VN30 1,295.45 (+0.85%)</span>
             </div>
          </div>
        }
      />

      <div className="space-y-lg">
        <div className="grid grid-cols-1 md:grid-cols-3 gap-md">
           <StatCard label="VN30 INDEX" value="1,295.45" trend="+0.85%" trendType="positive" />
           <StatCard label="HNX INDEX" value="242.15" trend="+0.42%" trendType="positive" />
           <StatCard label="UPCOM" value="91.20" trend="-0.12%" trendType="negative" />
        </div>

        <MarketTable />
      </div>
    </motion.div>
  );
}
