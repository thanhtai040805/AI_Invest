"use client";

import dynamic from "next/dynamic";
import { motion } from 'framer-motion';
import { useRouter, useSearchParams } from 'next/navigation';
import { Suspense } from 'react';
import '@klinecharts/pro/dist/klinecharts-pro.css'

const PriceChart = dynamic(
  () => import('@/components/feature/stock/PriceChart'),
  {
    ssr: false,
    loading: () => <div className="w-full h-full bg-[#18181B] animate-pulse rounded-[2rem]" />
  }
);

function AdvancedChartContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const symbol = searchParams.get('symbol') || 'HPG';

  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center p-4 sm:p-8 md:p-12 bg-[#050505]/60 backdrop-blur-md">
      {/* Click outside to close (optional, but good UX) */}
      <div className="absolute inset-0 cursor-pointer" onClick={() => router.back()} />
      
      <motion.div
        initial={{ opacity: 0, scale: 0.95, y: 20 }}
        animate={{ opacity: 1, scale: 1, y: 0 }}
        exit={{ opacity: 0, scale: 0.95, y: 20 }}
        transition={{ type: "spring", stiffness: 100, damping: 20 }}
        className="w-full max-w-[1400px] h-[85vh] bg-[#0a0a0a]/90 backdrop-blur-2xl rounded-[2.5rem] border border-white/10 shadow-[inset_0_1px_0_rgba(255,255,255,0.05)] shadow-[0_40px_80px_-20px_rgba(0,0,0,0.8)] overflow-hidden flex flex-col relative z-10"
      >
        {/* PREMIUM HEADER */}
        <div className="flex-none px-10 py-6 flex justify-between items-center border-b border-white/5 bg-white/[0.01]">
          <div className="flex items-center gap-8">
            <div>
              <div className="flex items-baseline gap-3">
                <h2 className="text-3xl font-bold tracking-tight text-white uppercase">{symbol}<span className="text-sm text-white/40 font-medium ml-1">HOSE</span></h2>
                <span className="border border-secondary/20 text-secondary bg-secondary/5 font-data-mono px-2 py-0.5 rounded text-sm">+1.25%</span>
              </div>
              <p className="text-xs text-white/40 uppercase tracking-widest mt-1">Company Name</p>
            </div>
            
            <div className="h-10 w-px bg-white/10 mx-2"></div>
            
            <div className="flex gap-8">
              <div>
                <p className="text-[10px] text-white/40 font-bold uppercase tracking-widest mb-1">LAST PRICE</p>
                <p className="font-data-mono text-white text-lg font-medium">28.450</p>
              </div>
              <div>
                <p className="text-[10px] text-white/40 font-bold uppercase tracking-widest mb-1">VOLUME</p>
                <p className="font-data-mono text-white text-lg font-medium">12.4M</p>
              </div>
            </div>
          </div>
          
          <div className="flex items-center gap-4">
            <div className="flex gap-3 mr-4">
              <button className="px-6 py-2.5 rounded-full bg-secondary text-white font-bold text-sm shadow-[0_0_20px_-5px_rgba(16,185,129,0.4)] active:scale-[0.98] transition-all">BUY</button>
              <button className="px-6 py-2.5 rounded-full bg-error text-white font-bold text-sm shadow-[0_0_20px_-5px_rgba(239,68,68,0.4)] active:scale-[0.98] transition-all">SELL</button>
            </div>
            
            {/* Close Button */}
            <button 
              onClick={() => router.back()}
              className="w-10 h-10 rounded-full bg-white/5 border border-white/10 flex items-center justify-center text-white/60 hover:text-white hover:bg-white/10 active:scale-95 transition-all"
            >
              <span className="text-xl">✕</span>
            </button>
          </div>
        </div>

        {/* CHART BODY */}
        <div className="flex-1 relative bg-[#050505] p-2">
          <PriceChart symbol={symbol} />
        </div>
      </motion.div>
    </div>
  );
}

export default function AdvancedChartPopup() {
  return (
    <Suspense fallback={<div className="fixed inset-0 z-[100] flex items-center justify-center bg-[#050505]/60 backdrop-blur-md"><div className="w-16 h-16 border-4 border-white/10 border-t-white/60 rounded-full animate-spin" /></div>}>
      <AdvancedChartContent />
    </Suspense>
  );
}
